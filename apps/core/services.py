"""Core services: the dry-run + audit pattern shared by all mutating services (spec 01
conventions), the platform operator, feature flags, the worker heartbeat, throttle counters and
the rotation of field encryption keys."""

import datetime as dt
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import F, Model, Q
from django.utils import timezone

from . import crypto, encryption, flags
from .audit import VENDOR_MODEL, AuditContext, record
from .changeset import ChangeSet
from .models import VENDOR_OVERRIDES, FeatureFlag, Heartbeat, PlatformOperator, Throttle
from .throttling import BackoffPolicy, ThrottleState, after_failure
from .validity import NoValidRow, OverlappingValidity, next_start_error, pick_valid


def run_mutation(
    changeset: ChangeSet,
    apply: Callable[[], object],
    *,
    ctx: AuditContext,
    action: str,
    dry_run: bool,
) -> ChangeSet:
    """Return the ChangeSet; unless dry-run, apply it and audit every change atomically."""
    if dry_run or changeset.is_empty:
        return changeset
    with transaction.atomic():
        apply()
        audit_changes(changeset, ctx=ctx, action=action)
    return changeset


def audit_changes(changeset: ChangeSet, *, ctx: AuditContext, action: str) -> None:
    """One AuditLog entry per change. For changes already applied by the caller in the same
    transaction (e.g. many-to-many fields saved by a form); otherwise use `run_mutation`."""
    for change in changeset.changes:
        record(ctx, action, change.model, change.key, change.before, change.after)


def save_audited[M: Model](
    instance: M,
    snapshot: Callable[[M], dict[str, Any]],
    *,
    key: str,
    ctx: AuditContext,
    created: str,
    updated: str,
    dry_run: bool = False,
) -> ChangeSet:
    """Insert or update `instance`, auditing the fields `snapshot` exposes (SEC.4).

    For an existing row, `before` is read from the database (row locked), never from the
    edited instance. The row is saved even when no audited field changed, but only real
    changes are audited.
    """
    label = instance._meta.label_lower
    adding = instance._state.adding
    changeset = ChangeSet()
    with transaction.atomic():
        if adding:
            changeset.create(label, key, snapshot(instance))
        else:
            stored = type(instance)._default_manager.select_for_update().get(pk=instance.pk)
            changeset.update(label, key, snapshot(stored), snapshot(instance))
        if dry_run:
            return changeset
        instance.save()
        audit_changes(changeset, ctx=ctx, action=created if adding else updated)
    return changeset


# --- Platform operator (ARC.7)


class NoPlatformOperator(NoValidRow):
    """No PlatformOperator is configured for the requested instant."""


class OverlappingPlatformOperators(OverlappingValidity):
    """More than one PlatformOperator covers the requested instant."""


OPERATOR_MODEL = "core.platformoperator"
OPERATOR_CONFIGURED = "core.platform_operator.configured"
OPERATOR_FIELDS = (
    "legal_name",
    "country",
    "vat_number",
    "tax_regime",
    "commission_vat_mode",
    "einvoice_channel",
    "dac7_authority_country",
    "valid_from",
    "valid_to",
)


def operator_snapshot(op: PlatformOperator) -> dict[str, Any]:
    return {name: getattr(op, name) for name in OPERATOR_FIELDS}


def current_operator(at: dt.datetime | None = None) -> PlatformOperator:
    """The PlatformOperator valid at `at` (default: now); a clear error if none or ambiguous."""
    at = at or timezone.now()
    rows = PlatformOperator.objects.filter(valid_from__lte=at).filter(
        Q(valid_to__isnull=True) | Q(valid_to__gt=at)
    )
    try:
        return pick_valid(rows, at, what="PlatformOperator")
    except OverlappingValidity as exc:
        raise OverlappingPlatformOperators(str(exc)) from exc
    except NoValidRow as exc:
        raise NoPlatformOperator(str(exc)) from exc


def latest_operator() -> PlatformOperator | None:
    return PlatformOperator.objects.order_by("-valid_from").first()


def check_operator_start(valid_from: dt.datetime, latest: PlatformOperator | None) -> None:
    """A new configuration starts after the latest one, and only an open-ended row is closed."""
    error = next_start_error(valid_from, latest)
    if error is not None:
        raise ValidationError({"valid_from": error})


