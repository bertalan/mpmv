"""Session levels (D-32, CORE.25): `restricted` (default) and `operational` (temporary).

State lives in the Django session: when operational mode started, when the session was last
active, and the single-use "do it once" grants. The timers and the action lists come from the
`PlatformOperator` configuration (`core.session_policy`), read per request, so a change in the
configuration takes effect with no code change.

Hooks for later work: CORE.26 (vendor policy) replaces `policy()` with the merge of the platform
policy and the vendor's tightening for shop-space actions; CORE.24 (session lifetimes) owns the
12 h staff session and the "log out everywhere" list, and should call `end_operational` when it
invalidates a session.
"""

import hashlib
import time
from typing import Any, Final

from django.contrib.auth import user_logged_out
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.dispatch import receiver
from django.http import HttpRequest

from apps.core import session_policy
from apps.core.audit import audit_context, record, user_context
from apps.core.models import ELEVATE_CODENAME
from apps.core.services import NoPlatformOperator, current_operator

from . import services
from .models import PasskeyDevice, User

__all__ = ["ELEVATE_CODENAME", "OPERATIONAL", "RESTRICTED"]

RESTRICTED: Final = session_policy.RESTRICTED
OPERATIONAL: Final = session_policy.OPERATIONAL

STARTED_KEY: Final = "level_started_at"
ACTIVITY_KEY: Final = "level_last_activity"
SINGLE_KEY: Final = "level_single_grants"
CHALLENGE_KEY: Final = "level_passkey_challenge"
SINGLE_CHALLENGE_KEY: Final = "level_single_challenge"

SESSION_STARTED: Final = "accounts.session.operational_started"
SESSION_ENDED: Final = "accounts.session.operational_ended"
SINGLE_GRANTED: Final = "accounts.session.single_granted"
SINGLE_USED: Final = "accounts.session.single_used"
PROMPT: Final = "accounts.session.prompt"

# Action keys passed to `requires_level`, filled as views are decorated (the sweep test reads it).
REGISTERED_ACTIONS: set[str] = set()


def policy() -> session_policy.SessionPolicy:
    """The policy of the current `PlatformOperator`; the D-32 defaults before one exists or if the
    stored mapping is invalid (the model validates it, so this only guards old rows)."""
    try:
        raw = current_operator().session_policy
    except NoPlatformOperator:
        return session_policy.DEFAULT_POLICY
    try:
        return session_policy.parse_policy(raw)
    except ValueError:
        return session_policy.DEFAULT_POLICY


def _target(user: AbstractBaseUser | AnonymousUser) -> tuple[str, str]:
    return services.USER_MODEL, str(getattr(user, "public_id", user.pk))


def _record(request: HttpRequest, action: str, after: dict[str, Any]) -> None:
    model, key = _target(request.user)
    record(audit_context(request), action, model, key, None, after)


# --- Who may elevate


def has_passkey(user: AbstractBaseUser | AnonymousUser) -> bool:
    return (
        isinstance(user, User) and PasskeyDevice.objects.filter(user=user, confirmed=True).exists()
    )


def may_elevate(user: AbstractBaseUser | AnonymousUser) -> bool:
    """An active user holding `core.elevate` (directly, by role, or as superuser) with a passkey."""
    return (
        isinstance(user, User)
        and user.is_active
        and user.has_perm(f"core.{ELEVATE_CODENAME}")
        and has_passkey(user)
    )


# --- The operational state


def current_level(request: HttpRequest) -> str:
    """The level of this request's session. Expiry is enforced by `refresh` in the middleware."""
    session = request.session
    return OPERATIONAL if session.get(STARTED_KEY) is not None else RESTRICTED


def seconds_left(request: HttpRequest) -> int | None:
    """Seconds until operational mode ends if nothing else happens; None in restricted mode."""
    started, active = request.session.get(STARTED_KEY), request.session.get(ACTIVITY_KEY)
    if started is None or active is None:
        return None
    pol = policy()
    now = time.time()
    left = min(started + pol.cap_minutes * 60 - now, active + pol.idle_minutes * 60 - now)
    return max(0, int(left))


