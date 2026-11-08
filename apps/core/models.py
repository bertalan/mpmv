"""Core models: the abstract base, translation and versioned-configuration models, the
append-only audit log, the platform operator, the feature flags, the shared image model and its
licence allow list, the transactional email outbox."""

import json
import uuid
from typing import Any, ClassVar, Self

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import ArrayField, DateTimeRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from wagtail.images.models import AbstractImage, AbstractRendition, Image

from . import flags, translations
from .countries import default_allowed_vendor_countries
from .fields import CountryField, EncryptedTextField, LanguageField
from .image_licences import (
    Credit,
    LicenceTerms,
    credit_for,
    metadata_errors,
    restricted_licence_error,
)

# The dedicated platform owner permission (spec 02 roles, CMP.13): never implied by staff or
# admin access, superuser status included. Granted as `core.platform_owner`, directly or through a
# group, and checked by `services.is_platform_owner`, not `has_perm` (true for any superuser).
PLATFORM_OWNER_CODENAME = "platform_owner"


class BaseModel(models.Model):
    """Base for domain models: bigint PK (default), UUID `public_id` for APIs, timestamps."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TranslationStatus(models.TextChoices):
    MACHINE = translations.MACHINE, "Machine draft (needs review)"
    REVIEWED = translations.REVIEWED, "Reviewed"
    PUBLISHED = translations.PUBLISHED, "Published"


class TranslationModel(BaseModel):
    """Base for every explicit translation table (`*Translation`, spec 09 §9.2, I18N.2).

    One row per translated entity and language holds that language's texts; the row in the
    source language (settings.LANGUAGE_CODE, EN) is the source of the others.

    Reuse contract (checked by `apps.core.checks`, so catalog tables follow it too):

    - set `translation_of` to the name of the ForeignKey to the translated entity, and
      `translated_fields` to the names of its CharField/TextField texts;
    - declare `class Meta(TranslationModel.Meta)`, keep `*TranslationModel.Meta.constraints` and
      add a `UniqueConstraint` on (`translation_of`, "language");
    - read texts for visitors with `resolve_translation(entity.<related_name>.all(), language)`,
      never by filtering on `language` alone: it applies the published-only rule and the
      explicit EN fallback.

    Workflow: rows start as `machine` drafts. Saving the source row stores the hash of its texts
    in `source_hash`; when that hash changes, the reviewed and published rows in other languages
    go back to `machine` ("needs review") and keep the hash they were made from, so a reviewer
    can tell they are stale. Other rows record the source hash when they are created and when
    they leave `machine` (a review is against the current source). Queryset `update()` bypasses
    all this: change texts through `save()`.
    """

    Status = TranslationStatus

    translation_of: ClassVar[str] = ""
    translated_fields: ClassVar[tuple[str, ...]] = ()

    language = LanguageField()
    status = models.CharField(
        max_length=16, choices=TranslationStatus.choices, default=TranslationStatus.MACHINE
    )
    source_hash = models.CharField(
        max_length=64,
        blank=True,
        editable=False,
        help_text="Hash of the source-language texts this row was made or reviewed from.",
    )

    class Meta:
        abstract = True
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(status__in=translations.STATUSES),
                name="%(app_label)s_%(class)s_status_valid",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        with transaction.atomic():
            stored = type(self)._default_manager.filter(pk=self.pk)
            previous = stored.values("status", "source_hash").first() if self.pk else None
            source_changed = False
            if self.is_source:
                self.source_hash = translations.source_hash(self.source_values())
                source_changed = previous is None or previous["source_hash"] != self.source_hash
            elif (previous is None and not self.source_hash) or (
                previous is not None
                and previous["status"] == TranslationStatus.MACHINE
                and self.status != TranslationStatus.MACHINE
            ):
                self.source_hash = self.current_source_hash() or ""
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "source_hash"}
            super().save(*args, **kwargs)
            if source_changed:
                self.mark_stale_translations()

    @property
    def is_source(self) -> bool:
        return bool(self.language == translations.source_language())

    def source_values(self) -> dict[str, str]:
        """The translated texts of this row, by field name."""
        return {name: getattr(self, name) for name in self.translated_fields}

    def family(self) -> "models.QuerySet[Self]":
        """Every row (all languages, this one included) of the same translated entity."""
        parent = self._meta.get_field(self.translation_of)
        if not isinstance(parent, models.ForeignKey):
            raise TypeError(f"{type(self).__name__}.translation_of must name a ForeignKey")
        attname = parent.attname
        return type(self)._default_manager.filter(**{attname: getattr(self, attname)})

    def current_source_hash(self) -> str | None:
        """The hash of the source texts as they are now; None when there is no source row."""
        if self.is_source:
            return translations.source_hash(self.source_values())
        source = translations.source_language()
        stored: str | None = (
            self.family().filter(language=source).values_list("source_hash", flat=True).first()
        )
        return stored

    @property
    def is_stale(self) -> bool:
        """True when the source texts changed after this translation was made or reviewed."""
        if self.is_source:
            return False
        current = self.current_source_hash()
        return current is not None and current != self.source_hash

    def mark_stale_translations(self) -> int:
        """Send the reviewed and published rows made from another source text back to review.

        Called by `save()` on the source row when its texts change; returns the rows changed.
        """
        if not self.is_source:
            return 0
        return (
            self.family()
            .exclude(pk=self.pk)
            .exclude(source_hash=self.source_hash)
            .exclude(status=TranslationStatus.MACHINE)
            .update(status=TranslationStatus.MACHINE, updated_at=timezone.now())
        )


def resolve_translation[TM: TranslationModel](
    rows: "models.QuerySet[TM]", language: str
) -> translations.Resolved[TM]:
    """The published row of `rows` in `language`, else the published source-language row.

    `rows` are the translations of one entity, e.g. `vendor.translations.all()`. The result's
    `is_fallback` tells templates to show the "not available in your language" notice.
    """
    candidates = rows.filter(
        status=TranslationStatus.PUBLISHED,
        language__in={language, translations.source_language()},
    )
    return translations.resolve(candidates, language)


class Heartbeat(BaseModel):
    """Proof that the worker and its scheduler are alive: one row per name, updated in place.

    Written by the periodic `core.heartbeat` job (ARC.9); monitoring alerts when `last_beat_at`
    gets old (OPS tasks).
    """

    name = models.CharField(max_length=100, unique=True)
    beats = models.PositiveBigIntegerField(default=0)
    last_beat_at = models.DateTimeField()
    scheduled_for = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.beats} beats, last {self.last_beat_at:%Y-%m-%d %H:%M})"


class Throttle(BaseModel):
    """Failure counter of one throttle key, for progressive lockout and rate limits (CORE.5).

    Kept in PostgreSQL, the only datastore (ADR-0001): every gunicorn worker and container sees
    the same counters, they survive restarts, and `select_for_update` makes each increment
    atomic. A process-local cache would let an attacker multiply the budget by the number of
    workers, and Django's database cache has no atomic increment. Only failures write here,
    so the load is small. Expired rows are purged nightly (`core.purge_throttles`).
    """

    key = models.CharField(max_length=320, unique=True)
    failures = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)

    def __str__(self) -> str:
        return f"{self.key} ({self.failures} failures)"


class AuditLog(models.Model):
    """Who changed what, before/after, and from where. Append-only (DB trigger + save guard).

    The actor is stored by id and label, not as a foreign key: audit rows must outlive users
    and must not cascade on deletion.
    """

    class Source(models.TextChoices):
        ADMIN = "admin"
        VENDOR = "vendor"
        API = "api"
        COMMAND = "command"
        TASK = "task"
        MCP = "mcp"
        SYSTEM = "system"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor_id = models.BigIntegerField(null=True, blank=True)
    actor_label = models.CharField(max_length=150)
    source = models.CharField(max_length=16, choices=Source.choices)
    action = models.CharField(max_length=100, db_index=True)
    target_model = models.CharField(max_length=100)
    target_key = models.CharField(max_length=200)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    # Public id of the vendor the entry belongs to, empty for platform-level entries (SEC.4).
    vendor_key = models.CharField(max_length=64, blank=True, default="", db_index=True)

    class Meta:
        indexes = [models.Index(fields=["target_model", "target_key"])]
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return (
            f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_label} {self.action} {self.target_key}"
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise PermissionError("AuditLog is append-only")
        super().save(*args, **kwargs)


class TsTzRange(models.Func):
    """PostgreSQL `tstzrange(lower, upper, '[)')`; a NULL upper bound means open-ended."""

    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class Versioned(BaseModel):
    """A row of versioned configuration: valid over `[valid_from, valid_to)`, never edited in
    place (spec 01 "History", `apps.core.validity`).

    A change is a new row valid from a date, and the service that adds it closes the current
    open-ended row: `valid_to` (with `updated_at`) is the only thing an existing row may write.
    Declare `valid_period_check(...)` among the subclass's constraints.
    """

    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True, editable=False)

    # The only fields an existing row may write: closing its validity period.
    CLOSING_FIELDS: ClassVar[frozenset[str]] = frozenset({"valid_to", "updated_at"})

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        update_fields = kwargs.get("update_fields")
        if self.pk is not None and (
            update_fields is None
            or "valid_to" not in update_fields
            or not self.CLOSING_FIELDS.issuperset(update_fields)
        ):
            raise PermissionError(
                f"{type(self).__name__} rows are never edited in place; add a new row instead"
            )
        super().save(*args, **kwargs)


def valid_period_check(name: str) -> models.CheckConstraint:
    """The database rule of a `Versioned` row: an end, when set, comes after the start."""
    return models.CheckConstraint(
        condition=models.Q(valid_to__isnull=True) | models.Q(valid_to__gt=models.F("valid_from")),
        name=name,
    )


class PlatformOperator(Versioned):
    """The legal entity running the marketplace, as versioned configuration (ARC.7).

    Its country, VAT position, e-invoicing channel and DAC7 authority drive tax, invoicing and
    reporting: code reads them with `apps.core.services.current_operator()` and never hard-codes
    a platform country. Rows are history: a change (e.g. a relocation to another EU country) is
    a new row through `configure_platform_operator`, which closes the previous one. Only
    `valid_to` may be written on an existing row, to close it. The countries where vendors may
    be established change only through `set_allowed_vendor_countries` (CMP.13).
    """

    class TaxRegime(models.TextChoices):
        STANDARD = "standard", "Standard VAT regime"
        SMALL_BUSINESS = "small_business", "Small-business / flat-rate scheme (no VAT charged)"

    class CommissionVatMode(models.TextChoices):
        NONE = "none", "No VAT on commissions and fees"
        STANDARD = "standard", "VAT on commissions and fees under the general rules"

    class EInvoiceChannel(models.TextChoices):
        NONE = "none", "No structured e-invoicing"
        SDI = "sdi", "SDI (FatturaPA)"
        PEPPOL = "peppol", "Peppol"

    legal_name = models.CharField(max_length=200)
    country = CountryField(help_text="Country of establishment (ISO 3166-1 alpha-2).")
    vat_number = models.CharField("VAT number", max_length=32, help_text="With the country prefix.")
    tax_regime = models.CharField(max_length=32, choices=TaxRegime.choices)
    commission_vat_mode = models.CharField(
        "commission VAT mode", max_length=16, choices=CommissionVatMode.choices
    )
    einvoice_channel = models.CharField(
        "e-invoice channel", max_length=16, choices=EInvoiceChannel.choices
    )
    dac7_authority_country = CountryField(
        "DAC7 reporting authority country",
        help_text="Member state whose authority receives the DAC7 report.",
    )
    # D-21, CMP.13: changed only by a platform owner through
    # `apps.core.services.set_allowed_vendor_countries`; other new versions carry it over.
    allowed_vendor_countries = ArrayField(
        CountryField(),
        default=default_allowed_vendor_countries,
        editable=False,
        verbose_name="allowed vendor establishment countries",
        help_text="A vendor is activated only when its registered office or permanent "
        "establishment is in one of these countries.",
    )

    class Meta:
        ordering = ["-valid_from"]
        permissions = [
            (
                PLATFORM_OWNER_CODENAME,
                "Platform owner: owner-only settings, such as the allowed vendor countries",
            )
        ]
        constraints = [
            valid_period_check("core_platformoperator_valid_to_after_valid_from"),
            models.CheckConstraint(
                condition=models.Q(allowed_vendor_countries__len__gt=0),
                name="core_platformoperator_allowed_vendor_countries_not_empty",
            ),
            ExclusionConstraint(
                name="core_platformoperator_no_overlap",
                expressions=[(TsTzRange("valid_from", "valid_to"), RangeOperators.OVERLAPS)],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.legal_name} ({self.country}) from {self.valid_from:%Y-%m-%d}"


# Per-vendor overrides are vendor-owned rows (CORE.3) in apps.vendors, which core cannot import:
# their ForeignKey to FeatureFlag uses this related name, and core reads them through it.
VENDOR_OVERRIDES = "vendor_overrides"


class FeatureFlag(BaseModel):
    """The staff switch of one registered flag (ARC.8, `apps.core.flags`).

    `state` is `default` (this environment's default, `settings.FEATURE_FLAGS`, else the code
    default), `on` or `off`. Per-vendor overrides are vendor-owned rows in the vendors app
    (`VendorFeatureFlag`, related name `vendor_overrides`). Rows are never deleted: set the state
    back to `default` instead. Write through `apps.core.services.save_flag` (audited); read with
    `flag_enabled`.
    """

    class State(models.TextChoices):
        DEFAULT = flags.DEFAULT, "Environment default"
        ON = flags.ON, "On"
        OFF = flags.OFF, "Off"

    name = models.CharField(max_length=flags.NAME_MAX_LENGTH, unique=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.DEFAULT)
    note = models.CharField(
        max_length=200, blank=True, help_text="Why it was switched, e.g. the incident reference."
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=flags.STATES), name="core_featureflag_state_valid"
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        if not flags.is_registered(self.name):
            raise ValidationError({"name": f"{self.name!r} is not a registered feature flag."})

    @property
    def spec(self) -> flags.FlagSpec | None:
        """The flag's declaration in code; None for a row left by removed code."""
        return flags.spec_of(self.name) if flags.is_registered(self.name) else None


