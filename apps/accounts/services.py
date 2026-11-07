"""Account services: business logic shared by views, API and commands."""

import datetime as dt

from django.utils import timezone

from apps.core.audit import AuditContext, record
from apps.core.changeset import ChangeSet
from apps.core.i18n import configured_languages
from apps.core.models import AuditLog
from apps.core.services import clear_throttle, record_throttle_failure, throttle_state
from apps.core.throttling import BackoffPolicy, ThrottleState, ip_bucket

from .models import User, normalize_email_address

# --- Login and registration protection (CORE.5, spec 02 "Authentication")
#
# Lockouts are keyed on what the client submitted, whether or not an account exists, so the
# behaviour (and the generic error) is the same for real and unknown emails: no enumeration.

ACCOUNT_LOCKOUT = BackoffPolicy(
    free_attempts=5,
    first_lock=dt.timedelta(minutes=1),
    max_lock=dt.timedelta(hours=1),
    forget_after=dt.timedelta(hours=24),
)
# Offices and mobile carriers put many people behind one address: more room before a lock.
IP_LOCKOUT = BackoffPolicy(
    free_attempts=20,
    first_lock=dt.timedelta(minutes=1),
    max_lock=dt.timedelta(hours=1),
    forget_after=dt.timedelta(hours=24),
)
# Every registration POST counts, successful or not.
REGISTRATION_LIMIT = BackoffPolicy(
    free_attempts=5,
    first_lock=dt.timedelta(minutes=15),
    max_lock=dt.timedelta(hours=24),
    forget_after=dt.timedelta(hours=24),
)

LOCKED_OUT = "accounts.login.locked_out"
REGISTRATION_RATE_LIMITED = "accounts.registration.rate_limited"
THROTTLE_MODEL = "core.throttle"
_EMAIL_MAX = 254


def _account_key(identifier: str | None) -> str | None:
    if identifier is None:
        return None
    normalized = normalize_email_address(identifier)[:_EMAIL_MAX]
    return f"login:account:{normalized}" if normalized else None


def _ip_key(scope: str, ip: str | None) -> str | None:
    bucket = ip_bucket(ip)
    return f"{scope}:ip:{bucket}" if bucket else None


def login_throttle_keys(identifier: str | None, ip: str | None) -> dict[str, BackoffPolicy]:
    """The throttle keys a login attempt counts against, with their policies."""
    keys: dict[str, BackoffPolicy] = {}
    if account := _account_key(identifier):
        keys[account] = ACCOUNT_LOCKOUT
    if address := _ip_key("login", ip):
        keys[address] = IP_LOCKOUT
    return keys


def _retry_after(keys: list[str], now: dt.datetime) -> dt.timedelta:
    return max(
        (throttle_state(key, now=now).retry_after(now) for key in keys),
        default=dt.timedelta(0),
    )


def _audit_new_lock(
    key: str,
    before: ThrottleState,
    after: ThrottleState,
    *,
    action: str,
    ip: str | None,
    now: dt.datetime,
) -> None:
    if before.is_locked(now) or not after.is_locked(now):
        return
    ctx = AuditContext(actor_label="anonymous", source=AuditLog.Source.SYSTEM, ip=ip)
    snapshot = {"failures": before.failures, "locked_until": before.locked_until}
    record(
        ctx,
        action,
        THROTTLE_MODEL,
        key[:200],
        snapshot,
        {"failures": after.failures, "locked_until": after.locked_until},
    )


def login_retry_after(
    identifier: str | None, ip: str | None, *, now: dt.datetime | None = None
) -> dt.timedelta:
    """How long until this account and IP may try again; zero when login is allowed."""
    return _retry_after(list(login_throttle_keys(identifier, ip)), now or timezone.now())


def record_failed_login(
    identifier: str | None, ip: str | None, *, now: dt.datetime | None = None
) -> None:
    """Count a failed login against the account and the IP; audit every lock that starts.

    Attempts made while a lock is running are refused before any password check, so they
    carry no information and are not counted: a lock never extends itself.
    """
    now = now or timezone.now()
    keys = login_throttle_keys(identifier, ip)
    if _retry_after(list(keys), now):
        return
    for key, policy in keys.items():
        before, after = record_throttle_failure(key, policy, now=now)
        _audit_new_lock(key, before, after, action=LOCKED_OUT, ip=ip, now=now)


def record_successful_login(identifier: str | None) -> None:
    """Reset the account counter. The IP counter only decays: resetting it would let one
    valid account launder an IP that keeps guessing other accounts' passwords."""
    if account := _account_key(identifier):
        clear_throttle(account)


def registration_retry_after(ip: str | None, *, now: dt.datetime | None = None) -> dt.timedelta:
    key = _ip_key("register", ip)
    return _retry_after([key] if key else [], now or timezone.now())


def record_registration_attempt(ip: str | None, *, now: dt.datetime | None = None) -> None:
    key = _ip_key("register", ip)
    if key is None:
        return
    now = now or timezone.now()
    before, after = record_throttle_failure(key, REGISTRATION_LIMIT, now=now)
    _audit_new_lock(key, before, after, action=REGISTRATION_RATE_LIMITED, ip=ip, now=now)


def set_preferred_language(user: User, language: str, *, dry_run: bool = False) -> ChangeSet:
    """Remember the user's language for emails and documents (spec 09 §9.2).

    Not a sensitive mutation, so no AuditLog entry.
    """
    if language not in configured_languages():
        raise ValueError(f"Language {language!r} is not configured in settings.LANGUAGES")
    changeset = ChangeSet()
    changeset.update(
        "accounts.user",
        str(user.public_id),
        {"preferred_language": user.preferred_language},
        {"preferred_language": language},
    )
    if not dry_run and not changeset.is_empty:
        user.preferred_language = language
        user.save(update_fields=["preferred_language", "updated_at"])
    return changeset
