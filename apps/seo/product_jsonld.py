"""The JSON-LD graph of a product page (SEO.1): gathers the data of a `ProductPage` and hands it
to the pure `product_graph` functions. Reads catalog, vendors and shipping; writes nothing.

Rules (spec 10 "Product node"):
- No `offers` for a product that is not sold online (`page.price is None`: on request, Q-31, or
  catalogue only, Q-33). The price is the consumer price with VAT of TAX.12
  (`apps.tax.prices.consumer_price(...).gross`, `valueAddedTaxIncluded: true`), for the same
  request as the visible price, so the two always match. When TAX.12 cannot decide it
  (`PriceUnavailable`) the offer has no price, never a net one.
- `gtin` and `mpn` come from the product fields, `sku` is the vendor's SKU label.
- The seller is the vendor's `Organization`, never the operator.
- `priceValidUntil` only from a sale end date: the page does not price a sale yet, so none.
- Shipping: one node per method of the vendor with a rate for the product's weight, its countries
  in one region; a method without band for that weight, or a product without weight, has none.
- Returns: one consumer policy per vendor for the countries it ships to (whatever the product's
  weight), with the 14-day withdrawal window of Directive 2011/83/EU as the floor (the vendor's
  own window is not modelled yet), the customer paying the return shipping and a lack of
  conformity free.
- Variants: `variesBy` from the attributes the variants take a value on, a `@id` per variant;
  no variant URL (the page picks a variant in a form, no URL selects one), and no per-variant
  gtin, mpn or image (the variant model has none yet).
- Brand: the CAT.20 brand graph (Brand and manufacturer nodes) joins the page graph, so the
  `brand` reference resolves.
"""

import logging
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from django.conf import settings
from django.http import HttpRequest
from django.utils import translation

from apps.catalog import engine as catalog_engine
from apps.catalog import services as catalog
from apps.catalog import slugs
from apps.catalog.models import VariantOption
from apps.catalog.product_pages import GalleryImage, ProductPage, page_return_policy
from apps.core.money import CURRENCY
from apps.core.services import NoPlatformOperator, current_operator
from apps.shipping import engine as shipping_engine
from apps.shipping import services as shipping
from apps.tax.prices import PriceUnavailable, consumer_price, estimated_country
from apps.vendors.models import Vendor

from . import product_graph as pg
from . import seller as seller_name

logger = logging.getLogger(__name__)

WITHDRAWAL_DAYS = 14


def _operator_country() -> str:
    """The platform's country (SEO.11), for a vendor that states none; empty if unconfigured."""
    try:
        return str(current_operator().country or "")
    except NoPlatformOperator:
        return ""


VARIES_BY_PROPERTIES = frozenset({"color", "size", "material", "pattern"})


def _varies_by(page: ProductPage, language: str) -> tuple[str, ...]:
    """What the variants differ in: the schema.org property for the usual codes, else the
    attribute's name in the page language."""
    attributes = {
        option.attribute_id: option.attribute
        for option in VariantOption.objects.filter(variant__product=page.product)
        .select_related("attribute")
        .order_by("attribute__code")
    }
    names: list[str] = []
    for attribute in attributes.values():
        code = str(attribute.code)
        if code in VARIES_BY_PROPERTIES:
            names.append(f"{pg.SCHEMA}/{code}")
        else:
            resolved = attribute.translation(language).translation
            names.append(str(resolved.name) if resolved is not None else code)
    return tuple(names)


def _vendor_url(page: ProductPage, site: str) -> str:
    path = slugs.vendor_path(page.shown, page.vendor.slug, default_language=settings.LANGUAGE_CODE)
    return f"{site}{path}"


def _image(gallery_image: GalleryImage, site: str) -> pg.ImageData:
    image = gallery_image.image
    url = str(image.file.url)
    credit = image.credit
    author = str(image.author or "").strip()
    return pg.ImageData(
        url=url if url.startswith("http") else f"{site}{url}",
        width=image.width or None,
        height=image.height or None,
        caption=gallery_image.alt,
        license=str(image.licence.url) if image.licence is not None else "",
        acquire_license_page=str(image.source_url or ""),
        credit_text=credit.text if credit is not None else str(image.credit_text or ""),
        creator=author,
        copyright_notice=f"\u00a9 {author}" if author else "",
    )


