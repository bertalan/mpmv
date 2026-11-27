"""Supplier import staging (spec 08 §8.2, ING.1).

A `SupplierSource` is one vendor's feed; its `SourceItem`s are keyed by the vendor and the SKU
match key (`apps.catalog.engine.sku_match_key`) and point at catalogue products by internal
identity (FK, never by SKU text). Observations are append-only versions of what the supplier
published; drafts are what the pipeline extracted from them. An `ImportRun` records one pass
with its counters, a `ReviewItem` a case a person must decide, and `MappingTable` the vendor's
(or the platform's) translation of supplier terms into catalogue objects or values.

Everything is vendor-owned (CORE.3) through `vendor_scope`. Rows are written by the services.
"""

from typing import Any, ClassVar

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product
from apps.core.models import BaseModel
from apps.vendors.models import Vendor
from apps.vendors.scoping import VendorScopedManager

from . import engine

__all__ = [
    "EnrichmentVersion",
    "ImportRun",
    "ItemChange",
    "ItemKind",
    "MappingTable",
    "ProductDraft",
    "ReviewItem",
    "SourceItem",
    "SourceMissing",
    "SourceObservation",
    "SourceState",
    "SourceStateVersion",
    "SourceVariation",
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
    TEXT_POLICY = "text_policy", "AI text outside the policy"


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
    PRODUCT_TYPE = "product_type", "Product type"
    COUNTRY = "country", "Country"
    OTHER = "other", "Other"


class SupplierSource(BaseModel):
    """One vendor's supplier feed and how to read it. Runs are dry unless `write_enabled`."""

    vendor_scope: ClassVar[str] = "vendor"

    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.CASCADE,
        related_name="supplier_sources",
        help_text=_("The vendor whose supplier feed this is."),
    )
    code = models.SlugField(
        max_length=60,
        help_text=_(
            "A short name without spaces for this feed. Two feeds of the same vendor cannot"
            " share it."
        ),
    )
    adapter_code = models.CharField(
        max_length=40,
        help_text=_(
            "Not used yet: saved for a future feature. "
            "The code of the reader that understands this supplier's feed."
        ),
    )
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            "Not used yet: saved for a future feature. "
            "Settings of the reader, as a JSON object. Leave '{}' if it needs none."
        ),
    )
    schedule = models.CharField(
        max_length=100,
        blank=True,
        help_text=_(
            "Not used yet: saved for a future feature. When the feed should be read, as a cron "
            "expression such as '0 3 * * *' (every day at 3:00). Leave it empty for none."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_(
            "Not used yet: saved for a future feature. "
            "Untick it to retire the source. Sources are never deleted."
        ),
    )
    write_enabled = models.BooleanField(
        default=False,
        help_text=_(
            "Tick it to allow runs that write. Without it, every run of this source is a "
            "dry run that changes nothing."
        ),
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

    source = models.ForeignKey(
        SupplierSource,
        on_delete=models.CASCADE,
        related_name="items",
        help_text=_("The supplier feed this item comes from."),
    )
    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.CASCADE,
        related_name="source_items",
        help_text=_("The vendor that owns the item."),
    )
    source_url = models.CharField(
        max_length=500,
        help_text=_(
            "The address of the item in the supplier's feed. It identifies the item in that feed."
        ),
    )
    aliases = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Other addresses the same item had in the feed, as a JSON list."),
    )
    sku = models.CharField(
        max_length=100,
        blank=True,
        help_text=_(
            "The supplier's code for the item. It is used to match the item to a product of"
            " the vendor."
        ),
    )
    sku_key = models.CharField(max_length=64, blank=True, editable=False)
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_items",
        help_text=_(
            "The product of the catalogue this item is matched to. Empty while it is not matched."
        ),
    )
    last_seen_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When a run last found the item in the feed.")
    )
    missing_since = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Since when the item is no longer in the feed. Empty while it is there."),
    )

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

    item = models.ForeignKey(
        SourceItem,
        on_delete=models.CASCADE,
        related_name="observations",
        help_text=_("The item this record is about."),
    )
    observed_at = models.DateTimeField(help_text=_("When the supplier's data was read."))
    payload = models.JSONField(
        help_text=_("What the supplier published for the item, as it was read.")
    )
    payload_hash = models.CharField(
        max_length=64,
        help_text=_("A fingerprint of the data. The same data is stored once per item."),
    )

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


def _stock_constraint(name: str) -> models.CheckConstraint:
    """Both stock columns null (unknown) or a coherent pair: out = 0 units, in = more than 0."""
    return models.CheckConstraint(
        condition=(
            models.Q(stock_status__isnull=True, stock_quantity__isnull=True)
            | models.Q(
                stock_status=engine.STOCK_OUT,
                stock_quantity=0,
                stock_status__isnull=False,
                stock_quantity__isnull=False,
            )
            | models.Q(
                stock_status=engine.STOCK_IN,
                stock_quantity__gt=0,
                stock_status__isnull=False,
                stock_quantity__isnull=False,
            )
        ),
        name=name,
    )


