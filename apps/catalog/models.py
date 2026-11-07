"""Products, variants and their translations (spec 03 §3.2, CAT.1).

The category tree, taxonomies, terms and brands (CAT.2) live in `taxonomy.py`; products link
to them here, and to the typed attributes of `attributes.py` (CAT.3) through `ProductAttribute`
and `VariantOption`. Fields owned by later tasks are added there: images (CAT.4), slug
generation and SlugHistory (CAT.5), `gpsr` (CMP.3).
Prices change only through `services.set_product_price` and `services.set_variant_price`, which
audit them; the status changes through the publishing services of CAT.10.

Vendor scoping (CORE.3): every model here uses `VendorScopedManager`. `Variant.vendor` copies
its product's vendor so that the database can enforce one SKU per vendor among variants; a
composite foreign key (migration 0001) keeps the copy equal to the product's vendor, and moves
it along when the product changes vendor.
"""

from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Trim, Upper

from apps.core.fields import MoneyField
from apps.core.models import BaseModel, TranslationModel, resolve_translation
from apps.core.translations import Resolved
from apps.vendors.models import Vendor
from apps.vendors.scoping import VendorScopedManager

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
    "Product",
    "ProductAttribute",
    "ProductKind",
    "ProductStatus",
    "ProductTranslation",
    "SalesMode",
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


def _sku_normalized(name: str) -> models.CheckConstraint:
    return models.CheckConstraint(
        condition=models.Q(sku=Upper(Trim("sku"))) & ~models.Q(sku=""),
        name=name,
        violation_error_message="The SKU must be upper case, without surrounding spaces.",
    )


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
    sku = SkuField(help_text="Unique per vendor; stored upper case.")
    kind = models.CharField(max_length=16, choices=ProductKind.choices, default=ProductKind.SIMPLE)
    # Changed only by the publishing services (CAT.10), which check the publishing rules.
    status = models.CharField(
        max_length=16, choices=ProductStatus.choices, default=ProductStatus.DRAFT, editable=False
    )
    sales_mode = models.CharField(max_length=16, choices=SalesMode.choices, default=SalesMode.BUY)
    fiscal_category = models.CharField(
        max_length=16, choices=FiscalCategory.choices, default=FiscalCategory.STANDARD
    )

    price_net = MoneyField("net price", help_text="EUR, VAT excluded.")
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
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "sku"],
                name="catalog_product_vendor_sku_unique",
                violation_error_message="This vendor already has a product with this SKU.",
            ),
            # Target of the variants' composite foreign key (product, vendor).
            models.UniqueConstraint(
                fields=["id", "vendor"], name="catalog_product_id_vendor_unique"
            ),
            _sku_normalized("catalog_product_sku_normalized"),
            _choice("kind", ProductKind, "catalog_product_kind_valid"),
            _choice("status", ProductStatus, "catalog_product_status_valid"),
            _choice("sales_mode", SalesMode, "catalog_product_sales_mode_valid"),
            _choice("fiscal_category", FiscalCategory, "catalog_product_fiscal_category_valid"),
            _choice("stock_policy", StockPolicy, "catalog_product_stock_policy_valid"),
            _choice("stock_status", StockStatus, "catalog_product_stock_status_valid"),
            _not_negative("price_net", "price", "catalog_product_price_net_not_negative"),
            models.CheckConstraint(
                condition=models.Q(sale_price_net__isnull=True)
                | (
                    models.Q(sale_price_net__gte=0)
                    & models.Q(sale_price_net__lt=models.F("price_net"))
                ),
                name="catalog_product_sale_price_below_price",
                violation_error_message="The sale price must be lower than the price.",
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
    # Derived {title}-{lang}-{sku} with `build_product_slug` (CAT.5); empty until generated.
    slug = models.SlugField(max_length=80, blank=True)
    seo_title = models.CharField("SEO title", max_length=255, blank=True)
    seo_description = models.TextField("SEO description", blank=True)

    class Meta(TranslationModel.Meta):
        ordering = ["product", "language"]
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
    sku = SkuField(help_text="Unique per vendor; stored upper case.")
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
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "sku"],
                name="catalog_variant_vendor_sku_unique",
                violation_error_message="This vendor already has a variant with this SKU.",
            ),
            models.UniqueConstraint(
                fields=["product"],
                condition=models.Q(is_default=True),
                name="catalog_variant_one_default_per_product",
                violation_error_message="The product already has a default variant.",
            ),
            _sku_normalized("catalog_variant_sku_normalized"),
            _choice("stock_status", StockStatus, "catalog_variant_stock_status_valid"),
            _not_negative(
                "price_net_override", "price override", "catalog_variant_price_not_negative"
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
        # `vendor` is not a form field, so forms skip the (vendor, sku) constraint: check it here.
        if self.vendor_id is None or not self.sku:
            return
        taken = Variant.objects.filter(vendor_id=self.vendor_id, sku=self.sku).exclude(pk=self.pk)
        if taken.exists():
            raise ValidationError({"sku": "This vendor already has a variant with this SKU."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        self._sync_vendor()
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
