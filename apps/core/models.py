"""Core models: the abstract base and translation models, the append-only audit log, the
platform operator, the feature flags."""

import uuid
from typing import Any, ClassVar, Self

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from . import flags, translations
from .fields import CountryField, LanguageField


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


class PlatformOperator(BaseModel):
    """The legal entity running the marketplace, as versioned configuration (ARC.7).

    Its country, VAT position, e-invoicing channel and DAC7 authority drive tax, invoicing and
    reporting: code reads them with `apps.core.services.current_operator()` and never hard-codes
    a platform country. Rows are history: a change (e.g. a relocation to another EU country) is
    a new row through `configure_platform_operator`, which closes the previous one. Only
    `valid_to` may be written on an existing row, to close it.
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
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True, editable=False)

    # The only fields an existing row may write: closing its validity period.
    CLOSING_FIELDS = frozenset({"valid_to", "updated_at"})

    class Meta:
        ordering = ["-valid_from"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(valid_to__isnull=True)
                | models.Q(valid_to__gt=models.F("valid_from")),
                name="core_platformoperator_valid_to_after_valid_from",
            ),
            ExclusionConstraint(
                name="core_platformoperator_no_overlap",
                expressions=[(TsTzRange("valid_from", "valid_to"), RangeOperators.OVERLAPS)],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.legal_name} ({self.country}) from {self.valid_from:%Y-%m-%d}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        update_fields = kwargs.get("update_fields")
        if self.pk is not None and (
            update_fields is None
            or "valid_to" not in update_fields
            or not self.CLOSING_FIELDS.issuperset(update_fields)
        ):
            raise PermissionError(
                "PlatformOperator rows are never edited in place; configure a new row instead"
            )
        super().save(*args, **kwargs)


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
