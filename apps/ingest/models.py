"""Supplier import staging (spec 08 §8.2, ING.1).

A `SupplierSource` is one vendor's feed; its `SourceItem`s are keyed by the vendor and the SKU
match key (`apps.catalog.engine.sku_match_key`) and point at catalogue products by internal
identity (FK, never by SKU text). Observations are append-only versions of what the supplier
published; drafts are what the pipeline extracted from them. An `ImportRun` records one pass
with its counters, a `ReviewItem` a case a person must decide, and `MappingTable` the vendor's
(or the platform's) translation of supplier terms into catalogue objects or values.

Everything is vendor-owned (CORE.3) through `vendor_scope`. Rows are written by the services.
"""

import datetime as dt
from typing import Any, ClassVar

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models

from apps.catalog.models import Product
from apps.core.models import BaseModel
from apps.vendors.models import Vendor
from apps.vendors.scoping import VendorScopedManager

from . import engine

__all__ = [
    "ImportRun",
    "MappingTable",
    "MigrationChoice",
    "MigrationRun",
    "ProductDraft",
    "ReviewItem",
    "SourceItem",
    "SourceObservation",
    "SupplierSource",
]


def _in(field: str, choices: type[models.TextChoices], name: str) -> models.CheckConstraint:
    return models.CheckConstraint(condition=models.Q(**{f"{field}__in": choices.values}), name=name)


class DraftStatus(models.TextChoices):
    NEW = "new", "New"
    READY = "ready", "Ready"
    NEEDS_REVIEW = "needs_review", "Needs review"
    PUBLISHED = "published", "Published"
    REJECTED = "rejected", "Rejected"


class RunKind(models.TextChoices):
    SCRAPE = "scrape", "Scrape"
    STOCK = "stock", "Stock"
    PRICE = "price", "Price"
    IMAGES = "images", "Images"
    PUBLISH = "publish", "Publish"


class RunStatus(models.TextChoices):
    QUEUED = engine.QUEUED, "Queued"
    RUNNING = engine.RUNNING, "Running"
    SUCCEEDED = engine.SUCCEEDED, "Succeeded"
    FAILED = engine.FAILED, "Failed"
    CANCELLED = engine.CANCELLED, "Cancelled"


class ReviewKind(models.TextChoices):
    PRICE_JUMP = "price_jump", "Price jump"
    SALE = "sale", "Sale"
    IDENTITY_CHANGE = "identity_change", "Identity change"
    STALE = "stale", "Stale"
    DUPLICATE_SKU = "duplicate_sku", "Ambiguous SKU match"


class ReviewDecision(models.TextChoices):
    PENDING = engine.PENDING, "Pending"
    ACCEPTED = engine.ACCEPTED, "Accepted"
    REJECTED = engine.REJECTED, "Rejected"
    DEFERRED = engine.DEFERRED, "Deferred"


class MappingKind(models.TextChoices):
    CATEGORY = "category", "Category"
    TERM = "term", "Taxonomy term"
    COLOR = "color", "Colour"
    MATERIAL = "material", "Material"
    BRAND = "brand", "Brand"
    OTHER = "other", "Other"


class SupplierSource(BaseModel):
    """One vendor's supplier feed and how to read it. Runs are dry unless `write_enabled`."""

    vendor_scope: ClassVar[str] = "vendor"

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="supplier_sources")
    code = models.SlugField(max_length=60, help_text="Short name, unique for the vendor.")
    adapter_code = models.CharField(max_length=40)
    config = models.JSONField(default=dict, blank=True)
    schedule = models.CharField(max_length=100, blank=True, help_text="Cron expression.")
    is_active = models.BooleanField(default=True)
    write_enabled = models.BooleanField(
        default=False, help_text="Without this flag every run of the source is a dry run."
    )

    objects = VendorScopedManager()

    class Meta:
        ordering = ["vendor", "code"]
        constraints = [
            models.UniqueConstraint(fields=["vendor", "code"], name="ingest_source_vendor_code"),
        ]

    def __str__(self) -> str:
        return f"{self.vendor} / {self.code}"


