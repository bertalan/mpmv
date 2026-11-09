"""Account services: business logic shared by views, API and commands."""

import datetime as dt
import hmac
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlencode

import django_otp
from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Model
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.module_loading import import_string
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.core.audit import AuditContext, record
from apps.core.changeset import ChangeSet
from apps.core.i18n import configured_languages
from apps.core.models import AuditLog
from apps.core.services import (
    audit_changes,
    clear_throttle,
    consume_rate_limit,
    enqueue_email,
    record_throttle_failure,
    run_mutation,
    save_audited,
    throttle_state,
)
from apps.core.throttling import BackoffPolicy, ThrottleState, ip_bucket

from . import engine
from .models import (
    ActionLinkIssue,
    ActionLinkUse,
    PasskeyDevice,
    RecoveryCode,
    RecoveryCodeDevice,
    User,
    normalize_email_address,
)

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

LOGIN_SUCCEEDED = "accounts.login.succeeded"
LOGIN_FAILED = "accounts.login.failed"
LOCKED_OUT = "accounts.login.locked_out"
REGISTRATION_RATE_LIMITED = "accounts.registration.rate_limited"
THROTTLE_MODEL = "core.throttle"
USER_MODEL = "accounts.user"
UNKNOWN_ACCOUNT = "unknown"  # target key of a failed login that named no account
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
    if not keys or _retry_after(list(keys), now):
        return
    for key, policy in keys.items():
        before, after = record_throttle_failure(key, policy, now=now)
        _audit_new_lock(key, before, after, action=LOCKED_OUT, ip=ip, now=now)
    _audit_failed_login(identifier, ip)


def _audit_failed_login(identifier: str | None, ip: str | None) -> None:
    """Audit a counted failure against the account it named, if any (SEC.4).

    What was typed for an unknown account is not stored: it may be a typo of a real address,
    or a password typed in the wrong field.
    """
    normalized = normalize_email_address(identifier or "")
    user = User.objects.filter(email=normalized).only("public_id").first() if normalized else None
    ctx = AuditContext(actor_label="anonymous", source=AuditLog.Source.SYSTEM, ip=ip)
    key = str(user.public_id) if user is not None else UNKNOWN_ACCOUNT
    record(ctx, LOGIN_FAILED, USER_MODEL, key, None, None)


def record_successful_login(user: User, *, ctx: AuditContext) -> None:
    """Audit the login and reset the account counter. The IP counter only decays: resetting
    it would let one valid account launder an IP that keeps guessing other accounts' passwords.
    """
    record(ctx, LOGIN_SUCCEEDED, USER_MODEL, str(user.public_id), None, None)
    if account := _account_key(user.get_username()):
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


# --- Multi-factor authentication (CORE.4, spec 02 §2.2)

ENROLLED = "accounts.mfa.enrolled"
DEVICE_REMOVED = "accounts.mfa.device_removed"
DISABLED = "accounts.mfa.disabled"
RECOVERY_CODES_GENERATED = "accounts.mfa.recovery_codes_generated"
RECOVERY_CODE_USED = "accounts.mfa.recovery_code_used"

TOTP = "totp"
PASSKEY = "passkey"
RECOVERY_CODES = "recovery_codes"
DEVICE_KINDS: Mapping[str, type[Model]] = {
    TOTP: TOTPDevice,
    PASSKEY: PasskeyDevice,
    RECOVERY_CODES: RecoveryCodeDevice,
}

type SecondFactor = TOTPDevice | PasskeyDevice
type AnyDevice = TOTPDevice | PasskeyDevice | RecoveryCodeDevice


class MfaError(ValueError):
    """An MFA operation was refused (wrong code, already confirmed device, ...)."""


class MfaPolicyError(MfaError):
    """The operation would leave a user who must use MFA without a second factor."""


def mfa_required(user: AbstractBaseUser | AnonymousUser) -> bool:
    """Whether `user` must verify a second factor before using the site or the admin.

    Staff and superusers always must. Each dotted path in `settings.MFA_REQUIRED_POLICIES`
    names a `check(user) -> bool` that adds more roles (vendor members: `is_vendor_member`);
    extra checks can only add requirements, never lift them.
    """
    if not user.is_authenticated:
        return False
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    return any(import_string(path)(user) for path in settings.MFA_REQUIRED_POLICIES)


