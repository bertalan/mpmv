"""Role-based session lifetimes (CORE.24 part 2, spec 02 §Session lifetimes and control).

- A customer's session is rolling: it ends `customer_days` after its last use.
- A staff session ends `staff_idle_minutes` after its last use and `staff_absolute_hours` after
  its sign-in, whatever its activity. "Staff" is decided on every request, never cached at
  sign-in (`is_strict`): staff and superusers, every role `services.mfa_required` names (vendor
  members, editors of the CMS) and each `settings.SESSION_STAFF_CHECKS` entry (platform staff,
  who need not hold the staff flag). A customer who becomes staff gets the staff limits on the
  next request.

The values come from the `PlatformOperator` configuration (`core.session_policy`, read per
request through `levels.policy`), so a new configuration version changes them with no code
change. The defaults live in `core.session_policy` only.

Two stamps live in the session data, which is stored server-side (the client never sees them):
the sign-in time, set on `user_logged_in` and copied with the data when the key cycles (the
passkey window cycles it), and the last use, refreshed at most every `SESSION_ACTIVITY_SECONDS`
by a request that is not polling or prefetch. Each refresh also moves the stored expiry to the
session's deadline plus `SESSION_EXPIRY_GRACE_SECONDS`, so the database row and the cookie
outlive the deadline only by the grace: within it the next request is ended here (audited, with a
message); after it the row is gone and the request is simply anonymous. A second-factor check
or the passkey window does not sign in, so neither restarts the count; a password sign-in does.

An expired session is ended server-side (`end_expired_session`): the passkey window is closed,
the session is deleted with its index row, and one audit entry without personal data names the
reason. The user is then sent to the login page with a message (JSON clients get 401).
"""

import datetime as dt
import logging
import math
import time
from typing import Any, Final

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.views import redirect_to_login
from django.contrib.messages.storage import default_storage
from django.contrib.sessions.backends.base import SessionBase
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import resolve_url
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from apps.core import session_policy
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog
from apps.core.services import run_mutation

from . import levels, services
from .http import return_url, wants_json
from .models import User, UserSession

logger = logging.getLogger(__name__)

SIGNED_IN_KEY: Final = "session_signed_in_at"  # epoch seconds of the sign-in; never moved
SEEN_KEY: Final = "session_last_seen"  # epoch seconds of the last use (within the cadence)

LIFETIME_ATTR: Final = "_mpmv_session_lifetime"  # (user pk, Lifetime) for this request

SESSION_EXPIRED: Final = "accounts.session.expired"
# The system ends the session, not the user: no actor, no IP, no personal data in the entry.
LIFETIME_CTX: Final = AuditContext(
    actor_label="system:session_lifetime", source=AuditLog.Source.SYSTEM
)

IDLE_MESSAGE: Final = _(
    "Your session ended because it was not used for a while. Please sign in again."
)
ABSOLUTE_MESSAGE: Final = _("Your session reached its maximum length. Please sign in again.")
INVALID_MESSAGE: Final = _("Your session ended. Please sign in again.")
_MESSAGES: Final = {"idle": IDLE_MESSAGE, "absolute": ABSOLUTE_MESSAGE}


def grace() -> dt.timedelta:
    """How long the stored session outlives its deadline, so its end can still be explained."""
    return dt.timedelta(seconds=settings.SESSION_EXPIRY_GRACE_SECONDS)


# --- Who gets which lifetime


def is_strict(user: AbstractBaseUser | AnonymousUser) -> bool:
    """Whether `user` gets the staff lifetime now. A check that fails counts as staff (fails
    closed: the shorter lifetime), and is logged."""
    if not user.is_authenticated:
        return False
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    try:
        if services.mfa_required(user):
            return True
        return any(import_string(path)(user) for path in settings.SESSION_STAFF_CHECKS)
    except Exception:
        logger.exception("A session staff check failed: the staff lifetime applies")
        return True


def lifetime_for(
    user: AbstractBaseUser | AnonymousUser, request: HttpRequest | None = None
) -> session_policy.Lifetime:
    return session_policy.lifetime_of(levels.policy(request), strict=is_strict(user))


def lifetime_for_request(request: HttpRequest) -> session_policy.Lifetime:
    """The lifetime of this request's user, decided once per request (the staff checks and the
    policy cost a few queries) and kept on the request for that user only."""
    user = request.user
    cached = getattr(request, LIFETIME_ATTR, None)
    if isinstance(cached, tuple) and cached[0] == user.pk:
        lifetime: session_policy.Lifetime = cached[1]
        return lifetime
    lifetime = lifetime_for(user, request)
    setattr(request, LIFETIME_ATTR, (user.pk, lifetime))
    return lifetime


# --- The stamps


