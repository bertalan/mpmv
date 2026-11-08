"""Core services: the dry-run + audit pattern shared by all mutating services (spec 01
conventions), the platform operator, feature flags, the worker heartbeat, throttle counters,
the rotation of field encryption keys and the transactional email outbox."""

import datetime as dt
import functools
import json
import logging
import re
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any, cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import F, Model, Q, QuerySet
from django.template import Context, TemplateDoesNotExist
from django.template.loader import select_template
from django.utils import timezone, translation
from django.utils.module_loading import import_string

from . import crypto, encryption, flags, mail
from .audit import VENDOR_MODEL, AuditContext, record
from .changeset import ChangeSet, to_jsonable
from .models import (
    VENDOR_OVERRIDES,
    EmailSuppression,
    FeatureFlag,
    Heartbeat,
    ImageLicence,
    MediaImage,
    OutboxEmail,
    OutboxEmailEvent,
    PlatformOperator,
    Throttle,
)
from .throttling import BackoffPolicy, ThrottleState, after_failure
from .validity import NoValidRow, OverlappingValidity, next_start_error, pick_valid

logger = logging.getLogger(__name__)


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


# --- Images: licence allow list and metadata (CMS.8, spec 10 §10.2)


LICENCE_CREATED = "core.image_licence.created"
LICENCE_UPDATED = "core.image_licence.updated"
LICENCE_FIELDS = (
    "code",
    "name",
    "url",
    "attribution_required",
    "source_required",
    "permission_required",
)


def licence_snapshot(licence: ImageLicence) -> dict[str, Any]:
    return {name: getattr(licence, name) for name in LICENCE_FIELDS}


