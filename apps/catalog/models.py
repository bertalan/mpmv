"""Products, variants and their translations (spec 03 §3.2, CAT.1).

The category tree, taxonomies, terms and brands (CAT.2) live in `taxonomy.py`; products link
to them here, and to the typed attributes of `attributes.py` (CAT.3) through `ProductAttribute`
and `VariantOption`. Fields owned by later tasks are added there: images (CAT.4). GPSR data
(CMP.3) lives in `apps.compliance` (`product.gpsr`, a reverse one-to-one); its publication check
is registered in `publishing.py`, which the publishing services of CAT.10 call.
Slugs are derived from the title by `services.sync_product_slug` (CAT.5), which keeps the
replaced ones in `SlugHistory`. `Redirect` (CAT.6) holds the staff-managed legacy redirects; it
belongs to the platform, not to a vendor, and changes only through the audited services.
Prices change only through `services.set_product_price` and `services.set_variant_price`, which
audit them; the status changes through the publishing services of CAT.10.

Vendor scoping (CORE.3): every model here uses `VendorScopedManager`. `Variant.vendor` copies
its product's vendor so that the database can enforce one SKU per vendor among variants; a
composite foreign key (migration 0001) keeps the copy equal to the product's vendor.

Integrity rules (CAT.14, owner decisions Q-29, Q-31, Q-32, Q-33), enforced by the database
(migration 0003) and reported by `clean()` for forms: a product never changes vendor (staff use
`services.duplicate_product_to_vendor`); only a `buy` product needs a price, and a price is never
0.00; variants exist only on variable products; a required companion has the same vendor.
Suggestions across vendors are checked by `services.check_upsells` (signal in `signals.py`).

Identity (CAT.13, owner decisions Q-30 and Q-33): the platform refers to products and variants
by `public_id`, and staff by `staff_ref`, a prefixed sequence the vendor never sees. The vendor
SKU is a label kept raw in `sku`; `sku_key` (`engine.sku_match_key`, set on save) is unique per
vendor across products and variants together. Within a table a unique constraint enforces it,
across the two tables a database trigger (migration 0006), which also writes a `SkuAlias` row
whenever a match key changes, so older import files still match. Change SKUs with `save()` or
the rename services, never with `QuerySet.update()`, which skips the match key.
"""

from typing import Any, ClassVar

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Now
from django.utils import timezone

from apps.core.fields import LanguageField, MoneyField
from apps.core.models import BaseModel, TranslationModel, TranslationStatus, resolve_translation
from apps.core.translations import Resolved
from apps.vendors.models import Vendor
from apps.vendors.scoping import VendorScopedManager

from . import engine
from .attributes import (
    Attribute,
    AttributeKind,
    AttributeTranslation,
    AttributeValue,
    AttributeValueTranslation,
)
from .fields import SkuField
from .taxonomy import (
    Brand,
    BrandTranslation,
    Category,
    CategoryTranslation,
    Manufacturer,
    Taxonomy,
    TaxonomyTranslation,
    Term,
    TermTranslation,
)

__all__ = [
    "Attribute",
    "AttributeKind",
    "AttributeTranslation",
    "AttributeValue",
    "AttributeValueTranslation",
    "Brand",
    "BrandTranslation",
    "Category",
    "CategoryTranslation",
    "FiscalCategory",
    "Manufacturer",
    "Product",
    "ProductAttribute",
    "ProductKind",
    "ProductStatus",
    "ProductTranslation",
    "Redirect",
    "SalesMode",
    "SkuAlias",
    "SlugHistory",
    "StockPolicy",
    "StockStatus",
    "Taxonomy",
    "TaxonomyTranslation",
    "Term",
    "TermTranslation",
    "Variant",
    "VariantOption",
    "VariantTranslation",
]

gtin_validator = RegexValidator(r"^\d{8,14}$", "A GTIN (EAN, UPC, ISBN) has 8 to 14 digits.")


def _choice(field: str, choices: type[models.TextChoices], name: str) -> models.CheckConstraint:
    return models.CheckConstraint(condition=models.Q(**{f"{field}__in": choices.values}), name=name)


def _not_negative(field: str, label: str, name: str) -> models.CheckConstraint:
    return models.CheckConstraint(
        condition=models.Q(**{f"{field}__isnull": True}) | models.Q(**{f"{field}__gte": 0}),
        name=name,
        violation_error_message=f"The {label} cannot be negative.",
    )


def _above_zero(field: str, label: str, name: str) -> models.CheckConstraint:
    return models.CheckConstraint(
        condition=models.Q(**{f"{field}__isnull": True}) | models.Q(**{f"{field}__gt": 0}),
        name=name,
        violation_error_message=f"The {label} must be above 0.00; leave it empty instead.",
    )