def _stamp(value: object) -> float | None:
    """A stored stamp as a finite number, else None (refused as `invalid`): NaN compares false
    to everything, so a NaN stamp would never expire."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _store_expiry(
    session: SessionBase, lifetime: session_policy.Lifetime, signed_in: float, seen: float
) -> None:
    """Keep the stored row and the cookie until the deadline plus the grace (a fixed date, so a
    later save of the session for another reason does not move it)."""
    deadline = session_policy.lifetime_deadline(lifetime, signed_in_at=signed_in, last_seen=seen)
    session.set_expiry(dt.datetime.fromtimestamp(deadline, tz=dt.UTC) + grace())


def stamp_sign_in(
    session: SessionBase, user: AbstractBaseUser, *, now: float | None = None
) -> None:
    """A sign-in starts the count: both stamps now, the stored expiry from the lifetime."""
    at = time.time() if now is None else now
    session[SIGNED_IN_KEY] = session[SEEN_KEY] = at
    _store_expiry(session, lifetime_for(user), at, at)


def _sign_in_of_unstamped(session: SessionBase, now: float) -> float:
    """A session from before the lifetimes: its index row's creation, else now."""
    created = (
        UserSession.objects.filter(session_key=session.session_key)
        .values_list("created_at", flat=True)
        .first()
    )
    return min(created.timestamp(), now) if created is not None else now


def stamps(session: SessionBase) -> tuple[float | None, float | None]:
    return _stamp(session.get(SIGNED_IN_KEY)), _stamp(session.get(SEEN_KEY))


def seconds_left(request: HttpRequest) -> int | None:
    """Seconds until this request's session ends if it is not used again; None if unknown."""
    signed_in, seen = stamps(request.session)
    if signed_in is None or seen is None:
        return None
    lifetime = lifetime_for_request(request)
    deadline = session_policy.lifetime_deadline(lifetime, signed_in_at=signed_in, last_seen=seen)
    return max(0, int(deadline - time.time()))


# --- Per request


def enforce(request: HttpRequest) -> HttpResponse | None:
    """End this request's session if its lifetime is over (the response to send), else count
    the request as use (unless it is polling or prefetch) and return None.

    Runs right after the session generation check, before the MFA, passkey window and Manager
    middlewares, so an ended session reaches none of them.
    """
    session, user = request.session, request.user
    if not isinstance(user, User):
        return None
    now = time.time()
    lifetime = lifetime_for_request(request)
    unstamped = SIGNED_IN_KEY not in session and SEEN_KEY not in session
    if unstamped:
        signed_in: float | None = _sign_in_of_unstamped(session, now)
        seen: float | None = now
        session[SIGNED_IN_KEY] = signed_in
    else:
        signed_in, seen = stamps(session)
    reason = session_policy.lifetime_expiry(
        lifetime, signed_in_at=signed_in, last_seen=seen, now=now
    )
    if reason is not None:
        return _end(request, user, reason, lifetime.kind)
    if signed_in is None or seen is None:  # unreachable: `lifetime_expiry` refused them
        return _end(request, user, "invalid", lifetime.kind)
    stale = now - seen >= settings.SESSION_ACTIVITY_SECONDS
    if unstamped or (stale and not levels.is_background(request)):
        session[SEEN_KEY] = now
        _store_expiry(session, lifetime, signed_in, now)
    return None


def _end(request: HttpRequest, user: User, reason: str, kind: str) -> HttpResponse:
    if levels.current_level(request) == levels.PASSKEY_WINDOW:
        levels.close_window(request, "expired")
    end_expired_session(user, request.session, reason=reason, lifetime=kind, ctx=LIFETIME_CTX)
    request.user = AnonymousUser()
    login_url = resolve_url(settings.LOGIN_URL)
    if wants_json(request):
        return JsonResponse({"error": "session_expired", "url": login_url}, status=401)
    storage = default_storage(request)
    storage.add(messages.INFO, str(_MESSAGES.get(reason, INVALID_MESSAGE)))
    response = redirect_to_login(return_url(request), login_url)
    storage.update(response)
    return response


def end_expired_session(
    user: User,
    session: SessionBase,
    *,
    reason: str,
    lifetime: str,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """End `session` of `user` because its lifetime is over: delete it with its index row
    (`session_store.SessionStore.delete`) and write one audit entry with the reason and the
    lifetime kind. The entry names the user by public id only: no IP, no device, no key."""
    changeset = ChangeSet()
    after: dict[str, Any] = {"reason": reason, "lifetime": lifetime}
    changeset.update(services.USER_MODEL, str(user.public_id), {}, after)
    return run_mutation(changeset, session.flush, ctx=ctx, action=SESSION_EXPIRED, dry_run=dry_run)


def stamp_on_login(
    sender: object, request: HttpRequest | None, user: AbstractBaseUser, **kwargs: object
) -> None:
    """`user_logged_in` receiver (connected in `AccountsConfig.ready`)."""
    session = getattr(request, "session", None)
    if session is not None and isinstance(user, User):
        stamp_sign_in(session, user)
