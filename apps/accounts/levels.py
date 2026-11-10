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

import datetime as dt
import hashlib
import json
import logging
import time
from typing import Any, Final

from django.conf import settings
from django.contrib.auth import user_logged_in, user_logged_out
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.dispatch import receiver
from django.http import HttpRequest
from django.utils import timezone

from apps.core import session_policy
from apps.core.audit import audit_context, record, user_context
from apps.core.models import ELEVATE_CODENAME
from apps.core.services import NoPlatformOperator, current_operator

from . import services
from .models import ElevationGrant, PasskeyDevice, User

logger = logging.getLogger(__name__)

__all__ = ["ELEVATE_CODENAME", "OPERATIONAL", "RESTRICTED"]

RESTRICTED: Final = session_policy.RESTRICTED
OPERATIONAL: Final = session_policy.OPERATIONAL

STARTED_KEY: Final = "level_started_at"
ACTIVITY_KEY: Final = "level_last_activity"
CHALLENGE_TTL: Final = 120  # seconds a passkey challenge for a level ceremony stays valid
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
    """The policy of the current `PlatformOperator`; the D-32 defaults before one exists.

    A stored mapping that no longer parses (the model validates it, so this is old or hand-made
    data) fails CLOSED: every action is group 3 until it is fixed, and the error is logged.
    """
    try:
        raw = current_operator().session_policy
    except NoPlatformOperator:
        return session_policy.DEFAULT_POLICY
    try:
        return session_policy.parse_policy(raw)
    except ValueError:
        logger.error("The stored session policy is invalid: failing closed (all group 3).")
        return session_policy.FAIL_CLOSED_POLICY


def stored_policy_error() -> str:
    """Why the current stored policy is invalid, or "" (for the admin warning)."""
    try:
        session_policy.parse_policy(current_operator().session_policy)
    except NoPlatformOperator:
        return ""
    except ValueError as exc:
        return str(exc)
    return ""


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
    renewed = request.session.get(STARTED_KEY) is not None  # a fresh passkey does not reset the cap
    if not renewed:
        request.session[STARTED_KEY] = now
    request.session[ACTIVITY_KEY] = now
    pol = policy()
    _record(
        request,
        SESSION_STARTED,
        {
            "kind": "session",
            "idle_minutes": pol.idle_minutes,
            "cap_minutes": pol.cap_minutes,
            "renewed": renewed,
        },
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
    elif not _is_background(request):
        session[ACTIVITY_KEY] = now


def _is_background(request: HttpRequest) -> bool:
    """Polling and prefetch are not the user working: they do not keep the mode alive."""
    if request.headers.get("X-MPMV-Poll") == "1" or "prefetch" in request.headers.get(
        "Sec-Purpose", ""
    ):
        return True
    prefixes: tuple[str, ...] = tuple(getattr(settings, "SESSION_LEVEL_POLL_PATHS", ()))
    return bool(prefixes) and request.path.startswith(prefixes)


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


@receiver(user_logged_in)
def _end_on_login(sender: object, request: HttpRequest | None, user: Any, **kwargs: object) -> None:
    """Signing in again in the same session starts restricted (Django keeps the data of the same
    user's session)."""
    if request is None or not hasattr(request, "session"):
        return
    started = request.session.pop(STARTED_KEY, None)
    request.session.pop(ACTIVITY_KEY, None)
    if started is None or user is None:
        return
    model, key = _target(user)
    record(
        user_context(user, None),
        SESSION_ENDED,
        model,
        key,
        None,
        {"kind": "session", "reason": "relogin", "seconds": int(time.time() - started)},
    )


# --- "Do it once": single-use grants bound to an action and its parameters


def _file_digest(upload: Any) -> str:
    sha = hashlib.sha256()
    for chunk in upload.chunks():
        sha.update(chunk)
    return sha.hexdigest()


def params_digest(request: HttpRequest) -> str:
    """A digest of what this request does: method, path, sorted query, and the body.

    A form body is its sorted fields (the CSRF token excluded) and the name and content hash of
    each uploaded file; any other body is hashed as JSON in canonical form when it parses, else
    as raw bytes. The material is JSON, so no field value can pose as a separator.
    """
    content_type = (request.content_type or "").lower()
    query = sorted((key, value) for key in request.GET for value in request.GET.getlist(key))
    body: Any
    if content_type in {"application/x-www-form-urlencoded", "multipart/form-data"}:
        fields = sorted(
            (key, value)
            for key in request.POST
            if key != "csrfmiddlewaretoken"
            for value in request.POST.getlist(key)
        )
        files = sorted(
            (key, upload.name, upload.size, _file_digest(upload))
            for key in request.FILES
            for upload in request.FILES.getlist(key)
        )
        body = {"form": fields, "files": files}
    else:
        raw = request.body
        try:
            body = {"json": json.loads(raw)} if raw else {"empty": True}
        except ValueError:
            body = {"raw": hashlib.sha256(raw).hexdigest()}
    material = json.dumps(
        [request.method, request.path, query, body],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _session_hash(session_key: str) -> str:
    return hashlib.sha256(session_key.encode()).hexdigest()


def create_grant(session_key: str, user: Any, action: str, digest: str, *, ttl: int) -> None:
    now = timezone.now()
    ElevationGrant.objects.filter(expires_at__lt=now - dt.timedelta(days=1)).delete()
    ElevationGrant.objects.create(
        session_hash=_session_hash(session_key),
        user=user,
        action=action,
        digest=digest,
        expires_at=now + dt.timedelta(seconds=ttl),
    )


def spend_grant(session_key: str, user: Any, action: str, digest: str) -> bool:
    """Spend the grant for exactly this session, user, action and parameters.

    One conditional UPDATE: the database lets only one of any number of parallel callers change
    `used_at` from NULL, so a grant is never spent twice.
    """
    now = timezone.now()
    spent = ElevationGrant.objects.filter(
        pk__in=ElevationGrant.objects.filter(
            session_hash=_session_hash(session_key),
            user=user,
            action=action,
            digest=digest,
            used_at__isnull=True,
            expires_at__gt=now,
        ).values("pk")[:1],
        used_at__isnull=True,
    ).update(used_at=now)
    return spent == 1


def grant_single(request: HttpRequest, action: str, digest: str) -> None:
    if not request.session.session_key:
        request.session.create()
    ttl = policy().single_seconds
    assert request.session.session_key  # noqa: S101  # created above
    create_grant(request.session.session_key, request.user, action, digest, ttl=ttl)
    _record(request, SINGLE_GRANTED, {"kind": "single", "action": action, "seconds": ttl})


def consume_single(request: HttpRequest, action: str, digest: str) -> bool:
    """Use the grant for exactly this action and parameters, once."""
    key = request.session.session_key
    if not key or not spend_grant(key, request.user, action, digest):
        return False
    _record(request, SINGLE_USED, {"kind": "single", "action": action})
    return True


def record_prompt(request: HttpRequest, action: str, group: int, outcome: str) -> None:
    """Audit that a user was asked to elevate or confirm (the friction report counts these)."""
    _record(request, PROMPT, {"action": action, "group": group, "outcome": outcome})