def kind_of(device: AnyDevice) -> str:
    for kind, model in DEVICE_KINDS.items():
        if isinstance(device, model):
            return kind
    raise TypeError(f"not an MFA device: {device!r}")


def _label(device: Model) -> str:
    return device._meta.label_lower


def _key(device: AnyDevice) -> str:
    public_id = getattr(device, "public_id", None)
    return str(public_id if public_id is not None else device.pk)


def _snapshot(device: AnyDevice) -> dict[str, Any]:
    return {"kind": kind_of(device), "name": device.name, "user": str(device.user.public_id)}


def second_factors(user: User) -> list[SecondFactor]:
    """Confirmed factors that can verify a login (recovery codes are a fallback, not a factor)."""
    totp: list[SecondFactor] = list(TOTPDevice.objects.filter(user=user, confirmed=True))
    passkeys: list[SecondFactor] = list(PasskeyDevice.objects.filter(user=user, confirmed=True))
    return totp + passkeys


def has_second_factor(user: User) -> bool:
    return (
        TOTPDevice.objects.filter(user=user, confirmed=True).exists()
        or PasskeyDevice.objects.filter(user=user, confirmed=True).exists()
    )


def unused_recovery_codes(user: User) -> int:
    return RecoveryCode.objects.filter(device__user=user, used_at__isnull=True).count()


@dataclass(frozen=True)
class RecoveryCodes:
    """The new plain codes are returned once, to be shown once; only their hashes are stored."""

    changeset: ChangeSet
    codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Enrolment:
    device: SecondFactor
    recovery_codes: list[str]  # new codes when this enrolment created the user's first set


def regenerate_recovery_codes(
    user: User, *, ctx: AuditContext, dry_run: bool = False
) -> RecoveryCodes:
    """Replace every recovery code of `user` with a new set (the old ones stop working)."""
    device = RecoveryCodeDevice.objects.filter(user=user).first()
    now = timezone.now()
    changeset = ChangeSet()
    changeset.update(
        _label(device) if device else "accounts.recoverycodedevice",
        str(device.public_id) if device else str(user.public_id),
        {
            "unused_codes": unused_recovery_codes(user),
            "issued_at": device.updated_at if device else None,
        },
        {"unused_codes": engine.RECOVERY_CODE_COUNT, "issued_at": now},
    )
    codes = [] if dry_run else engine.generate_recovery_codes()

    def apply() -> None:
        target, _ = RecoveryCodeDevice.objects.select_for_update().get_or_create(
            user=user, defaults={"name": "Recovery codes", "confirmed": True}
        )
        target.codes.all().delete()
        RecoveryCode.objects.bulk_create(
            RecoveryCode(device=target, code_hash=engine.hash_recovery_code(code)) for code in codes
        )
        target.throttle_reset(commit=False)
        target.save()

    run_mutation(changeset, apply, ctx=ctx, action=RECOVERY_CODES_GENERATED, dry_run=dry_run)
    return RecoveryCodes(changeset, codes)


def _after_enrolment(device: SecondFactor, ctx: AuditContext) -> Enrolment:
    """Audit a confirmed factor; give the user a first set of recovery codes if they have none."""
    record(ctx, ENROLLED, _label(device), _key(device), None, _snapshot(device))
    codes: list[str] = []
    if not RecoveryCodeDevice.objects.filter(user=device.user).exists():
        codes = regenerate_recovery_codes(device.user, ctx=ctx).codes
    return Enrolment(device, codes)


def start_totp_enrolment(user: User, pending_id: int | None = None) -> TOTPDevice:
    """The pending (unconfirmed) authenticator-app device of one enrolment.

    `pending_id` is the device this enrolment created earlier (the caller keeps it in the
    session). Any other unconfirmed device of the user is deleted and a fresh one with a new
    key is created, so a secret recorded in one session can never be confirmed from another.
    """
    with transaction.atomic():
        pending = TOTPDevice.objects.select_for_update().filter(user=user, confirmed=False)
        if pending_id is not None:
            device = pending.filter(pk=pending_id).first()
            if device is not None:
                return device
        pending.delete()
        return TOTPDevice.objects.create(user=user, name="Authenticator app", confirmed=False)