def _sku_key_present(name: str) -> models.CheckConstraint:
    return models.CheckConstraint(
        condition=~models.Q(sku_key=""),
        name=name,
        violation_error_message="A SKU cannot be empty.",
    )


def _vendor_sku_unique(name: str, label: str) -> models.UniqueConstraint:
    return models.UniqueConstraint(
        fields=["vendor", "sku_key"],
        name=name,
        violation_error_message=f"This vendor already has a {label} with this SKU.",
    )


def _staff_ref(prefix: str, sequence: str) -> "models.CharField[Any, Any]":
    """A short prefixed sequence for staff ("P-000123"), drawn by the database on insert."""
    return models.CharField(
        "staff reference",
        max_length=24,
        unique=True,
        editable=False,
        db_default=models.Func(
            models.Value(prefix),
            models.Value(sequence),
            function="catalog_staff_ref",
            output_field=models.CharField(),
        ),
        help_text="Internal reference for staff; never shown to vendors.",
    )


def _sku_key_field() -> "models.CharField[Any, Any]":
    return models.CharField(
        "SKU match key",
        max_length=64,
        editable=False,
        help_text="The SKU normalised for matching; unique per vendor.",
    )


def _sku_key_prefix_index(name: str) -> models.Index:
    """Serves the SKU prefix search (CAT.9): `sku_key LIKE 'prefix%'` in any collation."""
    return models.Index(fields=["sku_key"], opclasses=["varchar_pattern_ops"], name=name)


def product_text_vector(config: str) -> models.Expression:
    """The full-text vector of a product text in the text search `config` (CAT.9): title
    weighted A, short description B, description C. The search query and the per-language GIN
    indexes of `ProductTranslation` use this same expression, so the planner matches them."""
    return (
        SearchVector("title", weight="A", config=config)
        + SearchVector("short_description", weight="B", config=config)
        + SearchVector("description", weight="C", config=config)
    )


def sku_owner(
    vendor_id: int, sku_key: str, *, product_pk: int | None = None, variant_pk: int | None = None
) -> "Product | Variant | None":
    """The product or variant of the vendor that holds `sku_key`, other than the given ones."""
    products = Product.objects.filter(vendor_id=vendor_id, sku_key=sku_key)
    if product_pk is not None:
        products = products.exclude(pk=product_pk)
    product = products.first()
    if product is not None:
        return product
    variants = Variant.objects.filter(vendor_id=vendor_id, sku_key=sku_key)
    if variant_pk is not None:
        variants = variants.exclude(pk=variant_pk)
    return variants.first()


def sku_taken_message(owner: "Product | Variant") -> str:
    label = "product" if isinstance(owner, Product) else "variant"
    return f"This vendor already has a {label} with this SKU."


def _check_sku(item: "Product | Variant") -> None:
    """Model validation of the SKU: usable, and its match key free in the vendor's namespace."""
    if not item.sku:
        return  # the required-field error is already reported
    errors = engine.sku_errors(item.sku)
    if errors:
        raise ValidationError({"sku": errors})
    item.sku_key = engine.sku_match_key(item.sku)
    if item.vendor_id is None:
        return
    owner = sku_owner(
        item.vendor_id,
        item.sku_key,
        product_pk=item.pk if isinstance(item, Product) else None,
        variant_pk=item.pk if isinstance(item, Variant) else None,
    )
    if owner is not None:
        raise ValidationError({"sku": sku_taken_message(owner)})


def _with_sku_key(item: "Product | Variant", kwargs: dict[str, Any]) -> None:
    """Set the match key before a save; a save limited to `sku` also writes `sku_key`."""
    item.sku_key = engine.sku_match_key(item.sku)
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "sku" in update_fields:
        kwargs["update_fields"] = {*update_fields, "sku_key"}


class ProductKind(models.TextChoices):
    SIMPLE = "simple", "Simple"
    VARIABLE = "variable", "Variable"


class ProductStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    RETIRED = "retired", "Retired (page live, not purchasable)"


class SalesMode(models.TextChoices):
    BUY = "buy", "Buy"
    CATALOG_ONLY = "catalog_only", "Catalog only"
    ON_REQUEST = "on_request", "On request"


class FiscalCategory(models.TextChoices):
    STANDARD = "standard", "Standard rate"
    REDUCED = "reduced", "Reduced rate"
    BOOK_ART74 = "book_art74", "Books (art. 74 regime)"