class ItemKind(models.TextChoices):
    SIMPLE = engine.SIMPLE, "Simple"
    VARIABLE = engine.VARIABLE, "Variable"


class SourceState(BaseModel):
    """The current observed state of an item: kind, native id, stock and the full parsed state.

    Written only by `services.record_source_state`. `version` grows with every semantic change;
    a repeat observation only moves `observed_at`. Stock is null while the supplier has not
    stated it explicitly (unknown is never zero)."""

    vendor_scope: ClassVar[str] = "item__vendor"

    Kind = ItemKind

    item = models.OneToOneField(
        SourceItem,
        on_delete=models.CASCADE,
        related_name="state",
        help_text=_("The supplier item this state belongs to."),
    )
    # Mirrors item.source (no DB rule ties them); services set it from the locked item, and a
    # test keeps them equal. Kept here so the (source, native_id) uniqueness is a DB constraint.
    source = models.ForeignKey(
        SupplierSource,
        on_delete=models.CASCADE,
        related_name="states",
        help_text=_("The feed of the item; native ids are unique inside it."),
    )
    kind = models.CharField(
        max_length=10, choices=ItemKind.choices, help_text=_("Whether the item has variations.")
    )
    native_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text=_("The supplier's own numeric id of the item. Empty when it publishes none."),
    )
    state = models.JSONField(help_text=_("The parsed observation, with its variations."))
    stock_status = models.SmallIntegerField(
        null=True, blank=True, help_text=_("1 in stock, 0 out of stock, empty if unknown.")
    )
    stock_quantity = models.IntegerField(
        null=True, blank=True, help_text=_("Units the supplier states, empty if unknown.")
    )
    version = models.PositiveIntegerField(help_text=_("Grows by one at every change of the state."))
    observed_at = models.DateTimeField(help_text=_("When the state was last read."))

    objects = VendorScopedManager()

    class Meta:
        ordering = ["item"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=engine.ITEM_KINDS), name="ingest_state_kind"
            ),
            models.CheckConstraint(
                condition=models.Q(kind=engine.SIMPLE) | models.Q(native_id__isnull=False),
                name="ingest_state_variable_native_id",
            ),
            models.CheckConstraint(
                condition=models.Q(native_id__isnull=True) | models.Q(native_id__gt=0),
                name="ingest_state_native_id_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gt=0), name="ingest_state_version_positive"
            ),
            _stock_constraint("ingest_state_stock_coherent"),
            models.UniqueConstraint(
                fields=["source", "native_id"],
                condition=models.Q(native_id__isnull=False),
                name="ingest_state_source_native_id",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.item} v{self.version}"


class SourceStateVersion(BaseModel):
    """Append-only history of `SourceState`: one row per semantic change, never edited."""

    vendor_scope: ClassVar[str] = "item__vendor"

    item = models.ForeignKey(
        SourceItem,
        on_delete=models.CASCADE,
        related_name="state_versions",
        help_text=_("The supplier item."),
    )
    version = models.PositiveIntegerField(help_text=_("The version of the state it records."))
    state = models.JSONField(help_text=_("The state as it was at that version."))
    observed_at = models.DateTimeField(help_text=_("When that state was read."))

    objects = VendorScopedManager()

    class Meta:
        ordering = ["item", "version"]
        constraints = [
            models.UniqueConstraint(fields=["item", "version"], name="ingest_stateversion_item")
        ]

    def __str__(self) -> str:
        return f"{self.item} v{self.version}"


class SourceVariation(BaseModel):
    """One variation of a variable item, with its own stock. Never deleted: a variation the
    supplier stops listing is kept with `present` off and its last known data."""

    vendor_scope: ClassVar[str] = "item__vendor"

    item = models.ForeignKey(
        SourceItem,
        on_delete=models.CASCADE,
        related_name="variations",
        help_text=_("The variable item this variation belongs to."),
    )
    # Mirrors item.source (no DB rule ties them); services set it from the locked item, and a
    # test keeps them equal. Kept here so the (source, native_id) uniqueness is a DB constraint.
    source = models.ForeignKey(
        SupplierSource,
        on_delete=models.CASCADE,
        related_name="variations",
        help_text=_("The feed; a variation id belongs to one item of it."),
    )
    native_id = models.BigIntegerField(help_text=_("The supplier's numeric id of the variation."))
    state = models.JSONField(help_text=_("The parsed variation."))
    stock_status = models.SmallIntegerField(
        null=True, blank=True, help_text=_("1 in stock, 0 out of stock, empty if unknown.")
    )
    stock_quantity = models.IntegerField(
        null=True, blank=True, help_text=_("Units the supplier states, empty if unknown.")
    )
    present = models.BooleanField(help_text=_("Whether the last read still listed it."))
    observed_at = models.DateTimeField(help_text=_("When the variation was last written."))
    last_seen_at = models.DateTimeField(help_text=_("When the supplier last listed it."))

    objects = VendorScopedManager()

    class Meta:
        ordering = ["item", "native_id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(native_id__gt=0), name="ingest_variation_native_id_positive"
            ),
            _stock_constraint("ingest_variation_stock_coherent"),
            models.UniqueConstraint(
                fields=["source", "native_id"], name="ingest_variation_source_native_id"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.item} #{self.native_id}"