class ImageLicence(BaseModel):
    """An entry of the image licence allow list (CMS.8, spec 10 §10.2), edited by staff.

    Written through `services.save_image_licence` (audited). Non-commercial and
    no-derivatives licences are refused. Entries are never deleted while an image uses them.
    """

    code = models.CharField(max_length=40, unique=True, help_text="For example CC-BY-4.0.")
    name = models.CharField(max_length=100, help_text="Shown in credits, e.g. CC BY 4.0.")
    url = models.URLField("licence URL", blank=True)
    attribution_required = models.BooleanField(
        default=False, help_text="Images under this licence show a credit."
    )
    source_required = models.BooleanField(
        default=True, help_text="Images under this licence need the URL they come from."
    )
    permission_required = models.BooleanField(
        default=False, help_text="Images need a reference to a stored written permission."
    )

    class Meta:
        ordering = ("code",)
        verbose_name = "image licence"

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        error = restricted_licence_error(self.code, self.name, self.url)
        if error is not None:
            raise ValidationError({"code": error})

    @property
    def terms(self) -> LicenceTerms:
        return LicenceTerms(
            code=self.code,
            name=self.name,
            url=self.url,
            attribution_required=self.attribution_required,
            source_required=self.source_required,
            permission_required=self.permission_required,
        )