class StockPolicy(models.TextChoices):
    TRACK = "track", "Track stock"
    ALWAYS_OPEN = "always_open", "Always open (backorders allowed)"
    FROM_SOURCE = "from_source", "From the supplier source"


class StockStatus(models.TextChoices):
    IN_STOCK = "in_stock", "In stock"
    OUT_OF_STOCK = "out_of_stock", "Out of stock"
    ON_BACKORDER = "on_backorder", "On backorder"


class Product(BaseModel):
    Kind = ProductKind
    Status = ProductStatus
    SalesMode = SalesMode
    FiscalCategory = FiscalCategory
    StockPolicy = StockPolicy
    StockStatus = StockStatus

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="products")
    staff_ref = _staff_ref("P-", "catalog_product_staff_ref_seq")
    sku = SkuField(
        help_text="The vendor's label, kept as entered; unique per vendor ignoring case and "
        "spaces, across products and variants."
    )
    sku_key = _sku_key_field()
    kind = models.CharField(max_length=16, choices=ProductKind.choices, default=ProductKind.SIMPLE)
    # Changed only by the publishing services (CAT.10), which check the publishing rules.
    status = models.CharField(
        max_length=16, choices=ProductStatus.choices, default=ProductStatus.DRAFT, editable=False
    )
    sales_mode = models.CharField(max_length=16, choices=SalesMode.choices, default=SalesMode.BUY)
    fiscal_category = models.CharField(
        max_length=16, choices=FiscalCategory.choices, default=FiscalCategory.STANDARD
    )

    # Required only for `buy`; kept, never public, for the other modes (Q-31, engine.public_price).
    price_net = MoneyField(
        "net price",
        null=True,
        blank=True,
        help_text="EUR, VAT excluded. Required to sell; never 0.00 (leave it empty instead).",
    )
    sale_price_net = MoneyField("net sale price", null=True, blank=True)
    sale_from = models.DateTimeField(null=True, blank=True)
    sale_to = models.DateTimeField(null=True, blank=True)

    # Stock of a simple product; a variable product keeps it on its variants.
    stock_policy = models.CharField(
        max_length=16, choices=StockPolicy.choices, default=StockPolicy.TRACK
    )
    stock_qty = models.IntegerField("stock quantity", default=0)
    stock_status = models.CharField(
        max_length=16, choices=StockStatus.choices, default=StockStatus.IN_STOCK
    )

    weight_kg = models.DecimalField(
        "weight (kg)",
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Required to publish a product that can be bought: it drives shipping.",
    )
    length_cm = models.DecimalField(
        "length (cm)", max_digits=7, decimal_places=2, null=True, blank=True
    )
    width_cm = models.DecimalField(
        "width (cm)", max_digits=7, decimal_places=2, null=True, blank=True
    )
    height_cm = models.DecimalField(
        "height (cm)", max_digits=7, decimal_places=2, null=True, blank=True
    )

    gtin = models.CharField(
        "GTIN",
        max_length=14,
        blank=True,
        validators=[gtin_validator],
        help_text="EAN or ISBN; when empty the SKU is used as MPN.",
    )
    mpn = models.CharField(
        "MPN",
        max_length=70,
        blank=True,
        help_text="Manufacturer part number: with the brand it links the same article across "
        "vendors.",
    )
    google_category_id = models.PositiveIntegerField(
        "Google product category", null=True, blank=True
    )

    required_companion = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="required_by",
        help_text="A product that must be bought with this one.",
    )
    upsells = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="upsold_by"
    )

    # Classification (CAT.2). A draft may have no category yet; publishing rules are CAT.10's.
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="products",
        help_text="Main category: the product's breadcrumb and Google category fallback.",
    )
    secondary_categories = models.ManyToManyField(
        Category, blank=True, related_name="secondary_products"
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="products",
        help_text="Manufacturer brand.",
    )
    # Tag the most specific terms: their upper levels are derived (services.effective_terms).
    terms = models.ManyToManyField(Term, blank=True, related_name="products")

    legacy_wp_id_en = models.PositiveBigIntegerField(
        "legacy EN post id", null=True, blank=True, unique=True
    )
    legacy_wp_id_it = models.PositiveBigIntegerField(
        "legacy IT post id", null=True, blank=True, unique=True
    )

    class Meta:
        ordering = ["sku", "id"]
        indexes = [_sku_key_prefix_index("catalog_product_sku_key_like")]
        constraints = [
            _vendor_sku_unique("catalog_product_vendor_sku_key_unique", "product"),
            # Target of the variants' composite foreign key (product, vendor).
            models.UniqueConstraint(
                fields=["id", "vendor"], name="catalog_product_id_vendor_unique"
            ),
            _sku_key_present("catalog_product_sku_key_present"),
            _choice("kind", ProductKind, "catalog_product_kind_valid"),
            _choice("status", ProductStatus, "catalog_product_status_valid"),
            _choice("sales_mode", SalesMode, "catalog_product_sales_mode_valid"),
            _choice("fiscal_category", FiscalCategory, "catalog_product_fiscal_category_valid"),
            _choice("stock_policy", StockPolicy, "catalog_product_stock_policy_valid"),
            _choice("stock_status", StockStatus, "catalog_product_stock_status_valid"),
            models.CheckConstraint(
                condition=models.Q(price_net__isnull=True) | models.Q(price_net__gt=0),
                name="catalog_product_price_net_positive",
                violation_error_message="The price must be above 0.00; leave it empty instead.",
            ),
            models.CheckConstraint(
                condition=~models.Q(sales_mode=SalesMode.BUY) | models.Q(price_net__isnull=False),
                name="catalog_product_price_required_to_buy",
                violation_error_message="A product for sale needs a price.",
            ),
            models.CheckConstraint(
                condition=models.Q(sale_price_net__isnull=True)
                | (
                    models.Q(price_net__isnull=False)
                    & models.Q(sale_price_net__gt=0)
                    & models.Q(sale_price_net__lt=models.F("price_net"))
                ),
                name="catalog_product_sale_price_below_price",
                violation_error_message="The sale price must be above 0.00, below the price.",
            ),
            models.CheckConstraint(
                condition=models.Q(sale_price_net__isnull=False)
                | (models.Q(sale_from__isnull=True) & models.Q(sale_to__isnull=True)),
                name="catalog_product_sale_period_needs_sale_price",
                violation_error_message="A sale period needs a sale price.",
            ),
            models.CheckConstraint(
                condition=models.Q(sale_from__isnull=True)
                | models.Q(sale_to__isnull=True)
                | models.Q(sale_to__gt=models.F("sale_from")),
                name="catalog_product_sale_ends_after_start",
                violation_error_message="The sale must end after it starts.",
            ),
            _not_negative("stock_qty", "stock quantity", "catalog_product_stock_qty_not_negative"),
            models.CheckConstraint(
                condition=~models.Q(stock_status=StockStatus.ON_BACKORDER)
                | models.Q(stock_policy=StockPolicy.ALWAYS_OPEN),
                name="catalog_product_backorder_needs_always_open",
                violation_error_message="Backorders need the always-open stock policy.",
            ),
            _not_negative("weight_kg", "weight", "catalog_product_weight_not_negative"),
            _not_negative("length_cm", "length", "catalog_product_length_not_negative"),
            _not_negative("width_cm", "width", "catalog_product_width_not_negative"),
            _not_negative("height_cm", "height", "catalog_product_height_not_negative"),
            models.CheckConstraint(
                condition=~models.Q(required_companion=models.F("pk")),
                name="catalog_product_companion_not_self",
                violation_error_message="A product cannot be its own required companion.",
            ),
        ]

    def __str__(self) -> str:
        return str(self.sku)

    def clean(self) -> None:
        # Report the SKU (CAT.13) and the integrity rules (CAT.14) together.
        super().clean()
        errors: dict[str, list[str]] = {}
        try:
            _check_sku(self)
        except ValidationError as error:
            errors.update(error.message_dict)
        try:
            self.clean_integrity()
        except ValidationError as error:
            for name, messages in error.message_dict.items():
                errors.setdefault(name, []).extend(messages)
        if errors:
            raise ValidationError(errors)

    def clean_integrity(self) -> None:
        """The CAT.14 rules the database enforces, as form errors (Q-31, Q-32, Q-33)."""
        errors: dict[str, str] = {}
        stored = (
            None
            if self._state.adding or self.pk is None
            else Product.objects.filter(pk=self.pk).values("vendor_id").first()
        )
        if stored is not None and stored["vendor_id"] != self.vendor_id:
            errors["vendor"] = (
                "A product never changes vendor: duplicate it to the new vendor instead."
            )
        if (
            stored is not None
            and self.kind != ProductKind.VARIABLE
            and Variant.objects.filter(product_id=self.pk).exists()
        ):
            errors["kind"] = "Only a variable product has variants: remove them first."
        if self.required_companion_id is not None and self.vendor_id is not None:
            companion_vendor = (
                Product.objects.filter(pk=self.required_companion_id)
                .values_list("vendor_id", flat=True)
                .first()
            )
            if companion_vendor is not None and companion_vendor != self.vendor_id:
                errors["required_companion"] = (
                    "A required companion must belong to the same vendor."
                )
        if self.sale_price_net is not None and self.sale_price_net == 0:
            errors["sale_price_net"] = engine.ZERO_SALE_PRICE
        if "price_net" not in errors:
            missing = engine.price_required_error(self.sales_mode, self.price_net)
            if missing is not None:
                errors["price_net"] = missing
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        _with_sku_key(self, kwargs)
        super().save(*args, **kwargs)

    def translation(self, language: str) -> "Resolved[ProductTranslation]":
        """Texts to show in `language`: published, or EN with a fallback flag."""
        return resolve_translation(self.translations.all(), language)


