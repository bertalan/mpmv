"""The public product page (CAT.7, spec 03 §3.2): what the storefront may show of a product.

Only published data leaves this module: a product that is published (or retired: its page stays
online, spec 03 Rules) of an active vendor, its published texts, its public price (never the
stored price of an on-request product) and its published companion. The view is a thin adapter.

The gallery is the product's `ProductImage` rows (CAT.4, `gallery`); related products and
the other sellers of the same product are CAT.15 (`related`).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import Count, Q, prefetch_related_objects

from apps.core import images as core_images
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
    caption: str = ""
    is_cover: bool = False


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
    """The product's images shown in `language` (D-18: a language with dedicated images shows
    only those, the others the neutral ones), the cover first, with alt text and caption in
    `language`, or in the source language when it has no published text."""
    rows = list(
        product.images.filter(variant__isnull=True)
        .select_related("image")
        .prefetch_related("translations")
    )
    # An image with no alt text of its own is described by the product title (D-18 fallback to
    # the source language comes first, in `resolve`): a content image never has an empty alt.
    named = translations.resolve(product.translations.all(), language).translation
    fallback = named.title if named is not None and named.title else str(product.sku)
    selected = engine.gallery_selection(rows, language)
    core_images.prefetch_renditions(row.image for row in selected)
    shown = []
    for row in selected:
        text = translations.resolve(row.translations.all(), language).translation
        shown.append(
            GalleryImage(
                image=row.image,
                alt=(text.alt_text if text is not None else "") or fallback,
                caption=text.caption if text is not None else "",
                is_cover=row.is_cover,
            )
        )
    return shown


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


RELATED_LIMIT = 4


def related(page: ProductPage, language: str | None = None) -> Sequence["services.ProductCard"]:
    """Up to four products sharing the most terms with the page's product (spec 03 §3.1), then
    in random order. Never the product itself, its upsells, its companion, a product that is
    not published or of a vendor that is not active. The other sellers of the same product
    are CAT.15 (not here)."""
    product = page.product
    term_ids = list(product.terms.values_list("pk", flat=True))
    if not term_ids:
        return ()
    excluded = [
        product.pk,
        *product.upsells.values_list("pk", flat=True),
        *([product.required_companion_id] if product.required_companion_id else []),
    ]
    chosen = list(
        services.listed_products()
        .exclude(pk__in=excluded)
        .filter(terms__pk__in=term_ids)
        .annotate(shared=Count("terms", filter=Q(terms__pk__in=term_ids)))
        .order_by("-shared", "?")[:RELATED_LIMIT]
    )
    cards = services.product_cards(chosen, language or page.requested)
    return [card for card in cards if card.url]


def gallery_items(page: ProductPage) -> list[dict[str, Any]]:
    """The gallery as the `components/gallery.html` context: the `image` renders as a
    `<picture>` (AVIF, WebP, JPEG). No rendition is looked up or created here."""
    items: list[dict[str, Any]] = []
    for item in page.gallery:
        items.append(
            {
                "image": item.image,
                "alt": item.alt,
                "caption": item.caption,
            }
        )
    return items
