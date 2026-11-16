"""The JSON-LD graph of a product page (SEO.1): gathers the data of a `ProductPage` and hands it
to the pure `product_graph` functions. Reads catalog, vendors and shipping; writes nothing.

Rules (spec 10 "Product node"):
- No `offers` for a product that is not sold online (`page.price is None`: on request, Q-31, or
  catalogue only, Q-33). The price is the public price of the page, never recomputed here. It is
  the interim net price until TAX.12 supplies the gross one, so the specification says
  `valueAddedTaxIncluded: false` (TODO(TAX.12): switch the flag with the gross amount).
- `gtin` and `mpn` come from the product fields, `sku` is the vendor's SKU label.
- The seller is the vendor's `Organization`, never the operator.
- `priceValidUntil` only from a sale end date: the page does not price a sale yet, so none.
- Shipping: one node per method of the vendor with a rate for the product's weight, its countries
  in one region; a method without band for that weight, or a product without weight, has none.
- Returns: one policy per vendor for the countries it ships to, with the 14-day withdrawal
  window of Directive 2011/83/EU as the floor (the vendor's own window is not modelled yet).
"""

from decimal import Decimal
from typing import Any

from django.conf import settings
from django.utils import translation

from apps.catalog import engine as catalog_engine
from apps.catalog import services as catalog
from apps.catalog import slugs
from apps.catalog.product_pages import GalleryImage, ProductPage
from apps.core.money import CURRENCY
from apps.shipping import engine as shipping_engine
from apps.shipping import services as shipping
from apps.vendors.models import Vendor

from . import product_graph as pg

WITHDRAWAL_DAYS = 14


def _vendor_url(page: ProductPage, site: str) -> str:
    path = slugs.vendor_path(page.shown, page.vendor.slug, default_language=settings.LANGUAGE_CODE)
    return f"{site}{path}"


def _image(gallery_image: GalleryImage, site: str) -> pg.ImageData:
    image = gallery_image.image
    licence = getattr(image, "licence", None)
    url = str(image.file.url)
    return pg.ImageData(
        url=url if url.startswith("http") else f"{site}{url}",
        width=image.width or None,
        height=image.height or None,
        caption=gallery_image.alt,
        license=str(getattr(licence, "url", "") or ""),
        credit_text=str(image.credit_text or ""),
        creator=str(image.author or ""),
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


def _shipping_nodes(
    page: ProductPage, url: str, language: str
) -> tuple[list[dict[str, Any]], list[str], set[str]]:
    """Nodes, their ids and the countries of the vendor's zones (empty set: nothing known)."""
    weight = page.product.weight_kg
    if weight is None:
        return [], [], set()
    nodes: list[dict[str, Any]] = []
    countries: set[str] = set()
    config = shipping.shipping_config_snap(page.vendor, language=language)
    for method in config.methods:
        band = next((b for b in method.bands if b.holds(weight)), None)
        if band is None or not method.carrier_active:
            continue
        zone = method.zone.countries | method.zone.group_countries
        world = shipping_engine.WORLD in zone
        listed = set() if world else set(zone)
        countries |= listed
        nodes.append(
            pg.shipping_node(
                node_id=f"{url}#shipping-{method.key}",
                countries=sorted(listed),
                rate=band.price,
                currency=CURRENCY,
                min_days=method.min_delivery_days,
                max_days=method.max_delivery_days,
            )
        )
    return nodes, [str(node["@id"]) for node in nodes], countries


def _offer(
    page: ProductPage,
    price: Decimal | None,
    status: str,
    *,
    url: str,
    node_id: str,
    refs: dict[str, Any],
) -> pg.OfferData | None:
    if price is None:
        return None
    return pg.OfferData(
        price=price,
        currency=CURRENCY,
        availability="discontinued" if page.retired else status,
        url=url,
        price_includes_vat=False,
        node_id=node_id,
        **refs,
    )


def build_graph(page: ProductPage, *, site: str) -> dict[str, Any]:
    """The product page's graph; `site` is the origin, like `https://shop.example`."""
    language = page.shown
    url = f"{site}{page.canonical_path}"
    vendor: Vendor = page.vendor
    vendor_url = _vendor_url(page, site)
    sold = page.price is not None

    seller = pg.organization_node(
        node_id=f"{vendor_url}#organization", name=vendor.display_name, url=vendor_url
    )
    shipping_nodes: list[dict[str, Any]] = []
    shipping_ids: list[str] = []
    return_policy: dict[str, Any] | None = None
    if sold:
        shipping_nodes, shipping_ids, countries = _shipping_nodes(page, url, language)
        if countries:
            return_policy = pg.return_policy_node(
                node_id=f"{vendor_url}#return-policy",
                countries=sorted(countries),
                return_country=str(vendor.country or ""),
                days=WITHDRAWAL_DAYS,
                link=vendor_url,
            )
    refs: dict[str, Any] = {
        "seller_id": str(seller["@id"]),
        "return_policy_id": str(return_policy["@id"]) if return_policy else "",
        "shipping_ids": tuple(shipping_ids),
    }
    variants = tuple(
        pg.VariantData(
            sku=choice.sku,
            name=f"{page.title} {choice.label}".strip(),
            url=url,
            offer=_offer(
                page,
                choice.price,
                choice.stock_status,
                url=url,
                node_id=f"{url}#offer-{choice.sku}",
                refs=refs,
            ),
        )
        for choice in page.variants
    )
    brand = page.product.brand
    brand_id = ""
    if brand is not None and brand.status == brand.Status.APPROVED:
        brand_id = catalog.brand_node_id(brand, site=site)
    data = pg.ProductData(
        url=url,
        name=page.title,
        sku=str(page.product.sku),
        product_id=str(page.product.public_id),
        language=language,
        description=catalog_engine.plain_description(page.description or page.short_description),
        gtin=str(page.product.gtin or ""),
        mpn=str(page.product.mpn or ""),
        brand_id=brand_id,
        images=tuple(_image(image, site) for image in page.gallery),
        weight_kg=page.product.weight_kg,
        offer=_offer(
            page, page.price, page.stock_status, url=url, node_id=f"{url}#offer", refs=refs
        ),
        variants=variants,
    )
    return pg.product_graph(
        product=data,
        breadcrumb=pg.breadcrumb_node(page_url=url, items=_crumbs(page, site, url)),
        seller=seller if sold else None,
        return_policy=return_policy,
        shipping=shipping_nodes,
    )


def graph_text(page: ProductPage, *, site: str) -> str:
    return pg.graph_text(build_graph(page, site=site))