class ProductTranslation(TranslationModel):
    """A product's texts in one language (explicit table, spec 09 §9.2)."""

    translation_of = "product"
    translated_fields = (
        "title",
        "short_description",
        "description",
        "seo_title",
        "seo_description",
    )

    vendor_scope: ClassVar[str] = "product__vendor"
    objects = VendorScopedManager()

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="translations")
    title = models.CharField(max_length=255)
    short_description = models.TextField(blank=True)
    description = models.TextField(blank=True, help_text="HTML.")
    # Derived {title} (no language suffix, D-25) from the title by `services.sync_product_slug`
    # (CAT.5), never the SKU; empty until generated. Replaced slugs move to SlugHistory and answer
    # a 301.
    slug = models.SlugField(max_length=80, blank=True, editable=False)
    seo_title = models.CharField("SEO title", max_length=255, blank=True)
    seo_description = models.TextField("SEO description", blank=True)

    class Meta(TranslationModel.Meta):
        ordering = ["product", "language"]
        # Product search (CAT.9): one full-text index per language, on its published texts, and
        # a trigram index on the title for typos.
        indexes = [
            *(
                GinIndex(
                    product_text_vector(config),
                    condition=models.Q(language=code, status=TranslationStatus.PUBLISHED),
                    name=f"catalog_ptrans_fts_{code}",
                )
                for code, config in engine.SEARCH_LANGUAGE_CONFIGS.items()
            ),
            GinIndex(
                fields=["title"], opclasses=["gin_trgm_ops"], name="catalog_ptrans_title_trgm"
            ),
        ]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["product", "language"], name="catalog_producttranslation_unique"
            ),
            models.UniqueConstraint(
                fields=["language", "slug"],
                condition=~models.Q(slug=""),
                name="catalog_producttranslation_slug_unique",
                violation_error_message="Another product already uses this slug in this language.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product} [{self.language}]"


class SlugHistory(BaseModel):
    """A product's former slug in one language (CAT.5); it answers a 301 to the current slug.

    Replaces the legacy `_legacy_old_slugs` meta, without its limit of 10. The legacy
    `{title}-{lang}` and `{title}-{lang}-{sku}` slugs are loaded here. One slug belongs to
    at most one product per language, among current and old slugs alike (checked by the
    services), so every old slug redirects in a single hop.
    """

    vendor_scope: ClassVar[str] = "product__vendor"
    objects = VendorScopedManager()

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="slug_history")
    language = LanguageField()
    # Legacy slugs may be longer than the current 80-character limit.
    slug = models.SlugField(max_length=200)
    replaced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["product", "language", "-replaced_at"]
        verbose_name_plural = "slug history"
        constraints = [
            models.UniqueConstraint(
                fields=["language", "slug"],
                name="catalog_slughistory_language_slug_unique",
                violation_error_message="This old slug already belongs to a product.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.slug} [{self.language}]"


class PriceHistory(BaseModel):
    """One recorded price setting of a product or variant (CMP.6, Omnibus 30-day lowest price).

    Append-only: written by the price services on every change, never edited (the model refuses
    an update and database triggers refuse an update and a direct delete; rows go only with their
    product or variant, through the delete services, which is intended). A row
    with `variant` empty is the product's regular price and its optional scheduled sale; a row
    with a variant records that variant's override (empty: it follows the product again).
    Amounts are net, in `currency`.
    """

    vendor_scope: ClassVar[str] = "product__vendor"
    objects = VendorScopedManager()

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="price_history")
    variant = models.ForeignKey(
        "Variant", on_delete=models.CASCADE, null=True, blank=True, related_name="price_history"
    )
    currency = models.CharField(max_length=3, default="EUR")
    price_net = MoneyField("net price", null=True, blank=True)
    sale_price_net = MoneyField("net sale price", null=True, blank=True)
    sale_from = models.DateTimeField(null=True, blank=True)
    sale_to = models.DateTimeField(null=True, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["product", "recorded_at", "id"]
        verbose_name_plural = "price history"
        indexes = [models.Index(fields=["product", "recorded_at"], name="catalog_ph_product_time")]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(price_net__isnull=True) | models.Q(price_net__gte=0))
                & (models.Q(sale_price_net__isnull=True) | models.Q(sale_price_net__gte=0)),
                name="catalog_pricehistory_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(currency__regex=r"^[A-Z]{3}$"),
                name="catalog_pricehistory_currency",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product_id} {self.price_net} {self.currency} @ {self.recorded_at:%Y-%m-%d}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise PermissionError("PriceHistory is append-only")
        super().save(*args, **kwargs)


