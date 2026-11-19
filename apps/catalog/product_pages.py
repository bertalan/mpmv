"""The public product page (CAT.7, spec 03 §3.2): what the storefront may show of a product.

Only published data leaves this module: a product that is published (or retired: its page stays
online, spec 03 Rules) of an active vendor, its published texts, its public price (never the
stored price of an on-request product) and its published companion. The view is a thin adapter.

The gallery is the product's `ProductImage` rows (CAT.4, `gallery`); related products and
related products are `related`, the other sellers of the same article `other_sellers` (CAT.15).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import Case, Count, F, OuterRef, Q, Subquery, When, prefetch_related_objects
from django.utils.translation import gettext as _
from wagtail import hooks

from apps.core import images as core_images
from apps.core import translations
from apps.core.models import MediaImage
from apps.vendors import services as vendors
from apps.vendors.models import Vendor
from apps.vendors.return_engine import ReturnPolicy
from apps.vendors.return_services import effective_return_policy

from . import engine, services, slugs
from .models import Product, ProductImage, ProductStatus, ProductTranslation, SalesMode


@dataclass(frozen=True)
class VariantChoice:
    sku: str
    label: str
    price: Decimal | None
    stock_status: str
    selected: bool
    gtin: str = ""  # the variant's own, never the product's (CAT.23)
    mpn: str = ""
    image: MediaImage | None = None  # the variant's own cover, an existing rendition set
    image_alt: str = ""


@dataclass(frozen=True)
class GalleryImage:
    image: MediaImage
    alt: str
    caption: str = ""
    is_cover: bool = False


@dataclass(frozen=True)
class VariantGallery:
    """The gallery shown when a variant is selected (CAT.26): its own images first, then the
    product's. Only a variant with images of its own (in the page language) has one."""

    sku: str
    label: str
    images: list[GalleryImage]


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
    variant_galleries: list[VariantGallery] = field(default_factory=list)

    @property
    def gallery_skus(self) -> set[str]:
        return {entry.sku for entry in self.variant_galleries}

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


def _gallery_image(row: ProductImage, language: str, fallback: str) -> GalleryImage:
    text = translations.resolve(row.translations.all(), language).translation
    return GalleryImage(
        image=row.image,
        alt=(text.alt_text if text is not None else "") or fallback,
        caption=text.caption if text is not None else "",
        is_cover=row.is_cover,
    )


def _product_title(product: Product, language: str) -> str:
    # An image with no alt text of its own is described by the product title (D-18 fallback to
    # the source language comes first, in `resolve`): a content image never has an empty alt.
    named = translations.resolve(product.translations.all(), language).translation
    return named.title if named is not None and named.title else str(product.sku)


def _image_rows(product: Product) -> list[ProductImage]:
    """Every image row of the product, its variants' included, in two queries (never the
    prefetch cache: an image edited since is shown at once)."""
    return list(product.images.select_related("image").prefetch_related("translations"))


def gallery(product: Product, language: str) -> list[GalleryImage]:
    """The product's images shown in `language` (D-18: a language with dedicated images shows
    only those, the others the neutral ones), the cover first, with alt text and caption in
    `language`, or in the source language when it has no published text."""
    rows = [row for row in _image_rows(product) if row.variant_id is None]
    fallback = _product_title(product, language)
    selected = engine.gallery_selection(rows, language)
    core_images.prefetch_renditions(row.image for row in selected)
    return [_gallery_image(row, language, fallback) for row in selected]