def start_operational(request: HttpRequest) -> None:
    """Switch the session to operational mode (the caller verified a fresh passkey)."""
    request.session.cycle_key()  # a privilege change: a fixed session id is worthless
    now = time.time()
    request.session[STARTED_KEY] = request.session[ACTIVITY_KEY] = now
    pol = policy()
    _record(
        request,
        SESSION_STARTED,
        {"kind": "session", "idle_minutes": pol.idle_minutes, "cap_minutes": pol.cap_minutes},
    )


def end_operational(request: HttpRequest, reason: str) -> None:
    """Back to restricted: `reason` is idle, cap, manual, logout or invalid."""
    session = request.session
    started = session.pop(STARTED_KEY, None)
    session.pop(ACTIVITY_KEY, None)
    if started is None:
        return
    _record(
        request,
        SESSION_ENDED,
        {"kind": "session", "reason": reason, "seconds": int(time.time() - started)},
    )


def refresh(request: HttpRequest) -> None:
    """Per request: end an expired operational session, else count this request as activity."""
    session = request.session
    if session.get(STARTED_KEY) is None:
        return
    now = time.time()
    why = session_policy.expiry(
        policy(),
        started_at=_number(session.get(STARTED_KEY)),
        last_activity=_number(session.get(ACTIVITY_KEY)),
        now=now,
    )
    if why is not None:
        end_operational(request, why)
    else:
        session[ACTIVITY_KEY] = now


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


@receiver(user_logged_out)
def _end_on_logout(
    sender: object, request: HttpRequest | None, user: Any, **kwargs: object
) -> None:
    if request is None or user is None or not hasattr(request, "session"):
        return
    session = request.session
    started = session.get(STARTED_KEY)
    if started is None:
        return
    model, key = _target(user)
    ctx = user_context(user, audit_context(request).ip)
    record(
        ctx,
        SESSION_ENDED,
        model,
        key,
        None,
        {"kind": "session", "reason": "logout", "seconds": int(time.time() - started)},
    )


# --- "Do it once": single-use grants bound to an action and its parameters


def params_digest(request: HttpRequest) -> str:
    """A digest of what this request would do: its path and its query and form parameters."""
    items = sorted(
        (source, key, value)
        for source, data in (("get", request.GET), ("post", request.POST))
        for key in data
        for value in data.getlist(key)
        if key != "csrfmiddlewaretoken"
    )
    material = "\x1f".join([request.path, *("\x1e".join(item) for item in items)])
    return hashlib.sha256(material.encode()).hexdigest()


def grant_single(request: HttpRequest, action: str, digest: str) -> None:
    ttl = policy().single_seconds
    grants = _live_grants(request)
    grants.append({"action": action, "digest": digest, "expires": time.time() + ttl})
    request.session[SINGLE_KEY] = grants
    _record(request, SINGLE_GRANTED, {"kind": "single", "action": action, "seconds": ttl})


def _live_grants(request: HttpRequest) -> list[dict[str, Any]]:
    now = time.time()
    stored = request.session.get(SINGLE_KEY) or []
    return [g for g in stored if isinstance(g, dict) and g.get("expires", 0) > now]


def consume_single(request: HttpRequest, action: str, digest: str) -> bool:
    """Use the grant for exactly this action and parameters, once."""
    grants = _live_grants(request)
    for grant in grants:
        if grant["action"] == action and grant["digest"] == digest:
            grants.remove(grant)
            request.session[SINGLE_KEY] = grants
            _record(request, SINGLE_USED, {"kind": "single", "action": action})
            return True
    request.session[SINGLE_KEY] = grants
    return False


def record_prompt(request: HttpRequest, action: str, group: int, outcome: str) -> None:
    """Audit that a user was asked to elevate or confirm (the friction report counts these)."""
    _record(request, PROMPT, {"action": action, "group": group, "outcome": outcome})
