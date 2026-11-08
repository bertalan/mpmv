"""The geocoder chain (EVT.12): cache, per-provider limiter, timeouts, budget, circuit breaker.

Operational state, not domain changes: the cache and the provider state are written without a
ChangeSet or audit (like the CORE.5 throttles). The coordinates a person confirms are stored,
and audited, by the page that holds them (EVT.1).
"""

import datetime as dt
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from apps.core.models import GeocodeCacheEntry, GeocoderState

from . import (
    BUDGET_EXHAUSTED,
    CIRCUIT_OPEN,
    EMPTY,
    FAILED,
    NO_KEY,
    OK,
    RATE_LIMITED,
    THROTTLED,
    TIMEOUT,
    AddressQuery,
    Attempt,
    GeocodeOutcome,
    Geocoder,
    GeocodeResult,
    GeocoderRateLimited,
    GeocoderTimeout,
    engine,
)

logger = logging.getLogger(__name__)

Clock = Callable[[], dt.datetime]
Sleep = Callable[[float], None]


@dataclass(frozen=True)
class ChainPolicy:
    call_timeout: float  # seconds, each provider call
    budget: float  # seconds, the whole chain
    breaker_threshold: int  # consecutive failures that open the circuit
    breaker_cooldown: dt.timedelta
    cache_ttl: dt.timedelta
    min_intervals: Mapping[str, float]  # seconds between two requests to one provider


def chain_policy() -> ChainPolicy:
    return ChainPolicy(
        call_timeout=float(settings.GEOCODING_CALL_TIMEOUT),
        budget=float(settings.GEOCODING_CHAIN_BUDGET),
        breaker_threshold=int(settings.GEOCODING_BREAKER_THRESHOLD),
        breaker_cooldown=dt.timedelta(seconds=settings.GEOCODING_BREAKER_COOLDOWN),
        cache_ttl=dt.timedelta(days=settings.GEOCODING_CACHE_DAYS),
        min_intervals=dict(settings.GEOCODING_MIN_INTERVALS),
    )


def configured_geocoders() -> list[Geocoder]:
    """The chain from `settings.GEOCODING_PROVIDERS`, each built from `GEOCODING_ADAPTERS`."""
    adapters: Mapping[str, str] = settings.GEOCODING_ADAPTERS
    return [import_string(adapters[name])() for name in settings.GEOCODING_PROVIDERS]


# --- Access (the "Find coordinates" endpoint)


def may_geocode(user: AbstractBaseUser | AnonymousUser) -> bool:
    """Staff, or a user one of `settings.GEOCODING_ACCESS_POLICIES` admits (vendor members).

    The policies are dotted paths so that core does not import the vendors app.
    """
    if not user.is_authenticated or not getattr(user, "is_active", False):
        return False
    if getattr(user, "is_staff", False):
        return True
    return any(import_string(path)(user) for path in settings.GEOCODING_ACCESS_POLICIES)


# --- Cache


def _cached(query: AddressQuery, language: str, now: dt.datetime) -> GeocodeOutcome | None:
    entry = GeocodeCacheEntry.objects.filter(
        key=engine.cache_key(query), language=language, expires_at__gt=now
    ).first()
    if entry is None:
        return None
    results = tuple(GeocodeResult.from_dict(item) for item in entry.candidates)
    return GeocodeOutcome(results=results, provider=entry.provider, attempts=(), cached=True)


def _store(
    query: AddressQuery,
    language: str,
    provider: str,
    results: Sequence[GeocodeResult],
    expires_at: dt.datetime,
) -> None:
    GeocodeCacheEntry.objects.update_or_create(
        key=engine.cache_key(query),
        language=language,
        defaults={
            "normalised_address": engine.normalised_address(query),
            "provider": provider,
            "candidates": [result.to_dict() for result in results],
            "expires_at": expires_at,
        },
    )


def purge_expired_geocode_cache(*, now: dt.datetime | None = None) -> int:
    """Delete the cache entries past their expiry; return how many were deleted."""
    deleted, _ = GeocodeCacheEntry.objects.filter(expires_at__lte=now or timezone.now()).delete()
    return deleted


# --- Provider state: limiter and circuit breaker


def _breaker(state: GeocoderState | None) -> engine.BreakerState:
    if state is None:
        return engine.BreakerState()
    return engine.BreakerState(failures=state.failures, open_until=state.open_until)


def _circuit_open(provider: str, now: dt.datetime) -> bool:
    state = GeocoderState.objects.filter(provider=provider).first()
    return engine.breaker_is_open(_breaker(state), now)