def _home(site: str, language: str) -> str:
    return f"{site}/" if language == settings.LANGUAGE_CODE else f"{site}/{language}/"


def _crumbs(page: ProductPage, site: str, url: str) -> list[pg.Crumb]:
    crumbs = [pg.Crumb(_home_label(page.shown), _home(site, page.shown))]
    category = page.product.category
    if category is not None:
        for node in [*category.get_ancestors(), category]:
            path = catalog.category_url(node, page.shown)
            resolved = node.translation(page.shown).translation
            name = str(resolved.name) if resolved is not None else ""
            if path and name:
                crumbs.append(pg.Crumb(name, f"{site}{path}"))
    crumbs.append(pg.Crumb(page.title, url))
    return crumbs


def _home_label(language: str) -> str:
    with translation.override(language):
        return str(translation.gettext("Home"))


def _shipping_rate(
    page: ProductPage, net: Decimal, zone: frozenset[str] | set[str], request: HttpRequest | None
) -> Decimal | None:
    """The shipping price with VAT (TAX.12; shipping follows the goods, TAX.17) for the visitor's
    destination; None when that destination is not in the zone or TAX.12 cannot decide it,
    so the node carries no rate rather than a net one."""
    country, _estimated = estimated_country(request)
    if shipping_engine.WORLD not in zone and country not in zone:
        return None
    try:
        return consumer_price(
            net, page.product.fiscal_category, vendor=page.vendor, request=request
        ).gross
    except PriceUnavailable as exc:
        logger.warning("JSON-LD shipping rate omitted (%s): %s", page.product.fiscal_category, exc)
        return None


def _shipping_nodes(
    page: ProductPage, url: str, language: str, request: HttpRequest | None = None
) -> tuple[list[dict[str, Any]], list[str], set[str]]:
    """Nodes, their ids and the countries of the vendor's zones (empty set: nothing known).
    The countries do not depend on the product's weight; the shipping nodes do."""
    weight = page.product.weight_kg
    nodes: list[dict[str, Any]] = []
    countries: set[str] = set()
    config = shipping.shipping_config_snap(page.vendor, language=language)
    for method in config.methods:
        if not method.carrier_active:
            continue
        zone = method.zone.countries | method.zone.group_countries
        world = shipping_engine.WORLD in zone
        listed = set() if world else set(zone)
        countries |= listed
        band = None if weight is None else next((b for b in method.bands if b.holds(weight)), None)
        if band is None:
            continue
        rate = _shipping_rate(page, band.price, zone, request)
        if rate is None:
            continue  # shippingRate is required by Google: no gross rate, no node
        nodes.append(
            pg.shipping_node(
                node_id=f"{url}#shipping-{method.key}",
                countries=sorted(listed),
                rate=rate,
                currency=CURRENCY,
                min_days=method.min_delivery_days,
                max_days=method.max_delivery_days,
            )
        )
    return nodes, [str(node["@id"]) for node in nodes], countries


def _offer_price(page: ProductPage, net: Decimal, request: HttpRequest | None) -> Decimal | None:
    """The consumer price with VAT (TAX.12) the page shows for `net`; None when TAX.12 cannot
    decide it, so the offer carries no price rather than a net one."""
    try:
        return consumer_price(
            net, page.product.fiscal_category, vendor=page.vendor, request=request
        ).gross
    except PriceUnavailable as exc:
        logger.warning("JSON-LD offer without a price (%s): %s", page.product.fiscal_category, exc)
        return None


def _offer(
    page: ProductPage,
    price: Decimal | None,
    status: str,
    *,
    url: str,
    node_id: str,
    refs: dict[str, Any],
    request: HttpRequest | None,
) -> pg.OfferData | None:
    if price is None:
        return None
    gross = _offer_price(page, price, request)
    if gross is None:
        return None
    return pg.OfferData(
        price=gross,
        currency=CURRENCY,
        availability="discontinued" if page.retired else status,
        url=url,
        price_includes_vat=True,
        node_id=node_id,
        **refs,
    )