def _only(kind: str, field: str | None) -> models.Q:
    """The target columns of a 301 to `kind`: `field` set, every other one empty."""
    empty = {
        "target_path": models.Q(target_path=""),
        "target_product": models.Q(target_product__isnull=True),
        "target_term": models.Q(target_term__isnull=True),
        "target_query": models.Q(target_query=""),
    }
    condition = models.Q(code=301, target_kind=kind)
    for name, is_empty in empty.items():
        condition &= ~is_empty if name == field else is_empty
    return condition


class Redirect(BaseModel):
    """A legacy URL that moves (301) or is gone (410), CAT.6, spec 03 §3.1-3.2.

    Applies only when the path answers 404, so a live page always wins. The target is a path on
    this site, a product, a taxonomy term or a search, resolved in the source path's language at
    request time (a product or a term keeps its current URL). Write through
    `services.save_redirect` and `services.delete_redirect`, which validate (no off-site target,
    no loop, no chain) and audit.
    """

    class Code(models.IntegerChoices):
        MOVED = 301, "301 moved permanently"
        GONE = 410, "410 gone"

    class Target(models.TextChoices):
        PATH = "path", "Path on this site"
        PRODUCT = "product", "Product"
        TERM = "term", "Taxonomy term"
        SEARCH = "search", "Search"

    class Origin(models.TextChoices):
        MANUAL = "manual", "Manual"
        RETIRED_SKU = "retired_sku", "Retired SKU"
        MERGED_TAG = "merged_tag", "Merged tag"
        DISCONTINUED_CATEGORY = "discontinued_category", "Discontinued category"
        LEGACY_RULE = "legacy_rule", "Legacy site rule"

    source_path = models.CharField(
        max_length=engine.REDIRECT_PATH_MAX_LENGTH,
        unique=True,
        help_text="The old path, e.g. /tag-product/old-tag/.",
    )
    match_prefix = models.BooleanField(
        "match everything below", default=False, help_text="E.g. /author/ for /author/anyone/."
    )
    code = models.PositiveSmallIntegerField(choices=Code.choices, default=Code.MOVED)
    target_kind = models.CharField(max_length=16, choices=Target.choices, blank=True)
    target_path = models.CharField(
        max_length=engine.REDIRECT_PATH_MAX_LENGTH,
        blank=True,
        help_text="A path on this site, e.g. /it/. Never another site.",
    )
    target_product = models.ForeignKey(
        Product, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    target_term = models.ForeignKey(
        Term, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    target_query = models.CharField("search words", max_length=200, blank=True)
    origin = models.CharField(max_length=32, choices=Origin.choices, default=Origin.MANUAL)
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Empty: the rule never expires."
    )
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["source_path"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    _only("path", "target_path")
                    | _only("product", "target_product")
                    | _only("term", "target_term")
                    | _only("search", "target_query")
                    | (
                        models.Q(code=410, target_kind="", target_path="", target_query="")
                        & models.Q(target_product__isnull=True, target_term__isnull=True)
                    )
                ),
                name="catalog_redirect_one_target",
                violation_error_message="A 301 has exactly one target; a 410 has none.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_path}{'*' if self.match_prefix else ''} ({self.code})"

    def clean(self) -> None:
        """The shape of the rule; loops and chains need the other rules (the services)."""
        errors: dict[str, str] = {}
        # Stored decoded, as Django decodes request paths: an encoded legacy URL still matches.
        self.source_path = engine.normalise_redirect_source(self.source_path)
        source_error = engine.redirect_path_error(self.source_path, allow_query=False)
        if source_error is None and self.match_prefix:
            source_error = engine.prefix_source_error(
                self.source_path, languages=[code for code, _ in settings.LANGUAGES]
            )
        if source_error:
            errors["source_path"] = source_error
        if self.code == self.Code.GONE:
            has_target = bool(self.target_kind or self.target_path or self.target_query) or (
                self.target_product_id is not None or self.target_term_id is not None
            )
            if has_target:
                errors["target_kind"] = "A 410 (gone) rule has no target."
        elif not self.target_kind:
            errors["target_kind"] = "Choose what the old path moves to."
        else:
            errors.update(self._target_errors())
        if errors:
            raise ValidationError(errors)

    def _target_errors(self) -> dict[str, str]:
        errors: dict[str, str] = {}
        needed = {
            self.Target.PATH: ("target_path", bool(self.target_path)),
            self.Target.PRODUCT: ("target_product", self.target_product_id is not None),
            self.Target.TERM: ("target_term", self.target_term_id is not None),
            self.Target.SEARCH: ("target_query", bool(self.target_query.strip())),
        }
        for kind, (field, present) in needed.items():
            if kind == self.target_kind and not present:
                errors[field] = "Required for this kind of target."
            elif kind != self.target_kind and present:
                errors[field] = "Leave empty: the rule moves to another kind of target."
        if self.target_kind == self.Target.PATH and self.target_path:
            path_error = engine.redirect_path_error(self.target_path, allow_query=True)
            if path_error:
                errors["target_path"] = path_error
        return errors


class Variant(BaseModel):
    """A purchasable variant of a variable product; empty overrides use the product's values."""

    StockStatus = StockStatus

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    # Always the product's vendor (set on save, enforced by the composite foreign key).
    vendor = models.ForeignKey(
        Vendor, on_delete=models.PROTECT, related_name="variants", editable=False
    )
    staff_ref = _staff_ref("V-", "catalog_variant_staff_ref_seq")
    sku = SkuField(
        help_text="The vendor's label, kept as entered; unique per vendor ignoring case and "
        "spaces, across products and variants."
    )
    sku_key = _sku_key_field()
    price_net_override = MoneyField(
        "net price override", null=True, blank=True, help_text="Empty: the product's price."
    )
    weight_kg_override = models.DecimalField(
        "weight override (kg)", max_digits=8, decimal_places=3, null=True, blank=True
    )
    stock_qty = models.IntegerField("stock quantity", default=0)
    stock_status = models.CharField(
        max_length=16, choices=StockStatus.choices, default=StockStatus.IN_STOCK
    )
    is_default = models.BooleanField("default variant", default=False)
    position = models.PositiveIntegerField(default=0)
    # One value per variant axis of the product (CAT.3); see VariantOption.
    options = models.ManyToManyField(
        AttributeValue, through="VariantOption", related_name="variants", blank=True
    )

    class Meta:
        ordering = ["product", "position", "id"]
        indexes = [_sku_key_prefix_index("catalog_variant_sku_key_like")]
        constraints = [
            _vendor_sku_unique("catalog_variant_vendor_sku_key_unique", "variant"),
            models.UniqueConstraint(
                fields=["product"],
                condition=models.Q(is_default=True),
                name="catalog_variant_one_default_per_product",
                violation_error_message="The product already has a default variant.",
            ),
            _sku_key_present("catalog_variant_sku_key_present"),
            _choice("stock_status", StockStatus, "catalog_variant_stock_status_valid"),
            _above_zero(
                "price_net_override", "price override", "catalog_variant_price_override_positive"
            ),
            _not_negative(
                "weight_kg_override", "weight override", "catalog_variant_weight_not_negative"
            ),
            _not_negative("stock_qty", "stock quantity", "catalog_variant_stock_qty_not_negative"),
        ]

    def __str__(self) -> str:
        return str(self.sku)

    def _sync_vendor(self) -> None:
        try:
            product = self.product
        except Product.DoesNotExist:
            return
        self.vendor_id = product.vendor_id

    def clean_fields(self, exclude: Any = None) -> None:
        self._sync_vendor()
        super().clean_fields(exclude)

    def clean(self) -> None:
        override = engine.override_error(self.price_net_override)
        if override is not None:
            raise ValidationError({"price_net_override": override})
        # Variants only on variable products (Q-29, Q-32; a database trigger enforces it too).
        kind = Product.objects.filter(pk=self.product_id).values_list("kind", flat=True).first()
        if kind is not None and kind != ProductKind.VARIABLE:
            raise ValidationError({"product": "Only a variable product can have variants."})
        # `vendor` and `sku_key` are not form fields, so forms skip the (vendor, sku_key)
        # constraint, and no constraint spans products and variants: check here.
        _check_sku(self)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self._sync_vendor()
        _with_sku_key(self, kwargs)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = {*update_fields, "vendor"}
        super().save(*args, **kwargs)

    def translation(self, language: str) -> "Resolved[VariantTranslation]":
        """Texts to show in `language`: published, or EN with a fallback flag."""
        return resolve_translation(self.translations.all(), language)


class VariantTranslation(TranslationModel):
    """A variant's texts in one language.

    `label` names the variant (e.g. "Red, size M") until CAT.3 derives labels from the
    translated attribute values of its options.
    """

    translation_of = "variant"
    translated_fields = ("label", "description")

    vendor_scope: ClassVar[str] = "variant__vendor"
    objects = VendorScopedManager()

    variant = models.ForeignKey(Variant, on_delete=models.CASCADE, related_name="translations")
    label = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    class Meta(TranslationModel.Meta):
        ordering = ["variant", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["variant", "language"], name="catalog_varianttranslation_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.variant} [{self.language}]"


class SkuAlias(BaseModel):
    """A SKU match key a product or variant used before a rename (CAT.13).

    Written by a database trigger whenever a match key changes (migration 0006), whatever made
    the change, so imports keep matching older files. Never edited by hand.
    """

    vendor_scope: ClassVar[str] = "vendor"

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="sku_aliases")
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, null=True, blank=True, related_name="sku_aliases"
    )
    variant = models.ForeignKey(
        Variant, on_delete=models.CASCADE, null=True, blank=True, related_name="sku_aliases"
    )
    sku = SkuField(help_text="The SKU as it was entered.")
    sku_key = models.CharField("SKU match key", max_length=64)
    replaced_at = models.DateTimeField(db_default=Now())

    objects = VendorScopedManager()

    class Meta:
        verbose_name = "SKU alias"
        verbose_name_plural = "SKU aliases"
        ordering = ["vendor", "sku_key", "replaced_at"]
        indexes = [
            models.Index(fields=["vendor", "sku_key"], name="catalog_skualias_vendor_key"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(product__isnull=False) & models.Q(variant__isnull=True))
                | (models.Q(product__isnull=True) & models.Q(variant__isnull=False)),
                name="catalog_skualias_product_xor_variant",
                violation_error_message="An alias belongs to a product or to a variant.",
            ),
        ]

    def __str__(self) -> str:
        return str(self.sku)