def _reserve(provider: str, interval: float, now: dt.datetime, max_wait: float) -> float | None:
    """Reserve the provider's next slot; the seconds to wait, or None if beyond `max_wait`.

    One short transaction per reservation, so the row lock is never held while waiting or
    calling the provider; every process sees the same `next_slot_at`.
    """
    with transaction.atomic():
        state, _ = GeocoderState.objects.select_for_update().get_or_create(provider=provider)
        slot, next_free = engine.reserve_slot(state.next_slot_at, now, interval=interval)
        wait = (slot - now).total_seconds()
        if wait > max_wait:
            return None
        state.next_slot_at = next_free
        state.save(update_fields=["next_slot_at", "updated_at"])
    return wait


def _record_success(provider: str) -> None:
    GeocoderState.objects.filter(provider=provider).exclude(failures=0, open_until=None).update(
        failures=0, open_until=None, updated_at=timezone.now()
    )


def _record_failure(provider: str, now: dt.datetime, policy: ChainPolicy) -> None:
    with transaction.atomic():
        state, _ = GeocoderState.objects.select_for_update().get_or_create(provider=provider)
        after = engine.breaker_after_failure(
            _breaker(state),
            now,
            threshold=policy.breaker_threshold,
            cooldown=policy.breaker_cooldown,
        )
        state.failures = after.failures
        state.open_until = after.open_until
        state.save(update_fields=["failures", "open_until", "updated_at"])


# --- The chain


def geocode_address(
    query: AddressQuery,
    *,
    language: str,
    limit: int = 5,
    geocoders: Sequence[Geocoder] | None = None,
    policy: ChainPolicy | None = None,
    clock: Clock = timezone.now,
    sleep: Sleep = time.sleep,
) -> GeocodeOutcome:
    """Candidates for `query`: from the cache, else from the first provider that has some.

    The chain moves on when a provider has no key, has its circuit open, cannot get a limiter
    slot within the budget, fails, times out, is rate limited or finds nothing, and records
    each attempt. Each call gets the per-call timeout, capped by what is left of the budget.
    """
    language = language.strip().lower()
    policy = policy or chain_policy()
    start = clock()
    cached = _cached(query, language, start)
    if cached is not None:
        return cached
    chain = configured_geocoders() if geocoders is None else geocoders
    attempts: list[Attempt] = []

    def remaining() -> float:
        return policy.budget - (clock() - start).total_seconds()

    for geocoder in chain:
        name = geocoder.name
        if not geocoder.configured:
            attempts.append(Attempt(name, NO_KEY))
            continue
        if remaining() <= 0:
            attempts.append(Attempt(name, BUDGET_EXHAUSTED))
            continue
        if _circuit_open(name, clock()):
            attempts.append(Attempt(name, CIRCUIT_OPEN))
            continue
        wait = _reserve(name, policy.min_intervals.get(name, 0.0), clock(), remaining())
        if wait is None:
            attempts.append(Attempt(name, THROTTLED))
            continue
        if wait > 0:
            sleep(wait)
        timeout = min(policy.call_timeout, remaining())
        if timeout <= 0:
            attempts.append(Attempt(name, BUDGET_EXHAUSTED))
            continue
        status = _call(geocoder, query, language=language, limit=limit, timeout=timeout)
        if isinstance(status, str):
            attempts.append(Attempt(name, status))
            _record_failure(name, clock(), policy)
            continue
        _record_success(name)
        if not status:
            attempts.append(Attempt(name, EMPTY))
            continue
        attempts.append(Attempt(name, OK))
        results = tuple(status[:limit])
        _store(query, language, name, results, clock() + policy.cache_ttl)
        return GeocodeOutcome(results=results, provider=name, attempts=tuple(attempts))
    return GeocodeOutcome(results=(), provider=None, attempts=tuple(attempts))


def _call(
    geocoder: Geocoder, query: AddressQuery, *, language: str, limit: int, timeout: float
) -> list[GeocodeResult] | str:
    """The provider's results, or the attempt status of its failure.

    Only the provider name and the exception class are logged: a message could carry a URL
    with a key, and the address is personal enough to keep out of the logs.
    """
    try:
        return geocoder.geocode(query, language=language, limit=limit, timeout=timeout)
    except GeocoderRateLimited:
        logger.warning("geocoder %s: rate limited", geocoder.name)
        return RATE_LIMITED
    except GeocoderTimeout:
        logger.warning("geocoder %s: timed out", geocoder.name)
        return TIMEOUT
    except Exception as exc:  # any adapter failure moves the chain on
        logger.warning("geocoder %s: failed (%s)", geocoder.name, type(exc).__name__)
        return FAILED
