"""The passkey window (D-32, CORE.25): one fresh passkey for several sensitive actions.

A sensitive action is always sensitive; the window grants no extra power. The normal state (no
window open) has no name. State lives in the Django session: when the passkey window opened, when
the session was last active, and the single-use "do it once" grants. The timers and the action
lists come from the `PlatformOperator` configuration (`core.session_policy`), read per request, so
a change in the configuration takes effect with no code change.

A vendor owner holds no `elevate` permission (only platform roles do), yet the shop space gates
some of its own actions (D-32 amendment, owner 2026-11-14). For the actions in
`SHOP_OWNER_ACTIONS`, and only on a shop they own, a vendor owner with a passkey may "Do it once"
and may open a passkey window scoped to that one shop: it covers only those actions of that shop,
never a platform action and never another shop, and ownership is re-checked on every covered
action. The shop is the view's (`requires_level(..., shop=...)`); ownership comes from
`settings.SHOP_OWNER_CHECK`, since accounts never imports vendors.

Hooks for later work: CORE.26 (vendor policy) replaces `policy()` with the merge of the platform
policy and the vendor's tightening for shop-space actions; CORE.24 (session lifetimes) owns the
12 h staff session and the "log out everywhere" list, and should call `close_window` when it
invalidates a session.
"""

import datetime as dt
import hashlib
import json
import logging
import time
from collections.abc import Mapping
from typing import Any, Final

from django.conf import settings
from django.contrib.auth import user_logged_in, user_logged_out
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db import IntegrityError, transaction
from django.dispatch import receiver
from django.http import HttpRequest
from django.utils import timezone
from django.utils.module_loading import import_string

from apps.core import session_policy
from apps.core.audit import audit_context, record, user_context
from apps.core.models import ELEVATE_CODENAME
from apps.core.services import NoPlatformOperator, current_operator

from . import services
from .models import ElevationGrant, PasskeyDevice, User

logger = logging.getLogger(__name__)

__all__ = ["ELEVATE_CODENAME", "NO_WINDOW", "PASSKEY_WINDOW"]

NO_WINDOW: Final = session_policy.NO_WINDOW
PASSKEY_WINDOW: Final = session_policy.PASSKEY_WINDOW

STARTED_KEY: Final = "level_started_at"
ACTIVITY_KEY: Final = "level_last_activity"
CHALLENGE_TTL: Final = 120  # seconds a passkey challenge for a level ceremony stays valid
CHALLENGE_KEY: Final = "level_passkey_challenge"
SINGLE_CHALLENGE_KEY: Final = "level_single_challenge"
WINDOW_SHOP_KEY: Final = "level_window_shop"  # the one shop a vendor owner's window covers
SHOP_OFFERS_KEY: Final = "level_shop_offers"  # prompts offered to a vendor owner, newest last
MAX_SHOP_OFFERS: Final = 8

# Shop-space actions a vendor owner may confirm for their own shop (D-32 amendment 2026-11-14).
SHOP_OWNER_ACTIONS: Final = frozenset({"payout_details", "sales_terms_publish"})

SESSION_STARTED: Final = "accounts.session.window_started"
SESSION_ENDED: Final = "accounts.session.window_ended"
# Rows written before the passkey window wording keep these action strings; readers accept both.
LEGACY_SESSION_STARTED: Final = "accounts.session.operational_started"
LEGACY_SESSION_ENDED: Final = "accounts.session.operational_ended"
SESSION_STARTED_ACTIONS: Final = (SESSION_STARTED, LEGACY_SESSION_STARTED)
SESSION_ENDED_ACTIONS: Final = (SESSION_ENDED, LEGACY_SESSION_ENDED)
SINGLE_GRANTED: Final = "accounts.session.single_granted"
SINGLE_USED: Final = "accounts.session.single_used"
PROMPT: Final = "accounts.session.prompt"

# Action keys passed to `requires_level`, filled as views are decorated (the sweep test reads it).
REGISTERED_ACTIONS: set[str] = set()


def policy() -> session_policy.SessionPolicy:
    """The policy of the current `PlatformOperator`; the D-32 defaults before one exists.

    A stored mapping that no longer parses (the model validates it, so this is old or hand-made
    data) fails CLOSED: every action is critical until it is fixed, and the error is logged.
    """
    try:
        raw = current_operator().session_policy
    except NoPlatformOperator:
        return session_policy.DEFAULT_POLICY
    try:
        return session_policy.parse_policy(raw)
    except ValueError:
        logger.error("The stored session policy is invalid: failing closed (all critical).")
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


def shop_key(shop: object) -> str:
    """The scope a shop gives a grant or a window: its model label and primary key."""
    meta = getattr(shop, "_meta", None)
    label = meta.label_lower if meta is not None else type(shop).__name__.lower()
    return f"{label}:{getattr(shop, 'pk', None)}"