class ProductAttribute(BaseModel):
    """What a product says about one attribute, typed by the attribute's kind (CAT.3).

    A choice attribute lists the product's values (`values`), a number attribute holds
    `numeric_value` (in the attribute's unit), a text attribute `text_value` (language-neutral:
    an author, an ISBN). On a variable product, the choice attributes marked `is_variant_axis`
    are the axes its variants vary on, in `position` order: the first axis orders the variants
    first. `services.create_product` checks the whole shape (`engine.product_structure_errors`).
    """

    vendor_scope: ClassVar[str] = "product__vendor"
    objects = VendorScopedManager()

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="attributes")
    attribute = models.ForeignKey(
        Attribute, on_delete=models.PROTECT, related_name="product_attributes"
    )
    values = models.ManyToManyField(AttributeValue, blank=True, related_name="product_attributes")
    numeric_value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    text_value = models.CharField(max_length=255, blank=True)
    is_variant_axis = models.BooleanField(
        "variant axis", default=False, help_text="The product's variants vary on this attribute."
    )
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["product", "position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "attribute"],
                name="catalog_productattribute_unique",
                violation_error_message="The product already has this attribute.",
            ),
            models.CheckConstraint(
                condition=models.Q(numeric_value__isnull=True) | models.Q(text_value=""),
                name="catalog_productattribute_one_typed_value",
                violation_error_message="An attribute holds a number or a text, not both.",
            ),
            models.CheckConstraint(
                condition=models.Q(is_variant_axis=False)
                | (models.Q(numeric_value__isnull=True) & models.Q(text_value="")),
                name="catalog_productattribute_axis_has_values",
                violation_error_message="A variant axis takes values, not a number or a text.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product}: {self.attribute}"


class VariantOption(BaseModel):
    """The value a variant takes on one axis of its product (`Variant.options`, CAT.3).

    `attribute` repeats the value's attribute so that the database allows one value per
    attribute and variant; a composite foreign key (migration 0003) keeps it equal to the
    value's own attribute.
    """

    vendor_scope: ClassVar[str] = "variant__vendor"
    objects = VendorScopedManager()

    variant = models.ForeignKey(Variant, on_delete=models.CASCADE, related_name="variant_options")
    attribute = models.ForeignKey(Attribute, on_delete=models.PROTECT, related_name="+")
    value = models.ForeignKey(
        AttributeValue, on_delete=models.PROTECT, related_name="variant_options"
    )

    class Meta:
        ordering = ["variant", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["variant", "attribute"],
                name="catalog_variantoption_one_value_per_axis",
                violation_error_message="The variant already has a value for this attribute.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.variant}: {self.value}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.attribute_id is None and self.value_id is not None:
            self.attribute_id = self.value.attribute_id
        super().save(*args, **kwargs)
