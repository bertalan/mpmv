"""The public product page (CAT.7, spec 03 §3.2): what the storefront may show of a product.

Only published data leaves this module: a product that is published (or retired: its page stays
online, spec 03 Rules) of an active vendor, its published texts, its public price (never the
stored price of an on-request product) and its published companion. The view is a thin adapter.

The gallery has no source yet (`gallery`: CAT.4 adds the product images); related products and
the other sellers of the same product are CAT.15 (`related`).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db.models import prefetch_related_objects

from apps.core import translations
from apps.core.models import MediaImage
from apps.vendors import services as vendors
from apps.vendors.models import Vendor

from . import engine, services, slugs
from .models import Product, ProductStatus, ProductTranslation, SalesMode


@dataclass(frozen=True)
class VariantChoice:
    sku: str
    label: str
    price: Decimal | None
    stock_status: str
    selected: bool


@dataclass(frozen=True)
class GalleryImage:
    image: MediaImage
    alt: str


@dataclass(frozen=True)
class ProductPage:
    product: Product
    vendor: Vendor
    title: str
    short_description: str
    description: str  # sanitised HTML
    requested: str  # the page language
    shown: str  # the language of the texts shown
    paths: dict[str, str]  # the page path in each language it is published in
    price: Decimal | None  # None: "Price on request" (never a stored price)
    on_request: bool
    purchasable: bool
    retired: bool
    stock_status: str
    variants: list[VariantChoice]
    companion: "services.ProductCard | None"
    gallery: list[GalleryImage]

    @property
    def is_fallback(self) -> bool:
        return self.shown != self.requested

    @property
    def canonical_path(self) -> str:
        return self.paths[self.shown]


PUBLIC_STATUSES = (ProductStatus.PUBLISHED, ProductStatus.RETIRED)


def _paths(product: Product) -> dict[str, str]:
    found = {
        row.language: row.slug
        for row in product.translations.all()
        if row.status == translations.PUBLISHED and row.slug
    }
    return {
        lang: slugs.product_path(lang, found[lang], default_language=settings.LANGUAGE_CODE)
        for lang in (str(code) for code, _name in settings.LANGUAGES)
        if lang in found
    }


def gallery(product: Product, language: str) -> list[GalleryImage]:
    """The product's images with their alt text; empty until CAT.4 adds the image model."""
    return []


def _variants(product: Product, language: str, *, on_request: bool) -> list[VariantChoice]:
    rows = list(product.variants.all())
    if not rows:
        return []
    chosen = next((row for row in rows if row.is_default), rows[0])
    choices = []
    for row in rows:
        text = translations.resolve(row.translations.all(), language).translation
        choices.append(
            VariantChoice(
                sku=str(row.sku),
                label=text.label if text is not None else str(row.sku),
                price=None if on_request else engine.public_variant_price(product, row),
                stock_status=str(row.stock_status),
                selected=row is chosen,
            )
        )
    return choices


def _companion(product: Product, language: str) -> "services.ProductCard | None":
    companion = product.required_companion
    if (
        companion is None
        or companion.status != ProductStatus.PUBLISHED
        or companion.vendor.status != Vendor.Status.ACTIVE
    ):
        return None
    card = services.product_cards([companion], language)[0]
    return card if card.url else None


def find(slug: str, language: str) -> ProductPage | None:
    """The page of the product whose published `slug` is in `language`, or in the source
    language when the product has no published text in `language` (the fallback page).
    None for a draft, a hidden product, an inactive vendor or an unknown slug."""
    source = translations.source_language()
    rows = (
        ProductTranslation.objects.filter(
            slug=slug,
            status=translations.PUBLISHED,
            language__in={language, source},
            product__status__in=PUBLIC_STATUSES,
            product__vendor__status=Vendor.Status.ACTIVE,
        )
        .select_related(
            "product__vendor__connected_account",
            "product__brand",
            "product__required_companion",
        )
        .order_by("language")
    )
    found = next((row for row in rows if row.language == language), None)
    if found is None:
        found = next((row for row in rows if row.language == source), None)
    if found is None:
        return None
    return _build(found.product, language)


def _build(product: Product, language: str) -> ProductPage | None:
    prefetch_related_objects([product], "translations", "variants__translations")
    resolved = translations.resolve(product.translations.all(), language)
    text = resolved.translation
    if text is None or not text.slug or resolved.language is None:
        return None
    paths = _paths(product)
    # The vendor's on-request mode (chosen, or forced by D-19) overrides the product (VEN.6).
    on_request = vendors.effective_sales_mode(product) != SalesMode.BUY
    retired = product.status == ProductStatus.RETIRED
    return ProductPage(
        product=product,
        vendor=product.vendor,
        title=text.title,
        short_description=text.short_description,
        description=services._clean_rich_text(text.description).strip(),
        requested=language,
        shown=resolved.language,
        paths=paths,
        price=None if on_request else engine.public_price(product),
        on_request=on_request,
        purchasable=not on_request and not retired,
        retired=retired,
        stock_status=str(product.stock_status),
        variants=_variants(product, language, on_request=on_request),
        companion=_companion(product, language),
        gallery=gallery(product, language),
    )


def structured_data(page: ProductPage, *, site: str) -> str:
    """The product page's JSON-LD graph as safe script text."""
    brand = page.product.brand
    brand_node_id = ""
    if brand is not None and brand.status == brand.Status.APPROVED:
        brand_node_id = services.brand_node_id(brand, site=site)
    node = engine.product_node(
        url=f"{site}{page.canonical_path}",
        name=page.title,
        sku=str(page.product.sku),
        description=engine.plain_description(page.description or page.short_description),
        brand_id=brand_node_id,
        mpn=str(page.product.mpn),
        gtin=str(page.product.gtin),
    )
    return engine.jsonld_text(engine.brand_graph([node]))


def related(page: ProductPage) -> Sequence["services.ProductCard"]:
    """Related products and other sellers: CAT.15 fills this."""
    return ()