def save_image_licence(
    licence: ImageLicence, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Add or change an allow-list entry (validated, audited).

    Raises `ValidationError` for a non-commercial or no-derivatives licence.
    """
    licence.full_clean()
    return save_audited(
        licence,
        licence_snapshot,
        key=str(licence.public_id),
        ctx=ctx,
        created=LICENCE_CREATED,
        updated=LICENCE_UPDATED,
        dry_run=dry_run,
    )


def image_snapshot(image: MediaImage) -> dict[str, Any]:
    return {
        "title": image.title,
        "licence": image.licence.code if image.licence is not None else None,
        **{name: getattr(image, name) for name in MediaImage.METADATA_FIELDS[1:]},
    }


def save_image(image: MediaImage, *, dry_run: bool = False) -> ChangeSet:
    """Save an image uploaded outside the Wagtail admin (imports, vendor area).

    Raises `ValidationError` when the licence, author or a field the licence requires is
    missing. Not audited: images are CMS content, like the admin uploads in Wagtail's log.
    """
    image.full_clean()
    key = str(image.pk) if image.pk is not None else image.title
    changeset = ChangeSet()
    if image.pk is None:
        changeset.create(MediaImage._meta.label_lower, key, image_snapshot(image))
    else:
        stored = MediaImage.objects.get(pk=image.pk)
        changeset.update(
            MediaImage._meta.label_lower, key, image_snapshot(stored), image_snapshot(image)
        )
    if not dry_run:
        image.save()
    return changeset


def unclassified(images: QuerySet[MediaImage]) -> QuerySet[MediaImage]:
    """Images missing their licence or author (migrated without known metadata)."""
    return images.filter(Q(licence__isnull=True) | Q(author__regex=r"^\s*$"))


def classified(images: QuerySet[MediaImage]) -> QuerySet[MediaImage]:
    """Images with their licence and author: the only ones offered for new content."""
    return images.exclude(pk__in=unclassified(images).values("pk"))


def images_needing_review() -> QuerySet[MediaImage]:
    """The staff review list: unclassified images, oldest first."""
    return unclassified(MediaImage.objects.all()).order_by("created_at", "pk")


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


# --- Transactional email (ARC.16, docs/dev/transactional-email.md)

EMAIL_MODEL = "core.outboxemail"
SUPPRESSION_MODEL = "core.emailsuppression"
EMAIL_SUPPRESSED = "core.email.suppressed"
EMAIL_PARTS = ("subject.txt", "body.txt", "body.html")
_TEMPLATE_KEY = re.compile(r"[a-z0-9_]+(\.[a-z0-9_]+)+")
_FEEDBACK_EVENTS = {
    mail.HARD_BOUNCE: OutboxEmailEvent.Kind.BOUNCED,
    mail.SOFT_BOUNCE: OutboxEmailEvent.Kind.SOFT_BOUNCED,
    mail.COMPLAINT: OutboxEmailEvent.Kind.COMPLAINED,
}


class EmailRetryScheduled(Exception):
    """A transient failure is recorded and committed; the task queue retries the send job."""


@functools.cache
def _sender_for(path: str) -> mail.EmailSender:
    return cast("mail.EmailSender", import_string(path)())


def email_sender() -> mail.EmailSender:
    """The configured backend (`settings.EMAIL_SENDER`), one instance per process."""
    return _sender_for(settings.EMAIL_SENDER)


def feedback_parser() -> mail.FeedbackParser | None:
    """The webhook's parser, or None while no webhook secret is configured (webhook off)."""
    secret = settings.EMAIL_WEBHOOK_SECRET
    if not secret:
        return None
    return cast("mail.FeedbackParser", import_string(settings.EMAIL_FEEDBACK_PARSER)(secret))


def email_language(language: str) -> str:
    """`language` when it is configured, else the source language (EN, spec 09 fallback)."""
    codes = {code for code, _ in settings.LANGUAGES}
    return language if language in codes else settings.LANGUAGE_CODE


def _email_templates(template_key: str, language: str) -> list[Any]:
    """Subject, text and HTML templates; a per-language file wins over the gettext one."""
    if not _TEMPLATE_KEY.fullmatch(template_key):
        raise ValueError(f"invalid email template key {template_key!r} (expected app.name)")
    base = "email/" + template_key.replace(".", "/")
    try:
        return [
            select_template([f"{base}/{language}/{part}", f"{base}/{part}"]) for part in EMAIL_PARTS
        ]
    except TemplateDoesNotExist as exc:
        raise ValueError(f"email template {template_key!r} is incomplete: {exc}") from exc


def _render_plain(template: Any, values: Mapping[str, Any]) -> str:
    # Plain text is not HTML: render without autoescaping (the HTML part is escaped).
    return str(template.template.render(Context(dict(values), autoescape=False)))


def render_email(
    template_key: str, language: str, context: Mapping[str, Any]
) -> mail.RenderedEmail:
    """Render `email/<app>/<name>/{subject.txt, body.txt, body.html}` in `language`.

    Texts are translated with gettext; a missing translation shows the EN source text, and a
    language that is not configured renders in EN. The context gets `email_language`.
    """
    language = email_language(language)
    subject, text, html = _email_templates(template_key, language)
    values = {**context, "email_language": language}
    with translation.override(language):
        return mail.RenderedEmail(
            subject=" ".join(_render_plain(subject, values).split()),
            text_body=_render_plain(text, values).strip() + "\n",
            html_body=html.render(values),
        )


def is_suppressed(address: str) -> bool:
    return EmailSuppression.objects.filter(address=mail.normalise_address(address)).exists()


def email_snapshot(email: OutboxEmail) -> dict[str, Any]:
    """What change reports and the history show of an email: never its context."""
    return {
        "recipient": email.recipient,
        "template_key": email.template_key,
        "language": email.language,
        "idempotency_key": email.idempotency_key,
        "status": email.status,
        "attempts": email.attempts,
        "provider_message_id": email.provider_message_id,
    }


def _email_event(email: OutboxEmail, status: str, detail: str = "") -> None:
    OutboxEmailEvent.objects.create(
        email=email, status=status, attempt=email.attempts, detail=detail[:2000]
    )


def enqueue_email(
    *,
    recipient: str,
    template_key: str,
    language: str,
    context: Mapping[str, Any],
    idempotency_key: str | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Put a transactional email in the outbox and queue its send job, in the caller's
    transaction (a rollback drops both).

    `context` is stored as a snapshot (JSON, encrypted at rest), so the email shows the values
    of the moment it was requested. An email to a suppressed address is stored as `suppressed`
    and never sent. With `idempotency_key`, a second request with the same key changes nothing.
    Raises ValueError for an unknown or incomplete template.
    """
    from . import tasks  # tasks imports services

    language = email_language(language)
    _email_templates(template_key, language)
    payload = json.dumps(to_jsonable(context), sort_keys=True)
    key = idempotency_key or None
    if key is not None and OutboxEmail.objects.filter(idempotency_key=key).exists():
        return ChangeSet()
    suppressed = is_suppressed(recipient)
    email = OutboxEmail(
        recipient=recipient.strip(),
        template_key=template_key,
        language=language,
        context=payload,
        idempotency_key=key,
        status=OutboxEmail.Status.SUPPRESSED if suppressed else OutboxEmail.Status.QUEUED,
    )
    changeset = ChangeSet()
    changeset.create(EMAIL_MODEL, str(email.public_id), email_snapshot(email))
    if dry_run:
        return changeset
    try:
        with transaction.atomic():
            email.save()
            _email_event(email, email.status, "address suppressed" if suppressed else "")
            if not suppressed:
                tasks.enqueue(tasks.send_email, email_id=str(email.public_id))
    except IntegrityError:
        if key is not None and OutboxEmail.objects.filter(idempotency_key=key).exists():
            return ChangeSet()  # a concurrent request with the same key won
        raise
    return changeset


def _attempt(email: OutboxEmail, *, dry_run: bool) -> str:
    """Try to send `email`, updating its fields; return the history detail."""
    status = OutboxEmail.Status
    if is_suppressed(email.recipient):
        email.status = status.SUPPRESSED
        return "address suppressed"
    try:
        rendered = render_email(email.template_key, email.language, email.context_data)
    except ValueError as exc:
        email.status, email.last_error = status.FAILED, str(exc)
        return email.last_error
    violations = mail.html_violations(rendered.html_body)
    if violations:
        email.status = status.FAILED
        email.last_error = "unsafe HTML: " + "; ".join(violations)
        return email.last_error
    if dry_run:
        email.status = status.SENT
        return ""
    email.attempts += 1
    outgoing = mail.OutgoingEmail(
        recipient=email.recipient,
        from_address=settings.DEFAULT_FROM_EMAIL,
        subject=rendered.subject,
        text_body=rendered.text_body,
        html_body=rendered.html_body,
        idempotency_key=str(email.public_id),
    )
    now = timezone.now()
    try:
        result = email_sender().send(outgoing)
    except mail.PermanentSendError as exc:
        email.status, email.next_attempt_at = status.FAILED, None
        email.last_error = str(exc) or type(exc).__name__
    except Exception as exc:  # transient, or an unexpected provider error: retry
        if not isinstance(exc, mail.TransientSendError):
            logger.exception("unexpected error sending outbox email %s", email.public_id)
        delay = mail.retry_delay(email.attempts)
        email.last_error = str(exc) or type(exc).__name__
        if delay is None:
            email.status, email.next_attempt_at = status.FAILED, None
        else:
            email.status = status.RETRYING
            email.next_attempt_at = now + dt.timedelta(seconds=delay)
    else:
        email.status, email.sent_at = status.SENT, now
        email.provider_message_id = result.provider_message_id
        email.next_attempt_at, email.last_error = None, ""
        return ""
    return email.last_error


def deliver_email(email_id: str | uuid.UUID, *, dry_run: bool = False) -> ChangeSet:
    """Send one outbox email (the `core.send_email` job); safe to call more than once.

    An email that is already sent, failed or suppressed is left alone, and the row is locked
    while it is sent, so two runs never send it twice. The provider also gets the email's public
    id as idempotency key, for the case of a crash after it accepted the email. A transient
    failure is recorded and committed, then `EmailRetryScheduled` asks the queue to retry; after
    the last retry, or on a permanent failure, the email is `failed` and nothing is raised.
    Dry-run renders and checks the email without sending it.
    """
    with transaction.atomic():
        email = OutboxEmail.objects.select_for_update().get(public_id=email_id)
        if email.status in OutboxEmail.FINAL:
            return ChangeSet()
        before = email_snapshot(email)
        detail = _attempt(email, dry_run=dry_run)
        changeset = ChangeSet()
        changeset.update(EMAIL_MODEL, str(email.public_id), before, email_snapshot(email))
        if dry_run:
            return changeset
        email.save()
        _email_event(email, email.status, detail)
    if email.status == OutboxEmail.Status.RETRYING:
        raise EmailRetryScheduled(f"email {email.public_id}: retry at {email.next_attempt_at}")
    return changeset


def suppression_snapshot(suppression: EmailSuppression) -> dict[str, Any]:
    return {
        "address": suppression.address,
        "reason": suppression.reason,
        "detail": suppression.detail,
    }


def record_email_feedback(
    event: mail.FeedbackEvent, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Record a bounce or complaint from the provider (webhook, ARC.19 adapter).

    The event goes into the history of the email it names, when known. A hard bounce or a
    complaint suppresses the address (audited): later emails to it are not sent. A soft bounce
    is history only.
    """
    address = mail.normalise_address(event.recipient)
    email = (
        OutboxEmail.objects.filter(provider_message_id=event.provider_message_id).first()
        if event.provider_message_id
        else None
    )
    changeset = ChangeSet()
    suppression: EmailSuppression | None = None
    if event.kind in mail.SUPPRESSING_KINDS and not is_suppressed(address):
        suppression = EmailSuppression(address=address, reason=event.kind, detail=event.detail)
        after = {**suppression_snapshot(suppression), "message": event.provider_message_id}
        changeset.create(SUPPRESSION_MODEL, str(suppression.public_id), after)
    if dry_run:
        return changeset
    with transaction.atomic():
        if email is not None:
            _email_event(email, _FEEDBACK_EVENTS[event.kind], event.detail)
        if suppression is not None:
            try:
                run_mutation(
                    changeset, suppression.save, ctx=ctx, action=EMAIL_SUPPRESSED, dry_run=False
                )
            except IntegrityError:  # a concurrent report suppressed it first
                return ChangeSet()
    return changeset