def confirm_totp_device(device: TOTPDevice, token: str, *, ctx: AuditContext) -> Enrolment:
    """Confirm a pending device with a code from the app. Verifying consumes the code, so this
    service has no dry-run."""
    with transaction.atomic():
        locked = TOTPDevice.objects.select_for_update().get(pk=device.pk)
        if locked.confirmed:
            raise MfaError("device already confirmed")
        if not locked.verify_token(token):
            raise MfaError("invalid code")
        locked.confirmed = True
        locked.save(update_fields=["confirmed"])
        return _after_enrolment(locked, ctx)


def verify_totp(user: User, token: str) -> TOTPDevice | None:
    """The confirmed device of `user` that accepts `token` (throttled, no replay), if any."""
    for device in TOTPDevice.objects.filter(user=user, confirmed=True).order_by("id"):
        verified = django_otp.verify_token(user, device.persistent_id, token)
        if verified is not None:
            return verified
    return None


def use_recovery_code(user: User, code: str, *, ctx: AuditContext) -> RecoveryCodeDevice | None:
    """Consume one recovery code of `user`; audited, since it bypasses the usual factor."""
    with transaction.atomic():
        device = (
            RecoveryCodeDevice.objects.select_for_update().filter(user=user, confirmed=True).first()
        )
        if device is None:
            return None
        before = device.codes.filter(used_at__isnull=True).count()
        if not device.verify_token(code):
            return None
        record(
            ctx,
            RECOVERY_CODE_USED,
            _label(device),
            _key(device),
            {"unused_codes": before},
            {"unused_codes": before - 1},
        )
    return device


def register_passkey(
    user: User, passkey: engine.RegisteredPasskey, *, name: str, ctx: AuditContext
) -> Enrolment:
    """Store a verified WebAuthn registration as a confirmed second factor."""
    with transaction.atomic():
        if PasskeyDevice.objects.filter(credential_id=passkey.credential_id).exists():
            raise engine.PasskeyError("credential already registered")
        device = PasskeyDevice.objects.create(
            user=user,
            name=(name.strip() or "Passkey")[:64],
            confirmed=True,
            credential_id=passkey.credential_id,
            public_key=passkey.public_key,
            sign_count=passkey.sign_count,
            transports=passkey.transports,
            aaguid=passkey.aaguid,
            backed_up=passkey.backed_up,
        )
        return _after_enrolment(device, ctx)


def authenticate_passkey(
    user: User, rp: engine.RelyingParty, credential: Mapping[str, Any], *, challenge: bytes
) -> PasskeyDevice:
    """Verify an assertion from one of `user`'s passkeys and advance its counter."""
    credential_id = engine.credential_id_from(credential)
    with transaction.atomic():
        device = (
            PasskeyDevice.objects.select_for_update()
            .filter(user=user, confirmed=True, credential_id=credential_id)
            .first()
        )
        if device is None:
            raise engine.PasskeyError("unknown credential")
        device.sign_count = engine.verify_passkey_authentication(
            rp,
            credential,
            challenge=challenge,
            public_key=bytes(device.public_key),
            sign_count=device.sign_count,
        )
        device.last_used_at = timezone.now()
        device.save(update_fields=["sign_count", "last_used_at", "updated_at"])
    return device