class GeocodeCacheEntry(BaseModel):
    """The candidates of one geocoding lookup, by normalised address and language (EVT.12).

    `key` is the SHA-256 of the normalised address (country, postcode, city, street, case folded,
    whitespace collapsed); `normalised_address` keeps the text it was made from. Only the venue
    address typed in an event form is ever looked up, never a member's address. Written by
    `apps.core.geocoding.services.geocode_address`; expired rows are purged nightly.
    """

    key = models.CharField(max_length=64)
    language = models.CharField(max_length=16)
    normalised_address = models.TextField()
    provider = models.CharField(max_length=32)
    candidates = models.JSONField(default=list)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["key", "language"], name="core_geocode_cache_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.normalised_address} [{self.language}] via {self.provider}"


class GeocoderState(BaseModel):
    """Shared state of one geocoding provider (EVT.12): the limiter's next free slot and the
    circuit breaker. In PostgreSQL so that every process sees the same budget (no Redis, spec
    01); `select_for_update` serialises the slot reservations."""

    provider = models.CharField(max_length=32, unique=True)
    next_slot_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveIntegerField(default=0)  # consecutive failures or rate limits
    open_until = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.provider} ({self.failures} failures)"


class MediaImage(AbstractImage):  # type: ignore[misc]  # Wagtail is untyped
    """The site's image model (`WAGTAILIMAGES_IMAGE_MODEL`, spec 10 §10.2).

    Shared by CMS pages, products (CAT.4), vendor logos and banners. It is the image model from
    the first image on (I18N.3), so no image ever has to move between models.

    CMS.8: every image names its licence (from the `ImageLicence` allow list) and author, plus
    the source URL or permission reference its licence requires; `clean` refuses an image
    without them, in the Wagtail admin and in `services.save_image`. The columns accept empty
    values so that images migrated without known metadata can exist: they are listed for
    review and cannot be chosen for new content until classified.
    """

    licence = models.ForeignKey(
        ImageLicence,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="From the allow list: only licences that allow commercial use.",
    )
    author = models.CharField(
        max_length=200, blank=True, help_text="The author or rights holder to credit."
    )
    source_url = models.URLField(
        "source URL",
        max_length=500,
        blank=True,
        help_text="Where the image comes from; not needed for own work or vendor images.",
    )
    credit_text = models.CharField(
        max_length=250, blank=True, help_text="Optional: replaces the generated credit line."
    )
    permission_ref = models.CharField(
        "permission reference",
        max_length=200,
        blank=True,
        help_text="Written permission: reference of the stored permission document.",
    )

    METADATA_FIELDS = ("licence", "author", "source_url", "credit_text", "permission_ref")
    admin_form_fields = (*Image.admin_form_fields, *METADATA_FIELDS)

    class Meta(AbstractImage.Meta):  # type: ignore[misc]
        verbose_name = "image"
        verbose_name_plural = "images"

    def _terms(self) -> LicenceTerms | None:
        licence = self.licence if self.licence_id is not None else None
        return None if licence is None else licence.terms

    def clean(self) -> None:
        super().clean()
        errors = metadata_errors(
            self._terms(),
            author=self.author,
            source_url=self.source_url,
            permission_ref=self.permission_ref,
        )
        if errors:
            raise ValidationError(errors)

    @property
    def needs_review(self) -> bool:
        """Migrated without its licence or author: to classify before any new use."""
        return self.licence_id is None or not self.author.strip()

    @property
    def credit(self) -> Credit | None:
        """The credit to show with the image; None when its licence needs no attribution."""
        return credit_for(
            self._terms(),
            author=self.author,
            source_url=self.source_url,
            credit_text=self.credit_text,
        )