def is_shop_owner(user: AbstractBaseUser | AnonymousUser, shop: object) -> bool:
    """Whether `user` owns `shop` now, through `settings.SHOP_OWNER_CHECK` (read on each call).

    Fails closed: a check that cannot be imported or that raises means "not an owner" (logged),
    never a server error.
    """
    try:
        check = import_string(settings.SHOP_OWNER_CHECK)
        return bool(check(user, shop))
    except Exception:
        logger.exception("SHOP_OWNER_CHECK failed: treated as not an owner")
        return False


def may_confirm(user: AbstractBaseUser | AnonymousUser, action: str, shop: object | None) -> bool:
    """Whether `user` may confirm `action` with a passkey here, once or with a window.

    Staff with `core.elevate` may, as before. A vendor owner may only for an action of
    `SHOP_OWNER_ACTIONS` on `shop`, a shop they own (re-checked on each call), with a passkey.
    """
    if may_elevate(user):
        return True
    return (
        shop is not None
        and action in SHOP_OWNER_ACTIONS
        and isinstance(user, User)
        and user.is_active
        and has_passkey(user)
        and is_shop_owner(user, shop)
    )


def offer_shop_confirmation(request: HttpRequest, action: str, digest: str, shop: object) -> None:
    """Remember that the prompt offered a vendor owner this action on this shop: the passkey
    endpoints accept a vendor owner only for an offer the server made."""
    offers = [
        offer
        for offer in request.session.get(SHOP_OFFERS_KEY, [])
        if isinstance(offer, Mapping) and offer.get("digest") != digest
    ]
    offers.append({"action": action, "digest": digest, "shop": shop_key(shop)})
    request.session[SHOP_OFFERS_KEY] = offers[-MAX_SHOP_OFFERS:]


def shop_offer(request: HttpRequest, action: str | None = None, digest: str | None = None) -> str:
    """The shop of the newest offer to this vendor owner matching `action` and `digest` (any
    offer when both are None), or "". Never for a deactivated user or one without a passkey."""
    user = request.user
    if not (isinstance(user, User) and user.is_active and has_passkey(user)):
        return ""
    for offer in reversed(request.session.get(SHOP_OFFERS_KEY, [])):
        if not isinstance(offer, Mapping) or offer.get("action") not in SHOP_OWNER_ACTIONS:
            continue
        if action is None or (offer.get("action") == action and offer.get("digest") == digest):
            shop = offer.get("shop")
            return shop if isinstance(shop, str) else ""
    return ""


def drop_shop_offers(
    request: HttpRequest, *, shop: str, action: str | None = None, digest: str | None = None
) -> None:
    """Forget the offers spent by a confirmation: every offer on `shop` once a window opens for
    it, or the one offer of `action` and `digest` once it is done once. A new confirmation needs
    a new prompt, so a stale offer never outlives a lost ownership."""
    kept = [
        offer
        for offer in request.session.get(SHOP_OFFERS_KEY, [])
        if isinstance(offer, Mapping)
        and not (
            offer.get("shop") == shop
            and (
                action is None or (offer.get("action") == action and offer.get("digest") == digest)
            )
        )
    ]
    if kept:
        request.session[SHOP_OFFERS_KEY] = kept
    else:
        request.session.pop(SHOP_OFFERS_KEY, None)


# --- The passkey window


def current_level(request: HttpRequest) -> str:
    """The level of this request's session. Expiry is enforced by `refresh` in the middleware."""
    session = request.session
    return PASSKEY_WINDOW if session.get(STARTED_KEY) is not None else NO_WINDOW


def seconds_left(request: HttpRequest) -> int | None:
    """Seconds until the passkey window closes if nothing else happens; None with no window."""
    started, active = request.session.get(STARTED_KEY), request.session.get(ACTIVITY_KEY)
    if started is None or active is None:
        return None
    pol = policy()
    now = time.time()
    left = min(started + pol.cap_minutes * 60 - now, active + pol.idle_minutes * 60 - now)
    return max(0, int(left))


def open_window(request: HttpRequest, *, shop: str | None = None) -> None:
    """Open the passkey window (the caller verified a fresh passkey).

    `shop` (a `shop_key`) scopes a vendor owner's window to that shop's owner actions; a window
    for another scope starts anew rather than widening the old one.
    """
    request.session.cycle_key()  # a privilege change: a fixed session id is worthless
    now = time.time()
    session = request.session
    # A fresh passkey does not reset the cap, unless the scope changes.
    renewed = session.get(STARTED_KEY) is not None and session.get(WINDOW_SHOP_KEY) == shop
    if not renewed:
        session[STARTED_KEY] = now
    if shop is None:
        session.pop(WINDOW_SHOP_KEY, None)
    else:
        session[WINDOW_SHOP_KEY] = shop
    session[ACTIVITY_KEY] = now
    pol = policy()
    after: dict[str, Any] = {
        "kind": "session",
        "idle_minutes": pol.idle_minutes,
        "cap_minutes": pol.cap_minutes,
        "renewed": renewed,
    }
    if shop is not None:
        after["shop"] = shop
    _record(request, SESSION_STARTED, after)