def remove_device(device: AnyDevice, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Remove one factor. Removing the last one disables MFA, which required roles may not do."""
    user = device.user
    kind = kind_of(device)
    others = [d for d in second_factors(user) if (type(d), d.pk) != (type(device), device.pk)]
    disabling = kind != RECOVERY_CODES and not others
    if disabling and mfa_required(user):
        raise MfaPolicyError("a user who must use MFA cannot remove their last second factor")
    recovery = RecoveryCodeDevice.objects.filter(user=user).first() if disabling else None

    changeset = ChangeSet()
    changeset.delete(_label(device), _key(device), _snapshot(device))
    if recovery is not None:
        changeset.delete(_label(recovery), _key(recovery), _snapshot(recovery))
    if dry_run:
        return changeset
    with transaction.atomic():
        before = _snapshot(device)
        key = _key(device)
        device.delete()
        record(ctx, DEVICE_REMOVED, _label(device), key, before, None)
        if disabling:
            if recovery is not None:
                recovery.delete()
            factors = sorted({kind} | ({RECOVERY_CODES} if recovery else set()))
            record(ctx, DISABLED, "accounts.user", str(user.public_id), {"factors": factors}, None)
    return changeset


def reset_mfa(user: User, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Delete every factor and recovery code of `user` (lost device, offboarding).

    A required-role user is sent back to enrolment on their next request, and sessions that
    were verified by a deleted device stop counting as verified.
    """
    devices: list[AnyDevice] = [
        *TOTPDevice.objects.filter(user=user),
        *PasskeyDevice.objects.filter(user=user),
        *RecoveryCodeDevice.objects.filter(user=user),
    ]
    changeset = ChangeSet()
    for device in devices:
        changeset.delete(_label(device), _key(device), _snapshot(device))
    if dry_run or changeset.is_empty:
        return changeset
    factors = sorted({kind_of(device) for device in devices})
    with transaction.atomic():
        for device in devices:
            device.delete()
        record(ctx, DISABLED, "accounts.user", str(user.public_id), {"factors": factors}, None)
    return changeset


VERIFIED = "accounts.mfa.verified"
VERIFICATION_FAILED = "accounts.mfa.verification_failed"
VERIFICATION_METHODS = frozenset({TOTP, PASSKEY, "recovery"})


def record_mfa_verified(device: AnyDevice, *, ctx: AuditContext) -> None:
    """Audit that `device` verified its user's session (login, step-up or enrolment)."""
    record(ctx, VERIFIED, USER_MODEL, str(device.user.public_id), None, {"kind": kind_of(device)})


def record_mfa_failure(user: User, method: str, *, ctx: AuditContext) -> None:
    """Audit a refused second-factor code or passkey: the password was already right."""
    after = {"method": method if method in VERIFICATION_METHODS else "unknown"}
    record(ctx, VERIFICATION_FAILED, USER_MODEL, str(user.public_id), None, after)


# --- Staff changes to users, groups and permissions (SEC.4)
#
# The admin saves these through the services below, so every change to an account's identity,
# flags, credentials or permissions is audited with before/after. No password hash is stored.

USER_CREATED = "accounts.user.created"
USER_UPDATED = "accounts.user.updated"
USER_DELETED = "accounts.user.deleted"
CREDENTIAL_CHANGED = "accounts.user.password_changed"
ACCESS_CHANGED = "accounts.user.access_changed"
USER_FIELDS = (
    "email",
    "full_name",
    "preferred_language",
    "is_active",
    "is_staff",
    "is_superuser",
)

GROUP_MODEL = "auth.group"
GROUP_CREATED = "auth.group.created"
GROUP_UPDATED = "auth.group.updated"
GROUP_DELETED = "auth.group.deleted"
GROUP_ACCESS_CHANGED = "auth.group.access_changed"


def _permission_labels(permissions: Iterable[Permission]) -> list[str]:
    return sorted(f"{p.content_type.app_label}.{p.codename}" for p in permissions)


def user_snapshot(user: User) -> dict[str, Any]:
    return {
        **{name: getattr(user, name) for name in USER_FIELDS},
        "has_usable_password": user.has_usable_password(),
    }


def access_snapshot(user: User) -> dict[str, Any]:
    """The user's groups and direct permissions, as stored."""
    if user.pk is None:
        return {"groups": [], "user_permissions": []}
    return {
        "groups": sorted(user.groups.values_list("name", flat=True)),
        "user_permissions": _permission_labels(
            user.user_permissions.select_related("content_type")
        ),
    }


def save_user(user: User, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Create or update `user` (profile, active, staff and superuser flags), audited."""
    return save_audited(
        user,
        user_snapshot,
        key=str(user.public_id),
        ctx=ctx,
        created=USER_CREATED,
        updated=USER_UPDATED,
        dry_run=dry_run,
    )


def record_access_change(user: User, before: Mapping[str, Any], *, ctx: AuditContext) -> None:
    """Audit group and permission changes a form has just saved (in the same transaction)."""
    changeset = ChangeSet()
    changeset.update(USER_MODEL, str(user.public_id), before, access_snapshot(user))
    audit_changes(changeset, ctx=ctx, action=ACCESS_CHANGED)


def record_password_change(user: User, *, had_usable_password: bool, ctx: AuditContext) -> None:
    """Audit a password set by staff, for someone else or themselves.

    Only whether a usable password exists is stored, never the hash.
    """
    record(
        ctx,
        CREDENTIAL_CHANGED,
        USER_MODEL,
        str(user.public_id),
        {"has_usable_password": had_usable_password},
        {"has_usable_password": user.has_usable_password()},
    )


def delete_user(user: User, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Delete `user` with their second factors (cascade), audited with what they held."""
    devices: list[AnyDevice] = [
        *TOTPDevice.objects.filter(user=user),
        *PasskeyDevice.objects.filter(user=user),
        *RecoveryCodeDevice.objects.filter(user=user),
    ]
    before = {
        **user_snapshot(user),
        **access_snapshot(user),
        "mfa_factors": sorted({kind_of(device) for device in devices}),
    }
    changeset = ChangeSet()
    changeset.delete(USER_MODEL, str(user.public_id), before)
    return run_mutation(changeset, user.delete, ctx=ctx, action=USER_DELETED, dry_run=dry_run)


def group_snapshot(group: Group) -> dict[str, Any]:
    return {"name": group.name}


def group_access_snapshot(group: Group) -> dict[str, Any]:
    if group.pk is None:
        return {"permissions": []}
    return {"permissions": _permission_labels(group.permissions.select_related("content_type"))}


def save_group(group: Group, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Create or rename a group, audited. Groups have no public id: the key is the (unique)
    name at the time of the change; a rename shows the old name in `before`."""
    return save_audited(
        group,
        group_snapshot,
        key=group.name,
        ctx=ctx,
        created=GROUP_CREATED,
        updated=GROUP_UPDATED,
        dry_run=dry_run,
    )


def record_group_access_change(
    group: Group, before: Mapping[str, Any], *, ctx: AuditContext
) -> None:
    changeset = ChangeSet()
    changeset.update(GROUP_MODEL, group.name, before, group_access_snapshot(group))
    audit_changes(changeset, ctx=ctx, action=GROUP_ACCESS_CHANGED)


def delete_group(group: Group, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    changeset = ChangeSet()
    changeset.delete(
        GROUP_MODEL, group.name, {**group_snapshot(group), **group_access_snapshot(group)}
    )
    return run_mutation(changeset, group.delete, ctx=ctx, action=GROUP_DELETED, dry_run=dry_run)


# --- Secure action links (CORE.15, spec 02 "Secure action links")
#
# An email asks a user to act on one object (approve a proposal, answer a reminder). Its link
# carries a token signed with Django's signing (HMAC with SECRET_KEY, a salt of its own,
# constant-time signature check) and bound to a purpose, the object, the recipient and the
# object version. The app that owns the object registers its purpose (`register_action_purpose`)
# in its AppConfig.ready, so accounts never imports it. A GET only shows a "Continue" page; the
# Continue POST spends the token's single sign-in use; the action page itself needs a step-up.
# A link also carries its issue generation and a stamp of the recipient's password: issuing a
# newer link for the same object and recipient, or a password change, revokes it.

ACTION_LINK_SALT = "mpmv.accounts.action-link"
ACTION_LINK_SIGNED_IN = "accounts.action_link.signed_in"
ACTION_LINK_REFUSED = "accounts.action_link.refused"
ACTION_LINK_SENT = "accounts.action_link.sent"
# Refusals with no account to attach them to (a bad signature, a recipient that does not exist)
# are logged here, not audited: anyone can send those, and the Continue POST is rate limited.
ACTION_LINK_LOGGER = "mpmv.accounts.action_links"
_log = logging.getLogger(ACTION_LINK_LOGGER)
# The query parameter that tells the action page which version the link was issued for.
ACTION_VERSION_PARAM = "version"
ACTION_LINK_RATE_WINDOW = dt.timedelta(minutes=15)
_ACTION_TOKEN_MAX_LENGTH = 2048


@dataclass(frozen=True)
class ActionPurpose:
    """What one kind of action link opens, told by the app that owns the object.

    - `version_of(object_key)`: the object's current version (None when it is gone); a link
      issued for another version is refused.
    - `url_of(object_key)`: the path of the action page (which needs `step_up_required`). A
      usable link leads there with `?version=<the link's version>` (`ACTION_VERSION_PARAM`).
      The page shows that version and puts it in its form; its approve or decline POST must
      call `action_version_matches` and refuse the action when the object changed since.
    - `may_act(object_key, user)`: whether the recipient may still act on the object.
    - `max_age`: seconds a link stays valid; None for `settings.ACTION_LINK_MAX_AGE`.
    """

    name: str
    version_of: Callable[[str], object | None]
    url_of: Callable[[str], str]
    may_act: Callable[[str, User], bool]
    max_age: int | None = None


_ACTION_PURPOSES: dict[str, ActionPurpose] = {}


def register_action_purpose(purpose: ActionPurpose) -> None:
    """Make links of `purpose.name` usable; registering a name again replaces it."""
    _ACTION_PURPOSES[purpose.name] = purpose


@dataclass(frozen=True)
class ActionLinkCheck:
    """The outcome of checking a link: `refusal` is None when it can be used now."""

    refusal: str | None
    claims: engine.ActionClaims | None = None
    user: User | None = None
    action_url: str | None = None


def _issue_action_claims(
    *,
    purpose: str,
    object_key: str,
    recipient: User,
    version: object,
    expires_in: int | None,
    now: float | None,
    dry_run: bool = False,
) -> tuple[str, engine.ActionClaims]:
    registered = _ACTION_PURPOSES.get(purpose)
    if registered is None:
        raise ValueError(f"unknown action link purpose {purpose!r}")
    generation = _next_generation(purpose, object_key, recipient, dry_run=dry_run)
    claims = engine.new_action_claims(
        purpose=purpose,
        object_key=object_key,
        recipient=str(recipient.public_id),
        version=version,
        now=time.time() if now is None else now,
        max_age=expires_in or registered.max_age or settings.ACTION_LINK_MAX_AGE,
        generation=generation,
        stamp=_password_stamp(recipient),
    )
    payload = engine.action_claims_payload(claims)
    return signing.dumps(payload, salt=ACTION_LINK_SALT, compress=True), claims


def _password_stamp(user: User) -> str:
    """A keyed digest of the user's password hash: it changes with every password change, so a
    link issued before a reset stops working. Keyed, so the token reveals nothing about it."""
    return salted_hmac(
        f"{ACTION_LINK_SALT}.password", user.password, algorithm="sha256"
    ).hexdigest()[:32]


def _next_generation(purpose: str, object_key: str, user: User, *, dry_run: bool) -> int:
    """Count one more link for (purpose, object, user); the older links are then revoked."""
    lookup: dict[str, Any] = {"purpose": purpose, "object_key": object_key, "user": user}
    if dry_run:
        current = ActionLinkIssue.objects.filter(**lookup).values_list("generation", flat=True)
        return (current.first() or 0) + 1
    with transaction.atomic():
        # ON CONFLICT DO NOTHING: two concurrent first issues do not fail on the unique key.
        ActionLinkIssue.objects.bulk_create([ActionLinkIssue(**lookup)], ignore_conflicts=True)
        issue = ActionLinkIssue.objects.select_for_update().get(**lookup)
        issue.generation += 1
        issue.save(update_fields=["generation", "updated_at"])
    return issue.generation


def _current_generation(claims: engine.ActionClaims, user: User) -> int:
    current = ActionLinkIssue.objects.filter(
        purpose=claims.purpose, object_key=claims.object_key, user=user
    ).values_list("generation", flat=True)
    return current.first() or 0


def issue_action_token(
    *,
    purpose: str,
    object_key: str,
    recipient: User,
    version: object,
    expires_in: int | None = None,
    now: float | None = None,
) -> str:
    """A signed token for `recipient` to act on `object_key` at `version` (CORE.15).

    It expires after `expires_in` seconds (default: the purpose's, then
    `settings.ACTION_LINK_MAX_AGE`, 72 h). Issuing it revokes the links issued before for the
    same purpose, object and recipient. Raises ValueError for an unregistered purpose. The
    token is a credential: never log it; email it with `send_action_link_email`.
    """
    token, _ = _issue_action_claims(
        purpose=purpose,
        object_key=object_key,
        recipient=recipient,
        version=version,
        expires_in=expires_in,
        now=now,
    )
    return token


def action_link_path(token: str) -> str:
    """The path of the link's "Continue" page."""
    return reverse("action_link:open", args=[token])


def _recipient(public_id: str) -> User | None:
    try:
        return User.objects.filter(public_id=public_id).first()
    except ValidationError:  # not a UUID: cannot come from a token we signed
        return None


def action_version_matches(purpose: str, object_key: str, version: str | None) -> bool:
    """Whether `version` is still the object's current version: the re-check an action page's
    approve or decline POST makes, so a change after the link was opened is never acted on."""
    registered = _ACTION_PURPOSES.get(purpose)
    if registered is None or version is None:
        return False
    current = registered.version_of(object_key)
    return current is not None and hmac.compare_digest(str(current).encode(), version.encode())


def action_page_url(check: ActionLinkCheck) -> str:
    """Where a usable link leads: its action page, told the version the link was issued for."""
    if check.action_url is None or check.claims is None:
        raise ValueError("only a usable link has an action page")
    separator = "&" if "?" in check.action_url else "?"
    query = urlencode({ACTION_VERSION_PARAM: check.claims.version})
    return f"{check.action_url}{separator}{query}"


def check_action_token(token: str, *, now: float | None = None) -> ActionLinkCheck:
    """Check a token without using it: signature, purpose, expiry, version, revocation and
    recipient.

    Read-only, so safe for GET and HEAD. Single use is checked only when the token is redeemed.
    """
    try:
        if len(token) > _ACTION_TOKEN_MAX_LENGTH:
            raise signing.BadSignature("token too long")
        payload = signing.loads(token, salt=ACTION_LINK_SALT)
    except signing.BadSignature:
        return ActionLinkCheck(engine.BAD_SIGNATURE)
    claims = engine.action_claims_from_payload(payload)
    if claims is None:
        return ActionLinkCheck(engine.BAD_SIGNATURE)
    purpose = _ACTION_PURPOSES.get(claims.purpose)
    user = _recipient(claims.recipient)
    if purpose is None:
        return ActionLinkCheck(engine.UNKNOWN_PURPOSE, claims, user)
    version = purpose.version_of(claims.object_key)
    refusal = engine.action_link_refusal(
        claims,
        now=time.time() if now is None else now,
        current_version=None if version is None else str(version),
        current_generation=None if user is None else _current_generation(claims, user),
        current_stamp=None if user is None else _password_stamp(user),
    )
    if refusal is None and (user is None or not purpose.may_act(claims.object_key, user)):
        refusal = engine.WRONG_RECIPIENT
    if refusal is None and user is not None and not user.is_active:
        refusal = engine.INACTIVE
    return ActionLinkCheck(refusal, claims, user, purpose.url_of(claims.object_key))


def _link_snapshot(claims: engine.ActionClaims) -> dict[str, str]:
    return {"purpose": claims.purpose, "object": claims.object_key, "version": claims.version}


def _audit_refused_link(check: ActionLinkCheck, ctx: AuditContext) -> None:
    """Audit a refusal against the recipient's account. With no account (a bad signature, a
    recipient that does not exist) there is nothing to attach it to and anyone can send one, so
    it is only logged, with the reason and never the token."""
    if check.user is None:
        _log.info("action link refused: %s", check.refusal, extra={"reason": check.refusal})
        return
    after: dict[str, str] = {"reason": check.refusal or ""}
    if check.claims is not None:
        after |= {"purpose": check.claims.purpose, "object": check.claims.object_key}
    record(ctx, ACTION_LINK_REFUSED, USER_MODEL, str(check.user.public_id), None, after)


def _spend(check: ActionLinkCheck, ctx: AuditContext) -> ActionLinkCheck:
    """Record the single sign-in use with a unique insert: of two concurrent POSTs one wins."""
    claims, user = check.claims, check.user
    if claims is None or user is None:  # never for a valid check; refuse rather than assume
        return replace(check, refusal=engine.WRONG_RECIPIENT)
    try:
        with transaction.atomic():
            ActionLinkUse.objects.create(
                nonce_digest=engine.action_nonce_digest(claims.nonce),
                purpose=claims.purpose,
                object_key=claims.object_key,
                user=user,
            )
            snapshot = _link_snapshot(claims)
            record(ctx, ACTION_LINK_SIGNED_IN, USER_MODEL, str(user.public_id), None, snapshot)
    except IntegrityError:
        return replace(check, refusal=engine.USED)
    return check


def _is_staff(user: User) -> bool:
    return bool(user.is_staff or user.is_superuser)


def redeem_action_token(
    token: str, *, session_user: User | None, ctx: AuditContext, now: float | None = None
) -> ActionLinkCheck:
    """The Continue POST: check the token and, without a session, spend its sign-in use.

    - Signed in as the recipient: nothing is spent, the user goes on to the action page.
    - Signed in as someone else: refused.
    - No session: the single use is recorded (`ActionLinkUse`) and audited; the caller then
      signs the recipient in, without MFA: the action page asks for the step-up.
    - No session and no confirmed second factor: refused, nothing spent. The first factor can
      be enrolled without other proof (CORE.4), so a passwordless sign-in would let whoever
      holds only the mailbox bind their own authenticator, pass the step-up and approve.
    - No session and a staff or superuser recipient: refused, nothing spent. Staff sign in with
      their password and MFA, then continue (lane-a decision on the CORE.15 review).

    Every refusal against a known account is audited (reason, purpose and object; never the
    token); the others are only logged. The caller sends a refused visitor to the normal login,
    then on to `action_url` when it is known.
    """
    check = check_action_token(token, now=now)
    if check.refusal is None:
        recipient = check.user
        if session_user is None and recipient is not None and _is_staff(recipient):
            check = replace(check, refusal=engine.STAFF_RECIPIENT)
        elif session_user is None and (recipient is None or not has_second_factor(recipient)):
            check = replace(check, refusal=engine.NO_SECOND_FACTOR)
        elif session_user is None:
            check = _spend(check, ctx)
        elif check.user is None or session_user.pk != check.user.pk:
            check = replace(check, refusal=engine.WRONG_RECIPIENT)
    if check.refusal is not None:
        _audit_refused_link(check, ctx)
    return check


def action_link_retry_after(ip: str | None) -> dt.timedelta:
    """Count one Continue POST from `ip`; zero when allowed, else the wait (Retry-After)."""
    return consume_rate_limit(
        f"action-link:ip:{ip_bucket(ip) or 'unknown'}",
        limit=settings.ACTION_LINK_RATE_LIMIT,
        window=ACTION_LINK_RATE_WINDOW,
    )


def send_action_link_email(
    *,
    purpose: str,
    object_key: str,
    recipient: User,
    version: object,
    template_key: str,
    context: Mapping[str, Any],
    ctx: AuditContext,
    expires_in: int | None = None,
    idempotency_key: str | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Email `recipient` a secure action link through the ARC.16 outbox, in their language.

    The template gets `action_url` (absolute, on `settings.SITE_BASE_URL`) and
    `action_link_expires_at` on top of `context`. The new link revokes the ones emailed before
    for the same purpose, object and recipient (not on a dry run). The outbox stores the
    context encrypted; the audit entry names the purpose, object, version and generation, never
    the token.
    """
    token, claims = _issue_action_claims(
        purpose=purpose,
        object_key=object_key,
        recipient=recipient,
        version=version,
        expires_in=expires_in,
        now=None,
        dry_run=dry_run,
    )
    values = {
        **context,
        "action_url": settings.SITE_BASE_URL.rstrip("/") + action_link_path(token),
        "action_link_expires_at": dt.datetime.fromtimestamp(claims.expires_at, tz=dt.UTC),
    }
    with transaction.atomic():
        changes = enqueue_email(
            recipient=recipient.email,
            template_key=template_key,
            language=recipient.preferred_language,
            context=values,
            idempotency_key=idempotency_key,
            dry_run=dry_run,
        )
        if not dry_run and not changes.is_empty:
            snapshot = {**_link_snapshot(claims), "generation": str(claims.generation)}
            record(ctx, ACTION_LINK_SENT, USER_MODEL, str(recipient.public_id), None, snapshot)
    return changes