class MediaImageRendition(AbstractRendition):  # type: ignore[misc]
    """A resized copy of a `MediaImage`, made on first use by templates."""

    image = models.ForeignKey(MediaImage, on_delete=models.CASCADE, related_name="renditions")

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["image", "filter_spec", "focal_point_key"],
                name="core_one_rendition_per_filter",
            ),
        ]


# --- Transactional email (ARC.16)


class OutboxEmail(BaseModel):
    """One transactional email, from the request to send it until the provider accepts it.

    Written by `apps.core.services.enqueue_email` (which queues the `core.send_email` job in the
    same transaction) and `deliver_email`; every status change adds an `OutboxEmailEvent`. The
    context is a snapshot of the values the templates need, encrypted at rest: it can hold a
    one-time code or personal data. `idempotency_key` (optional, chosen by the caller, e.g.
    `order:<public id>:confirmation`) makes a repeated request a no-op.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RETRYING = "retrying", "Waiting for a retry"
        SENT = "sent", "Accepted by the provider"
        FAILED = "failed", "Failed"
        SUPPRESSED = "suppressed", "Not sent: address suppressed"

    #: No further send attempt for these.
    FINAL: ClassVar[frozenset[str]] = frozenset({Status.SENT, Status.FAILED, Status.SUPPRESSED})

    recipient = models.EmailField(max_length=254)
    template_key = models.CharField(max_length=100)
    language = LanguageField()
    context = EncryptedTextField(blank=True, help_text="JSON snapshot of the template context.")
    idempotency_key = models.CharField(max_length=200, unique=True, null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    provider_message_id = models.CharField(max_length=200, blank=True, db_index=True)
    last_error = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["queued", "retrying", "sent", "failed", "suppressed"]
                ),
                name="core_outboxemail_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.template_key} to {self.recipient} ({self.status})"

    @property
    def context_data(self) -> dict[str, Any]:
        data = json.loads(self.context or "{}")
        return data if isinstance(data, dict) else {}


class OutboxEmailEvent(models.Model):
    """Append-only history of an outbox email: each status change and provider feedback.

    The database refuses updates and deletes (trigger), like the audit log.
    """

    class Kind(models.TextChoices):
        QUEUED = "queued", "Queued"
        RETRYING = "retrying", "Transient failure, retry scheduled"
        SENT = "sent", "Accepted by the provider"
        FAILED = "failed", "Failed"
        SUPPRESSED = "suppressed", "Not sent: address suppressed"
        BOUNCED = "bounced", "Hard bounce reported"
        SOFT_BOUNCED = "soft_bounced", "Soft bounce reported"
        COMPLAINED = "complained", "Complaint reported"

    email = models.ForeignKey(OutboxEmail, on_delete=models.PROTECT, related_name="events")
    status = models.CharField(max_length=16, choices=Kind.choices)
    attempt = models.PositiveIntegerField(default=0)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["pk"]

    def __str__(self) -> str:
        return f"{self.status} (attempt {self.attempt})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise PermissionError("OutboxEmailEvent is append-only")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise PermissionError("OutboxEmailEvent is append-only")


class EmailSuppression(BaseModel):
    """An address that receives no more email: it hard-bounced or its owner complained.

    Written by `apps.core.services.record_email_feedback` (audited). The address is stored
    normalised (`apps.core.mail.normalise_address`).
    """

    class Reason(models.TextChoices):
        HARD_BOUNCE = "hard_bounce", "Hard bounce"
        COMPLAINT = "complaint", "Complaint"

    address = models.CharField(max_length=254, unique=True)
    reason = models.CharField(max_length=16, choices=Reason.choices)
    detail = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(reason__in=["hard_bounce", "complaint"]),
                name="core_emailsuppression_reason_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.address} ({self.reason})"