def window_covers(request: HttpRequest, action: str, shop: object | None) -> bool:
    """Whether an open window covers `action` here. A window with no shop scope (opened with
    `elevate`) covers every action only while its user may still elevate (re-checked now: staff
    who lose `elevate`, or their passkeys, keep no step-up); a vendor owner's window covers only
    the owner actions of its own shop, with ownership re-checked now."""
    if current_level(request) != PASSKEY_WINDOW:
        return False
    scope = request.session.get(WINDOW_SHOP_KEY)
    if scope is None:
        return may_elevate(request.user)
    return (
        shop is not None
        and shop_key(shop) == scope
        and action in SHOP_OWNER_ACTIONS
        and may_confirm(request.user, action, shop)
    )


def close_window(request: HttpRequest, reason: str) -> None:
    """Close the passkey window: `reason` is idle, cap, manual, logout or invalid."""
    session = request.session
    started = session.pop(STARTED_KEY, None)
    session.pop(ACTIVITY_KEY, None)
    session.pop(WINDOW_SHOP_KEY, None)
    session.pop(SHOP_OFFERS_KEY, None)
    if started is None:
        return
    _record(
        request,
        SESSION_ENDED,
        {"kind": "session", "reason": reason, "seconds": int(time.time() - started)},
    )


def refresh(request: HttpRequest) -> None:
    """Per request: close an expired passkey window, else count this request as activity."""
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
        close_window(request, why)
    elif not _is_background(request):
        session[ACTIVITY_KEY] = now


def _is_background(request: HttpRequest) -> bool:
    """Polling and prefetch are not the user working: they do not keep the window open."""
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
    """Signing in again in the same session starts with no window (Django keeps the data of the
    same user's session)."""
    if request is None or not hasattr(request, "session"):
        return
    started = request.session.pop(STARTED_KEY, None)
    request.session.pop(ACTIVITY_KEY, None)
    request.session.pop(WINDOW_SHOP_KEY, None)
    request.session.pop(SHOP_OFFERS_KEY, None)
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


def params_digest(request: HttpRequest, *, shop: object | None = None) -> str:
    """A digest of what this request does: method, path, sorted query, the body, and the shop
    the view acts on when it names one (a grant for one shop never fits another).

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
    parts: list[Any] = [request.method, request.path, query, body]
    if shop is not None:
        parts.append({"shop": shop_key(shop)})
    material = json.dumps(
        parts,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _session_hash(session_key: str) -> str:
    return hashlib.sha256(session_key.encode()).hexdigest()


def create_grant(session_key: str, user: Any, action: str, digest: str, *, ttl: int) -> None:
    """Mint a grant; at most one unspent grant exists per session, user, action and parameters
    (a unique constraint), so a parallel replay of one assertion mints no second one."""
    now = timezone.now()
    session_hash = _session_hash(session_key)
    ElevationGrant.objects.filter(expires_at__lt=now - dt.timedelta(days=1)).delete()
    ElevationGrant.objects.filter(
        session_hash=session_hash,
        user=user,
        action=action,
        digest=digest,
        used_at__isnull=True,
        expires_at__lte=now,
    ).delete()  # an expired unspent grant must not block a new one
    try:
        with transaction.atomic():
            ElevationGrant.objects.create(
                session_hash=session_hash,
                user=user,
                action=action,
                digest=digest,
                expires_at=now + dt.timedelta(seconds=ttl),
            )
    except IntegrityError:
        pass  # a live grant for exactly this already exists


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


def grant_single(request: HttpRequest, action: str, digest: str, *, shop: str = "") -> None:
    if not request.session.session_key:
        request.session.create()
    ttl = policy().single_seconds
    assert request.session.session_key  # noqa: S101  # created above
    create_grant(request.session.session_key, request.user, action, digest, ttl=ttl)
    after: dict[str, Any] = {"kind": "single", "action": action, "seconds": ttl}
    if shop:
        after["shop"] = shop
    _record(request, SINGLE_GRANTED, after)


def consume_single(request: HttpRequest, action: str, digest: str, *, shop: str = "") -> bool:
    """Use the grant for exactly this action and parameters, once; `shop` (a `shop_key`) names
    the shop it acted on in the audit."""
    key = request.session.session_key
    if not key or not spend_grant(key, request.user, action, digest):
        return False
    after: dict[str, Any] = {"kind": "single", "action": action}
    if shop:
        after["shop"] = shop
    _record(request, SINGLE_USED, after)
    return True


def record_prompt(
    request: HttpRequest, action: str, group: int, outcome: str, *, shop: str = ""
) -> None:
    """Audit that a user was asked for a passkey or a confirmation (the friction report counts);
    `shop` (a `shop_key`) names the shop the action was on."""
    after: dict[str, Any] = {"action": action, "group": group, "outcome": outcome}
    if shop:
        after["shop"] = shop
    _record(request, PROMPT, after)