class SourceMissing(BaseModel):
    """Evidence that the supplier's page for an item is gone (HTTP 404).

    Written when a scan gets the 404 and removed when the same item is read successfully again.
    Stock rule: the supplier is master, so a missing item has stock 0 and stays published."""

    vendor_scope: ClassVar[str] = "item__vendor"

    item = models.OneToOneField(
        SourceItem,
        on_delete=models.CASCADE,
        related_name="missing",
        help_text=_("The supplier item whose page is missing."),
    )
    url = models.CharField(max_length=500, help_text=_("The address that answered."))
    http_status = models.PositiveSmallIntegerField(help_text=_("The answer, e.g. 404."))
    first_seen_at = models.DateTimeField(help_text=_("When it was first found missing."))
    last_seen_at = models.DateTimeField(help_text=_("When it was last found missing."))

    objects = VendorScopedManager()

    class Meta:
        ordering = ["item"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(http_status__gte=400, http_status__lt=600),
                name="ingest_missing_http_error",
            )
        ]

    def __str__(self) -> str:
        return f"{self.item} ({self.http_status})"


class ItemChange(BaseModel):
    """Append-only change history of an item's fields.

    `changed_fields` flags the fields this change sets (`{"price": true}`), `new_values` holds
    their values. The effective value of a field is the last one flagged, by version then id."""

    vendor_scope: ClassVar[str] = "item__vendor"

    item = models.ForeignKey(
        SourceItem,
        on_delete=models.CASCADE,
        related_name="changes",
        help_text=_("The supplier item."),
    )
    version = models.PositiveIntegerField(help_text=_("Order of the change for the item."))
    changed_fields = models.JSONField(help_text=_("The fields this change sets, as true flags."))
    new_values = models.JSONField(help_text=_("The values of the fields."))
    recorded_at = models.DateTimeField(help_text=_("When the change was recorded."))

    objects = VendorScopedManager()

    class Meta:
        ordering = ["item", "version", "id"]
        constraints = [
            models.UniqueConstraint(fields=["item", "version"], name="ingest_change_item_version")
        ]

    def __str__(self) -> str:
        return f"{self.item} change v{self.version}"


class ProductDraft(BaseModel):
    """What the pipeline extracted (and enriched, per language) from an item."""

    Status = DraftStatus

    vendor_scope: ClassVar[str] = "item__vendor"

    item = models.OneToOneField(
        SourceItem,
        on_delete=models.CASCADE,
        related_name="draft",
        help_text=_("The supplier item this draft was made from. An item has one draft."),
    )
    extracted = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("The product data the import pulled out of the supplier's data, as JSON."),
    )
    enriched = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("The texts written for the product, one set per language, as JSON."),
    )
    status = models.CharField(
        max_length=20,
        choices=Status,
        default=Status.NEW,
        help_text=_("Where the draft stands: new, ready, needs review, published or rejected."),
    )

    objects = VendorScopedManager()

    class Meta:
        constraints = [_in("status", DraftStatus, "ingest_draft_status_valid")]

    def __str__(self) -> str:
        return f"Draft of {self.item}"


class EnrichmentOutcome(models.TextChoices):
    ACCEPTED = "accepted", "Accepted"
    OUT_OF_POLICY = "out_of_policy", "Out of policy"
    SCHEMA_INVALID = "schema_invalid", "Schema invalid"


class EnrichmentVersion(BaseModel):
    """Archive of one AI enrichment output (ING.5): kept whether or not it was applied."""

    Outcome = EnrichmentOutcome

    vendor_scope: ClassVar[str] = "draft__item__vendor"

    draft = models.ForeignKey(
        ProductDraft,
        on_delete=models.CASCADE,
        related_name="enrichment_versions",
        help_text=_("The draft this text was written for."),
    )
    lang = models.CharField(max_length=16, help_text=_("The language of the text."))
    provider = models.CharField(max_length=40, help_text=_("Which AI provider wrote the text."))
    model = models.CharField(max_length=80, help_text=_("Which model of the provider wrote it."))
    prompt_version = models.CharField(
        max_length=40, help_text=_("The version of the instructions the model was given.")
    )
    outcome = models.CharField(
        max_length=20,
        choices=Outcome,
        help_text=_("Whether the text was accepted, outside the policy or not in the right shape."),
    )
    raw = models.JSONField(
        default=dict, blank=True, help_text=_("The answer of the provider, as it came.")
    )
    violations = models.JSONField(
        default=list, blank=True, help_text=_("What the text broke, as a list. Empty if accepted.")
    )

    objects = VendorScopedManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["draft", "lang"], name="ingest_enrich_draft_lang")]
        constraints = [_in("outcome", EnrichmentOutcome, "ingest_enrichment_outcome_valid")]

    def __str__(self) -> str:
        return f"{self.draft} {self.lang} ({self.outcome})"


