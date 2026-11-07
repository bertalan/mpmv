"""Core models: the abstract base model, the append-only audit log, the platform operator."""

import uuid
from typing import Any

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.db import models

from .fields import CountryField


class BaseModel(models.Model):
    """Base for domain models: bigint PK (default), UUID `public_id` for APIs, timestamps."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


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