def build_graph(
    page: ProductPage, *, site: str, request: HttpRequest | None = None
) -> dict[str, Any]:
    """The product page's graph; `site` is the origin, like `https://shop.example`. `request`
    gives the price destination, as for the visible price (TAX.12)."""
    language = page.shown
    url = f"{site}{page.canonical_path}"
    vendor: Vendor = page.vendor
    vendor_url = _vendor_url(page, site)
    sold = page.price is not None

    public_name = seller_name.public_name(vendor)
    seller = (
        pg.organization_node(node_id=f"{vendor_url}#organization", name=public_name, url=vendor_url)
        if public_name
        else None
    )
    shipping_nodes: list[dict[str, Any]] = []
    shipping_ids: list[str] = []
    return_policy: dict[str, Any] | None = None
    if sold:
        shipping_nodes, shipping_ids, countries = _shipping_nodes(page, url, language, request)
        if countries:
            stated = page_return_policy(vendor)  # one query; approved, else the shop-pays default
            return_policy = pg.return_policy_node(
                node_id=f"{vendor_url}#return-policy",
                countries=sorted(countries),
                return_country=stated.return_country or _operator_country(),
                days=stated.return_window_days,
                link=stated.returns_page_url,
                paid_by=stated.return_shipping_paid_by,
                methods=stated.return_methods,
            )
    refs: dict[str, Any] = {
        "seller_id": str(seller["@id"]) if seller else "",
        "return_policy_id": str(return_policy["@id"]) if return_policy else "",
        "shipping_ids": tuple(shipping_ids),
    }
    variants = tuple(
        pg.VariantData(
            sku=choice.sku,
            name=f"{page.title} {choice.label}".strip(),
            offer=_offer(
                page,
                choice.price,
                choice.stock_status,
                url=url,
                node_id=f"{url}#offer-{quote(choice.sku, safe='')}",
                refs=refs,
                request=request,
            ),
        )
        for choice in page.variants
    )
    brand = page.product.brand
    brand_id = maker_id = ""
    linked: list[dict[str, Any]] = []
    if brand is not None and brand.status == brand.Status.APPROVED:
        brand_id = catalog.brand_node_id(brand, site=site)
        if brand_id:
            linked = catalog.brand_graph(brand, language, site=site)["@graph"]
            maker = brand.manufacturer
            maker_id = f"{site}/#manufacturer-{maker.public_id}" if maker is not None else ""
    # SEO.10: a producer vendor fills the brand and manufacturer CAT.20 leaves empty; the vendor
    # is named by its Organization @id, and only when it has a public seller node (never a person).
    vendor_id = str(seller["@id"]) if seller else ""
    role = catalog_engine.effective_vendor_role(page.product)
    brand_id, maker_id = pg.vendor_role_refs(
        role,
        brand_id=brand_id,
        maker_id=maker_id,
        vendor_id=vendor_id,
    )
    names_vendor = bool(vendor_id) and vendor_id in (brand_id, maker_id)
    data = pg.ProductData(
        url=url,
        name=page.title,
        sku=str(page.product.sku),
        product_id=str(page.product.public_id),
        description=catalog_engine.plain_description(page.description or page.short_description),
        gtin=str(page.product.gtin or ""),
        mpn=str(page.product.mpn or ""),
        brand_id=brand_id,
        manufacturer_id=maker_id,
        images=tuple(_image(image, site) for image in page.gallery),
        weight_kg=page.product.weight_kg,
        offer=_offer(
            page,
            page.price,
            page.stock_status,
            url=url,
            node_id=f"{url}#offer",
            refs=refs,
            request=request,
        ),
        variants=variants,
        varies_by=_varies_by(page, language) if variants else (),
    )
    has_offer = data.offer is not None or any(v.offer is not None for v in variants)
    if not has_offer:
        # Nothing references the return policy or shipping nodes: no orphans. The seller node
        # stays only when the brand or manufacturer refers to it (SEO.10).
        seller = seller if names_vendor else None
        return_policy, shipping_nodes = None, []
    return pg.product_graph(
        product=data,
        breadcrumb=pg.breadcrumb_node(page_url=url, items=_crumbs(page, site, url)),
        seller=seller if sold or names_vendor else None,
        return_policy=return_policy,
        shipping=shipping_nodes,
        linked=linked,
    )


def graph_text(page: ProductPage, *, site: str, request: HttpRequest | None = None) -> str:
    return pg.graph_text(build_graph(page, site=site, request=request))