class SourceItem(BaseModel):
    """One supplier item. `source_url` is its identity in the source, `sku_key` its match key."""

    vendor_scope: ClassVar[str] = "vendor"

    source = models.ForeignKey(SupplierSource, on_delete=models.CASCADE, related_name="items")
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="source_items")
    source_url = models.CharField(max_length=500)
    aliases = models.JSONField(default=list, blank=True, help_text="Other URLs of the item.")
    sku = models.CharField(max_length=100, blank=True)
    sku_key = models.CharField(max_length=64, blank=True, editable=False)
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="source_items"
    )
    last_seen_at = models.DateTimeField(null=True, blank=True)
    missing_since = models.DateTimeField(null=True, blank=True)

    objects = VendorScopedManager()

    class Meta:
        ordering = ["vendor", "sku_key"]
        constraints = [
            models.UniqueConstraint(fields=["source", "source_url"], name="ingest_item_source_url"),
        ]

    def __str__(self) -> str:
        return self.sku or self.source_url

    def clean(self) -> None:
        if self.source_id and self.vendor_id and self.source.vendor_id != self.vendor_id:
            raise ValidationError({"vendor": "An item belongs to the vendor of its source."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.sku_key = engine.mapping_key(self.sku)
        fields = kwargs.get("update_fields")
        if fields is not None and "sku" in fields:
            kwargs["update_fields"] = {*fields, "sku_key"}
        super().save(*args, **kwargs)


class SourceObservation(BaseModel):
    """What the supplier published at one time; a payload version is stored once per item."""

    vendor_scope: ClassVar[str] = "item__vendor"

    item = models.ForeignKey(SourceItem, on_delete=models.CASCADE, related_name="observations")
    observed_at = models.DateTimeField()
    payload = models.JSONField()
    payload_hash = models.CharField(max_length=64)

    objects = VendorScopedManager()

    class Meta:
        ordering = ["item", "-observed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "payload_hash"], name="ingest_observation_item_hash"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.item} @ {self.observed_at:%Y-%m-%d %H:%M}"


class ProductDraft(BaseModel):
    """What the pipeline extracted (and enriched, per language) from an item."""

    Status = DraftStatus

    vendor_scope: ClassVar[str] = "item__vendor"

    item = models.OneToOneField(SourceItem, on_delete=models.CASCADE, related_name="draft")
    extracted = models.JSONField(default=dict, blank=True)
    enriched = models.JSONField(default=dict, blank=True, help_text="Texts per language.")
    status = models.CharField(max_length=20, choices=Status, default=Status.NEW)

    objects = VendorScopedManager()

    class Meta:
        constraints = [_in("status", DraftStatus, "ingest_draft_status_valid")]

    def __str__(self) -> str:
        return f"Draft of {self.item}"


class ImportRun(BaseModel):
    """One pass over a source. Starts queued and dry; the counters are totals of the pass."""

    Kind = RunKind

    Status = RunStatus

    vendor_scope: ClassVar[str] = "source__vendor"

    source = models.ForeignKey(SupplierSource, on_delete=models.CASCADE, related_name="runs")
    kind = models.CharField(max_length=20, choices=Kind)
    status = models.CharField(max_length=20, choices=Status, default=Status.QUEUED)
    dry_run = models.BooleanField(default=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    seen = models.PositiveIntegerField(default=0)
    created = models.PositiveIntegerField(default=0)
    updated = models.PositiveIntegerField(default=0)
    unchanged = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)
    log = models.TextField(blank=True)

    objects = VendorScopedManager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["source", "status"], name="ingest_run_source_status")]
        constraints = [
            _in("status", RunStatus, "ingest_run_status_valid"),
            _in("kind", RunKind, "ingest_run_kind_valid"),
        ]

    def __str__(self) -> str:
        return f"{self.source} {self.kind} ({self.status})"


class ReviewItem(BaseModel):
    """A case a person must decide. A blocking item keeps its run from succeeding."""

    Kind = ReviewKind

    Decision = ReviewDecision

    vendor_scope: ClassVar[str] = "run__source__vendor"

    run = models.ForeignKey(ImportRun, on_delete=models.CASCADE, related_name="review_items")
    item = models.ForeignKey(
        SourceItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="review_items"
    )
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="review_items"
    )
    kind = models.CharField(max_length=20, choices=Kind)
    blocking = models.BooleanField(default=False)
    payload = models.JSONField(default=dict, blank=True)
    decision = models.CharField(max_length=10, choices=Decision, default=Decision.PENDING)
    note = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    objects = VendorScopedManager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["run", "blocking", "decision"], name="ingest_review_run_block_dec")
        ]
        constraints = [
            _in("kind", ReviewKind, "ingest_review_kind_valid"),
            _in("decision", ReviewDecision, "ingest_review_decision_valid"),
            models.CheckConstraint(
                condition=models.Q(decision="pending") | models.Q(resolved_at__isnull=False),
                name="ingest_review_decided_has_time",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} ({self.decision})"

    def clean(self) -> None:
        vendor_id = self.run.source.vendor_id if self.run_id else None
        if self.item is not None and self.item.vendor_id != vendor_id:
            raise ValidationError({"item": "The item belongs to another vendor than the run."})
        if self.product is not None and self.product.vendor_id != vendor_id:
            raise ValidationError({"product": "The product belongs to another vendor."})


class MappingTable(BaseModel):
    """Translates a supplier term into a catalogue object or a plain value.

    `vendor` empty means a platform-wide entry; a vendor's own entry wins over it
    (`engine.resolve_mapping`). `key_norm` is the normalised key the database keeps unique.
    """

    Kind = MappingKind

    vendor_scope: ClassVar[str] = "vendor"

    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE, null=True, blank=True, related_name="mappings"
    )
    kind = models.CharField(max_length=20, choices=Kind)
    key = models.CharField(max_length=200)
    key_norm = models.CharField(max_length=200, editable=False)
    aliases = models.JSONField(default=list, blank=True)
    target_type = models.ForeignKey(
        ContentType, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    target_id = models.PositiveBigIntegerField(null=True, blank=True)
    target_value = models.CharField(max_length=200, blank=True)
    target = GenericForeignKey("target_type", "target_id")

    objects = VendorScopedManager()

    class Meta:
        ordering = ["vendor", "kind", "key_norm"]
        constraints = [
            _in("kind", MappingKind, "ingest_mapping_kind_valid"),
            models.UniqueConstraint(
                fields=["vendor", "kind", "key_norm"],
                condition=models.Q(vendor__isnull=False),
                name="ingest_mapping_vendor_key",
            ),
            models.UniqueConstraint(
                fields=["kind", "key_norm"],
                condition=models.Q(vendor__isnull=True),
                name="ingest_mapping_global_key",
            ),
            models.CheckConstraint(
                condition=~models.Q(target_value="")
                | (models.Q(target_type__isnull=False) & models.Q(target_id__isnull=False)),
                name="ingest_mapping_has_target",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}: {self.key}"

    def set_target(self, obj: models.Model) -> None:
        self.target_type = ContentType.objects.get_for_model(obj)
        self.target_id = obj.pk

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.key_norm = engine.mapping_key(self.key)
        super().save(*args, **kwargs)


class MigrationRun(BaseModel):
    """One planned migration cutover (MIG.16). Platform data: no vendor owns it.

    Vendors record their choices against a run; the choice is due 14 days before `cutover_at`.
    """

    label = models.SlugField(max_length=60, unique=True)
    cutover_at = models.DateTimeField()

    class Meta:
        ordering = ["cutover_at", "id"]

    def __str__(self) -> str:
        return self.label

    @property
    def choice_deadline(self) -> dt.datetime:
        return engine.choice_deadline(self.cutover_at)


class MigrationChoice(BaseModel):
    """What one vendor chose to migrate for one run (MIG.16, owner decision D-16).

    Append-only and versioned: a change adds a row with the next `version`; the current choice is
    the highest version. `period_months` null means all orders. `source` tells whether the vendor,
    a staff member on its behalf, or the deadline defaults recorded it. Every write goes through
    `services.record_choice`, which audits it with the previous value.
    """

    vendor_scope: ClassVar[str] = "vendor"

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="migration_choices")
    run = models.ForeignKey(MigrationRun, on_delete=models.PROTECT, related_name="choices")
    version = models.PositiveIntegerField()
    period_months = models.PositiveSmallIntegerField(null=True, blank=True)
    data_types = models.JSONField(default=list)
    include_unpublished = models.BooleanField(default=False)
    login_window_months = models.PositiveSmallIntegerField()
    source = models.CharField(max_length=10, choices=[(s, s) for s in engine.CHOICE_SOURCES])
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = VendorScopedManager()

    class Meta:
        ordering = ["vendor", "run", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "run", "version"], name="ingest_choice_vendor_run_version"
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="ingest_choice_version_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(period_months__isnull=True)
                | models.Q(period_months__in=engine.PERIOD_CHOICES),
                name="ingest_choice_period_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(login_window_months__in=engine.LOGIN_WINDOWS),
                name="ingest_choice_login_window_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(source__in=engine.CHOICE_SOURCES),
                name="ingest_choice_source_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vendor} / {self.run} v{self.version}"

    @property
    def values(self) -> engine.ChoiceValues:
        return engine.ChoiceValues(
            period_months=self.period_months,
            data_types=engine.normalise_data_types(list(self.data_types)),
            include_unpublished=self.include_unpublished,
            login_window_months=self.login_window_months,
        )