def variant_galleries(
    product: Product, variants: Sequence[VariantChoice], language: str
) -> list[VariantGallery]:
    """The gallery of each variant that has images of its own in `language` (CAT.26): its own
    images, its cover leading, then the product's gallery, all under the D-18 language rule.
    A variant without any shows the product gallery (no entry). Existing renditions only."""
    if not variants:
        return []
    rows = _image_rows(product)
    shared = [row for row in rows if row.variant_id is None]
    own: dict[Any, list[ProductImage]] = {}
    for row in rows:
        if row.variant_id is not None:
            own.setdefault(row.variant_id, []).append(row)
    labels = {choice.sku: choice.label for choice in variants}
    found: list[tuple[str, list[ProductImage]]] = []
    for variant in product.variants.all():
        selected = engine.variant_gallery(
            own.get(variant.pk, []), shared, variant.cover_image, language
        )
        if selected:
            found.append((str(variant.sku), selected))
    core_images.prefetch_renditions(row.image for _sku, selected in found for row in selected)
    title = _product_title(product, language)
    return [
        VariantGallery(
            sku=sku,
            label=labels.get(sku, sku),
            images=[_gallery_image(row, language, labels.get(sku) or title) for row in selected],
        )
        for sku, selected in found
    ]


def _variants(product: Product, language: str, *, on_request: bool) -> list[VariantChoice]:
    rows = list(product.variants.all())
    if not rows:
        return []
    chosen = next((row for row in rows if row.is_default), rows[0])
    covers = [row.cover_image.image for row in rows if row.cover_image is not None]
    core_images.prefetch_renditions(covers)
    choices = []
    for row in rows:
        text = translations.resolve(row.translations.all(), language).translation
        cover = row.cover_image
        cover_text = (
            translations.resolve(cover.translations.all(), language).translation
            if cover is not None
            else None
        )
        label = text.label if text is not None else str(row.sku)
        choices.append(
            VariantChoice(
                sku=str(row.sku),
                label=label,
                price=None if on_request else engine.public_variant_price(product, row),
                stock_status=str(row.stock_status),
                selected=row is chosen,
                gtin=row.gtin,
                mpn=row.mpn,
                image=cover.image if cover is not None else None,
                image_alt=((cover_text.alt_text if cover_text is not None else "") or label),
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
    prefetch_related_objects(
        [product],
        "translations",
        "variants__translations",
        "variants__cover_image__image",
        "variants__cover_image__translations",
    )
    resolved = translations.resolve(product.translations.all(), language)
    text = resolved.translation
    if text is None or not text.slug or resolved.language is None:
        return None
    paths = _paths(product)
    # The vendor's on-request mode (chosen, or forced by D-19) overrides the product (VEN.6).
    on_request = vendors.effective_sales_mode(product) != SalesMode.BUY
    retired = product.status == ProductStatus.RETIRED
    variants = _variants(product, language, on_request=on_request)
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
        variants=variants,
        companion=_companion(product, language),
        gallery=gallery(product, language),
        variant_galleries=variant_galleries(product, variants, language),
    )


RELATED_LIMIT = 4


def related(page: ProductPage, language: str | None = None) -> Sequence["services.ProductCard"]:
    """Up to four products sharing the most terms with the page's product (spec 03 §3.1), then
    in random order. Never the product itself, its upsells, its companion, a product that is
    not published or of a vendor that is not active. The other sellers of the same article
    are `other_sellers`."""
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


OTHER_SELLERS_LIMIT = 6


def other_sellers(
    product: Product, language: str, limit: int = OTHER_SELLERS_LIMIT
) -> list["services.ProductCard"]:
    """The other vendors' offers of the same article (CAT.15, owner decision Q-33), as cards.

    The same article is the same GTIN, or the same brand and MPN (the brand is one shared entity,
    CAT.20, so spelling variants are one row; an MPN without a brand, or with another brand, is
    not a match, and neither is an empty identifier). Only published products of active vendors
    other than the product's own, with a published text and slug in `language` (no fallback to
    another language, I18N rules). Cheapest first (the public net price; products without one
    last), then by title. At most `limit` cards, in two queries plus the card texts, whatever
    the number of sellers."""
    match = Q()
    gtin = engine.normalise_identifier(product.gtin)
    mpn = engine.normalise_identifier(product.mpn)
    if gtin:
        match |= Q(gtin=gtin)
    if mpn and product.brand_id is not None:
        match |= Q(brand_id=product.brand_id, mpn=mpn)
    if not match or limit < 1:
        return []
    text = ProductTranslation.objects.filter(
        product=OuterRef("pk"), language=language, status=translations.PUBLISHED
    ).exclude(slug="")
    chosen = list(
        services.listed_products()
        .filter(match)
        .exclude(Q(pk=product.pk) | Q(vendor_id=product.vendor_id))
        .annotate(
            _title=Subquery(text.values("title")[:1]),
            _price=Case(When(sales_mode=SalesMode.BUY, then=F("price_net"))),
        )
        .filter(_title__isnull=False)
        .order_by(F("_price").asc(nulls_last=True), "_title", "pk")[:limit]
    )
    return [card for card in services.product_cards(chosen, language) if card.url]


def _items(images: Sequence[GalleryImage]) -> list[dict[str, Any]]:
    return [{"image": item.image, "alt": item.alt, "caption": item.caption} for item in images]


def gallery_items(page: ProductPage) -> list[dict[str, Any]]:
    """The gallery as the `components/gallery.html` context: the `image` renders as a
    `<picture>` (AVIF, WebP, JPEG). No rendition is looked up or created here."""
    return _items(page.gallery)


def variant_gallery_items(page: ProductPage) -> list[dict[str, Any]]:
    """The variant galleries for the template (CAT.26): `sku`, `label`, an `id` prefix unique on
    the page and the `images` in the `components/gallery.html` context."""
    return [
        {
            "sku": entry.sku,
            "label": entry.label,
            "id": f"variant-images-{number}",
            "images": _items(entry.images),
        }
        for number, entry in enumerate(page.variant_galleries, start=1)
    ]


# --- Specifications (THM.7 part 3): what the product's data says, as label and value rows


@dataclass(frozen=True)
class ReturnTerms:
    """The return terms the page shows (SEO.14): the very values the JSON-LD states."""

    days: int
    shop_pays: bool
    methods: list[str]
    link: str


def page_return_policy(vendor: Vendor) -> ReturnPolicy:
    """The vendor's effective return policy, read once per vendor instance: the visible terms and
    the JSON-LD of one page request share the same `page.vendor` and so the same single query."""
    cached: ReturnPolicy | None = vendor.__dict__.get("_page_return_policy")
    if cached is None:
        cached = vendor.__dict__["_page_return_policy"] = effective_return_policy(vendor)
    return cached


def return_terms(vendor: Vendor) -> ReturnTerms:
    """The vendor's approved return policy, or the default, as visible text data."""
    stated = page_return_policy(vendor)
    labels = {
        "post": _("By post"),
        "drop_off": _("Drop-off point"),
        "in_store": _("In store"),
    }
    return ReturnTerms(
        days=stated.return_window_days,
        shop_pays=stated.return_shipping_paid_by == "vendor",
        methods=[labels[m] for m in stated.return_methods if m in labels],
        link=stated.returns_page_url,
    )


@dataclass(frozen=True)
class SpecRow:
    label: str
    value: str


def _number(value: Decimal) -> str:
    """A measure without trailing zeros: 8.500 shows as 8.5, 10.000 as 10."""
    return format(value.normalize(), "f")


def _attribute_row(row: Any, language: str) -> SpecRow | None:
    attribute = row.attribute
    name = translations.resolve(attribute.translations.all(), language).translation
    label = name.name if name is not None else str(attribute.code)
    if row.numeric_value is not None:
        value = _number(row.numeric_value)
        if attribute.unit:
            value = f"{value} {attribute.unit}"
    elif row.text_value:
        value = row.text_value
    else:
        labels = []
        for choice in row.values.all():
            text = translations.resolve(choice.translations.all(), language).translation
            labels.append(text.label if text is not None else str(choice.value))
        value = ", ".join(labels)
    return SpecRow(label, value) if value else None


def specifications(product: Product, language: str) -> list[SpecRow]:
    """The specification rows of `product`: its attributes (not the variant axes, which the
    buy box offers), then weight, dimensions, GTIN and MPN. Empty when it has none of them."""
    rows: list[SpecRow] = []
    attributes = product.attributes.filter(is_variant_axis=False).prefetch_related(
        "attribute__translations", "values__translations"
    )
    for row in attributes.select_related("attribute"):
        found = _attribute_row(row, language)
        if found is not None:
            rows.append(found)
    if product.weight_kg is not None:
        rows.append(SpecRow(_("Weight"), f"{_number(product.weight_kg)} kg"))
    # Sizes in mm (CAT.22) take over from the older centimetre fields, one row per side.
    for label, mm, cm in (
        (_("Length"), product.length_mm, product.length_cm),
        (_("Width"), product.width_mm, product.width_cm),
        (_("Height"), product.height_mm, product.height_cm),
    ):
        if mm is not None:
            rows.append(SpecRow(label, f"{mm} mm"))
        elif cm is not None:
            rows.append(SpecRow(label, f"{_number(cm)} cm"))
    texts = translations.resolve(product.translations.all(), language).translation
    if texts is not None and texts.material:
        rows.append(SpecRow(_("Material"), texts.material))
    if texts is not None and texts.colour:
        rows.append(SpecRow(_("Colour"), texts.colour))
    if product.country_of_origin:
        rows.append(SpecRow(_("Country of origin"), product.country_of_origin))
    if product.gtin:
        rows.append(SpecRow(_("GTIN"), product.gtin))
    if product.mpn:
        rows.append(SpecRow(_("MPN"), product.mpn))
    return rows


# --- Fitment (THM.7 part 3): the vehicles or applications the product fits (FIT.2)

# The Wagtail hook the fitment app registers: `fn(product, language)` returns a
# `FitmentSummary` or None. A hook keeps the dependency one way (fitment reads catalog).
FITMENT_HOOK = "catalog_product_fitment"
FITMENT_VISIBLE = 12  # entries shown before the "show all" disclosure


@dataclass(frozen=True)
class FitmentEntry:
    label: str  # the model or variant, or "All models" for a make row
    years: str = ""
    note: str = ""
    excluded: bool = False


@dataclass(frozen=True)
class FitmentGroup:
    label: str  # the make
    entries: tuple[FitmentEntry, ...]


@dataclass(frozen=True)
class FitmentSummary:
    universal: bool = False
    groups: tuple[FitmentGroup, ...] = ()

    @property
    def visible(self) -> tuple[FitmentGroup, ...]:
        return _split(self.groups)[0]

    @property
    def more(self) -> tuple[FitmentGroup, ...]:
        return _split(self.groups)[1]

    @property
    def more_count(self) -> int:
        return sum(len(group.entries) for group in self.more)


def _split(
    groups: tuple[FitmentGroup, ...],
) -> tuple[tuple[FitmentGroup, ...], tuple[FitmentGroup, ...]]:
    """The groups cut after `FITMENT_VISIBLE` entries; a group may continue in the rest."""
    visible: list[FitmentGroup] = []
    more: list[FitmentGroup] = []
    room = FITMENT_VISIBLE
    for group in groups:
        head, tail = group.entries[:room], group.entries[room:]
        room -= len(head)
        if head:
            visible.append(FitmentGroup(group.label, head))
        if tail:
            more.append(FitmentGroup(group.label, tail))
    return tuple(visible), tuple(more)


def fitment(product: Product, language: str) -> FitmentSummary | None:
    """What the product declares it fits, from the registered hook; None when nothing."""
    for provider in hooks.get_hooks(FITMENT_HOOK):
        summary: FitmentSummary | None = provider(product, language)
        if summary is not None and (summary.universal or summary.groups):
            return summary
    return None