def configure_platform_operator(
    new: PlatformOperator, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Add a configuration row from `new.valid_from`, closing the open-ended current one.

    Existing rows are never edited, so documents and snapshots computed under an earlier
    configuration remain explainable.
    """
    if new.pk is not None:
        raise ValueError("Pass a new, unsaved PlatformOperator")
    new.valid_to = None
    new.full_clean(validate_constraints=False)  # constraints are enforced by the database
    latest = latest_operator()
    check_operator_start(new.valid_from, latest)

    changeset = ChangeSet()
    closing = latest if latest is not None and latest.valid_to is None else None
    if closing is not None:
        changeset.update(
            OPERATOR_MODEL,
            str(closing.public_id),
            {"valid_to": None},
            {"valid_to": new.valid_from},
        )
    changeset.create(
        OPERATOR_MODEL,
        str(new.public_id),
        operator_snapshot(new),
        supersedes=operator_snapshot(latest) if latest is not None else None,
    )

    def apply() -> None:
        if closing is not None:
            closing.valid_to = new.valid_from
            closing.save(update_fields=["valid_to", "updated_at"])
        new.save()

    return run_mutation(changeset, apply, ctx=ctx, action=OPERATOR_CONFIGURED, dry_run=dry_run)


# --- Feature flags and kill switches (ARC.8, docs/dev/feature-flags.md)

FLAG_MODEL = "core.featureflag"
FLAG_CREATED = "core.feature_flag.created"
FLAG_UPDATED = "core.feature_flag.updated"
FLAG_FIELDS = ("name", "state", "note")

# This process's copy of the stored switches, kept FEATURE_FLAGS_CACHE_SECONDS (0: no cache).
FLAG_CACHE: flags.TtlCache[flags.FlagSnapshot] = flags.TtlCache()


def flag_snapshot(flag: FeatureFlag) -> dict[str, Any]:
    return {name: getattr(flag, name) for name in FLAG_FIELDS}


def load_flags() -> flags.FlagSnapshot:
    """Every staff switch and vendor override, as stored now (two small queries)."""
    rows = FeatureFlag.objects.filter(**{f"{VENDOR_OVERRIDES}__isnull": False}).values_list(
        "name", f"{VENDOR_OVERRIDES}__vendor_id", f"{VENDOR_OVERRIDES}__enabled"
    )
    return flags.FlagSnapshot(
        states=dict(FeatureFlag.objects.values_list("name", "state")),
        vendor_overrides={(name, vendor_id): enabled for name, vendor_id, enabled in rows},
    )


def environment_default(name: str) -> bool | None:
    """This environment's default for `name` (`settings.FEATURE_FLAGS`), None when unset."""
    value = settings.FEATURE_FLAGS.get(name)
    return None if value is None else bool(value)


def flag_enabled(name: str, vendor: Model | None = None) -> bool:
    """Is the flag `name` on, for the platform or for `vendor` (a `Vendor`)?

    The one helper code uses to read flags and kill switches, e.g.
    `if not flag_enabled(flags.CHECKOUT, vendor=vendor): refuse`. Raises `UnknownFlag` for a name
    nobody registered. Stored switches are cached per process for
    `settings.FEATURE_FLAGS_CACHE_SECONDS`, so a change reaches every process within that time.
    Raises `TypeError` when `vendor` is not a `Vendor`: overrides are keyed by the vendor's
    primary key, so another model's would read some vendor's override.
    """
    if vendor is not None and vendor._meta.label_lower != VENDOR_MODEL:
        raise TypeError(f"vendor must be a {VENDOR_MODEL}, got a {vendor._meta.label_lower}")
    spec = flags.spec_of(name)
    snapshot = FLAG_CACHE.get(load_flags, ttl=settings.FEATURE_FLAGS_CACHE_SECONDS)
    return snapshot.enabled(
        spec,
        environment=environment_default(name),
        vendor_id=None if vendor is None else vendor.pk,
    )


def flags_changed() -> None:
    """Drop this process's cached flags once the current transaction commits.

    Other processes reload within the TTL. Called by every service that writes switches or
    overrides.
    """
    transaction.on_commit(FLAG_CACHE.clear)


def save_flag(flag: FeatureFlag, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Create or change the staff switch of a registered flag (validated, audited).

    Raises `ValidationError` for an unregistered name or an unknown state.
    """
    flag.full_clean()
    changeset = save_audited(
        flag,
        flag_snapshot,
        key=str(flag.public_id),
        ctx=ctx,
        created=FLAG_CREATED,
        updated=FLAG_UPDATED,
        dry_run=dry_run,
    )
    if not dry_run:
        flags_changed()
    return changeset


# --- Worker heartbeat (ARC.9)


def record_heartbeat(name: str, *, scheduled_for: dt.datetime | None = None) -> Heartbeat:
    """Count one beat for `name` and remember when it happened.

    Operational telemetry, not a domain change: no ChangeSet, dry-run or audit entry.
    """
    now = timezone.now()
    beat, _ = Heartbeat.objects.update_or_create(
        name=name,
        defaults={"beats": F("beats") + 1, "last_beat_at": now, "scheduled_for": scheduled_for},
        create_defaults={"beats": 1, "last_beat_at": now, "scheduled_for": scheduled_for},
    )
    beat.refresh_from_db()
    return beat


# --- Throttling (CORE.5)
# Operational counters, not domain changes: no ChangeSet or dry-run. Callers audit the lockouts
# they care about (see apps.accounts.services).


def _state_of(row: Throttle | None, now: dt.datetime) -> ThrottleState:
    if row is None:
        return ThrottleState()
    state = ThrottleState(
        failures=row.failures, locked_until=row.locked_until, expires_at=row.expires_at
    )
    return ThrottleState() if state.is_expired(now) else state


def throttle_state(key: str, *, now: dt.datetime | None = None) -> ThrottleState:
    """The current state of `key`; a clean state when the key is unknown or expired."""
    return _state_of(Throttle.objects.filter(key=key).first(), now or timezone.now())


def record_throttle_failure(
    key: str, policy: BackoffPolicy, *, now: dt.datetime | None = None
) -> tuple[ThrottleState, ThrottleState]:
    """Count one failure for `key` atomically; return the state before and after it."""
    now = now or timezone.now()
    with transaction.atomic():
        row, _ = Throttle.objects.select_for_update().get_or_create(
            key=key, defaults={"expires_at": now}
        )
        before = _state_of(row, now)
        after = after_failure(before, now, policy)
        row.failures = after.failures
        row.locked_until = after.locked_until
        row.expires_at = after.expires_at or now
        row.save(update_fields=["failures", "locked_until", "expires_at", "updated_at"])
    return before, after


def clear_throttle(key: str) -> None:
    """Forget every failure of `key` (e.g. after a successful login)."""
    Throttle.objects.filter(key=key).delete()


def consume_rate_limit(
    key: str, *, limit: int, window: dt.timedelta, now: dt.datetime | None = None
) -> dt.timedelta:
    """Count one use of `key` in a fixed window of `limit` uses; zero when allowed.

    When the window is full the use is not counted and the result is the time until the window
    ends (for `Retry-After`). Shares the `Throttle` table: `failures` is the count of uses and
    `expires_at` the end of the window, so the nightly purge forgets old windows.
    """
    now = now or timezone.now()
    with transaction.atomic():
        row, _ = Throttle.objects.select_for_update().get_or_create(
            key=key, defaults={"expires_at": now}
        )
        if row.expires_at <= now:
            row.failures, row.expires_at = 0, now + window
        if row.failures >= limit:
            return row.expires_at - now
        row.failures += 1
        row.save(update_fields=["failures", "expires_at", "updated_at"])
    return dt.timedelta(0)


def purge_expired_throttles(*, now: dt.datetime | None = None) -> int:
    """Delete the rows nobody needs any more; return how many were deleted."""
    deleted, _ = Throttle.objects.filter(expires_at__lte=now or timezone.now()).delete()
    return deleted


# --- Field encryption keys (SEC.3)

FIELD_KEYS_ROTATED = "core.field_keys.rotated"


def _key_label(raw: str) -> str:
    key_id = encryption.key_id_of_raw(raw)
    return f"key {key_id}" if key_id else "cleartext"


def rotate_field_keys(*, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Re-encrypt with the primary key every encrypted value that uses an older key or is
    stored in clear (e.g. restored from an old dump). Rows are locked while they are rewritten.

    One change (and one audit entry) per object, naming only key ids: `{"iban": "key k1"}` to
    `{"iban": "key k2"}`, never values. Every value is decrypted first, also on dry-run, so a
    wrong or missing key raises `DecryptionError` before anything is written. Afterwards the
    older keys can be removed from the configuration.
    """
    keyring = encryption.configured_keyring()
    after_label = f"key {keyring.primary_id}"
    by_model: dict[type[Any], list[encryption.EncryptedColumn]] = defaultdict(list)
    for column in encryption.encrypted_columns():
        by_model[column.model].append(column)

    changeset = ChangeSet()
    updates: list[tuple[type[Any], Any, dict[str, str]]] = []
    with transaction.atomic():
        for model, columns in by_model.items():
            contexts = {column.field.name: column.context for column in columns}
            for pk, key, stale in encryption.stale_rows(connection, model, columns):
                plaintext = {
                    name: keyring.decrypt(raw, context=contexts[name])
                    if crypto.is_encrypted(raw)
                    else raw
                    for name, raw in stale.items()
                }
                changeset.update(
                    model._meta.label_lower,
                    key,
                    {name: _key_label(raw) for name, raw in stale.items()},
                    dict.fromkeys(stale, after_label),
                )
                updates.append((model, pk, plaintext))

        def apply() -> None:
            for model, pk, values in updates:
                # The fields encrypt with the primary key on the way in.
                model._base_manager.filter(pk=pk).update(**values)

        return run_mutation(changeset, apply, ctx=ctx, action=FIELD_KEYS_ROTATED, dry_run=dry_run)