class ImportRun(BaseModel):
    """One pass over a source. Starts queued and dry; the counters are totals of the pass."""

    Kind = RunKind

    Status = RunStatus

    vendor_scope: ClassVar[str] = "source__vendor"

    source = models.ForeignKey(
        SupplierSource,
        on_delete=models.CASCADE,
        related_name="runs",
        help_text=_("The supplier feed this run read."),
    )
    kind = models.CharField(
        max_length=20,
        choices=Kind,
        help_text=_("What the run does: scrape, stock, price, images or publish."),
    )
    status = models.CharField(
        max_length=20,
        choices=Status,
        default=Status.QUEUED,
        help_text=_("Where the run stands: queued, running, succeeded, failed or cancelled."),
    )
    dry_run = models.BooleanField(
        default=True,
        help_text=_(
            "A dry run only shows what would change. A run that writes is possible only if "
            "the source allows it."
        ),
    )
    started_at = models.DateTimeField(null=True, blank=True, help_text=_("When the run started."))
    finished_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When the run ended, whatever the result.")
    )
    seen = models.PositiveIntegerField(
        default=0, help_text=_("How many items the run found in the feed.")
    )
    created = models.PositiveIntegerField(default=0, help_text=_("How many items were new."))
    updated = models.PositiveIntegerField(default=0, help_text=_("How many items had changed."))
    unchanged = models.PositiveIntegerField(
        default=0, help_text=_("How many items had not changed.")
    )
    skipped = models.PositiveIntegerField(
        default=0, help_text=_("How many items the run left out.")
    )
    errors = models.PositiveIntegerField(default=0, help_text=_("How many items failed."))
    log = models.TextField(blank=True, help_text=_("The run's report, in plain text."))

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

    run = models.ForeignKey(
        ImportRun,
        on_delete=models.CASCADE,
        related_name="review_items",
        help_text=_("The run that opened this case."),
    )
    item = models.ForeignKey(
        SourceItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_items",
        help_text=_("The supplier item the case is about, if it is about one."),
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_items",
        help_text=_("The catalogue product the case is about, if it is about one."),
    )
    kind = models.CharField(
        max_length=20,
        choices=Kind,
        help_text=_(
            "Why a person must look: price jump, sale, identity change, stale item or "
            "ambiguous SKU match."
        ),
    )
    blocking = models.BooleanField(
        default=False,
        help_text=_("A blocking case stops its run from ending as succeeded until it is decided."),
    )
    payload = models.JSONField(
        default=dict, blank=True, help_text=_("The facts of the case, as JSON.")
    )
    decision = models.CharField(
        max_length=10,
        choices=Decision,
        default=Decision.PENDING,
        help_text=_("The decision: pending, accepted, rejected or deferred."),
    )
    note = models.TextField(blank=True, help_text=_("A note on the decision."))
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("The person who decided the case."),
    )
    resolved_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When the case was decided.")
    )

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
        Vendor,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mappings",
        help_text=_(
            "Leave it empty for an entry that holds for every vendor. A vendor's own entry "
            "wins over an entry for every vendor."
        ),
    )
    kind = models.CharField(
        max_length=20,
        choices=Kind,
        help_text=_(
            "What kind of term this is: category, taxonomy term, colour, material, brand, "
            "product type, country or other."
        ),
    )
    key = models.CharField(
        max_length=200,
        help_text=_(
            "The supplier's term, as the supplier writes it. Upper and lower case do not matter."
        ),
    )
    key_norm = models.CharField(max_length=200, editable=False)
    aliases = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Other ways the supplier writes the same term, as a JSON list."),
    )
    target_type = models.ForeignKey(
        ContentType,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_(
            "The kind of catalogue object this term stands for. Leave it empty when the "
            "term stands for a plain value."
        ),
    )
    target_id = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        help_text=_("The number of the catalogue object this term stands for."),
    )
    target_value = models.CharField(
        max_length=200,
        blank=True,
        help_text=_(
            "The plain value this term stands for, for example a colour name. Use it when "
            "there is no catalogue object."
        ),
    )
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
