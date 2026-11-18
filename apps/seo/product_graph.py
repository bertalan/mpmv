"""The Product part of the structured data graph (SEO.1, spec 10 "Product node").

Pure functions, no ORM: plain data in, schema.org nodes out. SEO.7's `page_graph` places these
nodes into the page graph; `product_graph` is the product page's graph until then. Nodes have
stable `@id`s and reference each other by `@id`; an empty value is left out (a property
without a real value is dropped, never defaulted).

No price anywhere for a product that is not sold online (Q-31, Q-33): the caller passes
`offer=None` and the node has no `offers`, no price specification and no seller.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Final
from urllib.parse import quote

from apps.catalog.engine import jsonld_text

SCHEMA: Final = "https://schema.org"
_AVAILABILITY: Final = {
    "in_stock": "InStock",
    "out_of_stock": "OutOfStock",
    "on_backorder": "BackOrder",
    "discontinued": "Discontinued",
}
_GTIN_KEYS: Final = {8: "gtin8", 12: "gtin12", 13: "gtin13", 14: "gtin14"}
FINITE_WINDOW: Final = f"{SCHEMA}/MerchantReturnFiniteReturnWindow"
# A lack of conformity is free by law; who pays a withdrawal return is stated only when the
# vendor's approved policy says so (SEO.11).
CUSTOMER_PAYS_RETURN: Final = f"{SCHEMA}/ReturnShippingFees"
FREE_RETURN: Final = f"{SCHEMA}/FreeReturn"
_RETURN_METHODS: Final = {
    "post": "ReturnByMail",
    "drop_off": "ReturnAtKiosk",
    "in_store": "ReturnInStore",
}


@dataclass(frozen=True)
class ImageData:
    url: str
    width: int | None = None
    height: int | None = None
    caption: str = ""
    license: str = ""
    acquire_license_page: str = ""
    credit_text: str = ""
    creator: str = ""
    copyright_notice: str = ""


@dataclass(frozen=True)
class OfferData:
    """An offer. `availability` is a stock status code or `discontinued`."""

    price: Decimal
    currency: str
    availability: str
    url: str
    price_includes_vat: bool = True
    price_valid_until: date | None = None
    seller_id: str = ""
    return_policy_id: str = ""
    shipping_ids: tuple[str, ...] = ()
    node_id: str = ""  # default: the URL plus #offer; a variant sets its own


@dataclass(frozen=True)
class VariantData:
    sku: str
    name: str
    url: str = ""  # only when the page can show this variant by URL; else left out
    offer: OfferData | None = None
    gtin: str = ""
    mpn: str = ""
    images: tuple[ImageData, ...] = ()


@dataclass(frozen=True)
class ProductData:
    url: str
    name: str
    sku: str
    product_id: str
    description: str = ""
    gtin: str = ""
    mpn: str = ""
    brand_id: str = ""
    manufacturer_id: str = ""
    images: tuple[ImageData, ...] = ()
    weight_kg: Decimal | None = None
    width_mm: int | None = None
    height_mm: int | None = None
    depth_mm: int | None = None
    material: str = ""
    color: str = ""
    country_of_origin: str = ""  # ISO 3166-1 alpha-2
    offer: OfferData | None = None
    variants: tuple[VariantData, ...] = ()
    varies_by: tuple[
        str, ...
    ] = ()  # what the variants differ in: schema.org property URLs or names


@dataclass(frozen=True)
class Crumb:
    name: str
    url: str


def _clean(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in ("", None, (), [])}


def _identifiers(gtin: str, mpn: str) -> dict[str, Any]:
    key = _GTIN_KEYS.get(len(gtin)) if gtin.isdigit() else None
    return _clean({key: gtin} if key else {}) | _clean({"mpn": mpn})


def image_node(image: ImageData) -> dict[str, Any]:
    return _clean(
        {
            "@type": "ImageObject",
            "contentUrl": image.url,
            "url": image.url,
            "width": image.width,
            "height": image.height,
            "caption": image.caption,
            "license": image.license,
            "acquireLicensePage": image.acquire_license_page,
            "creditText": image.credit_text,
            "creator": {"@type": "Person", "name": image.creator} if image.creator else "",
            "copyrightNotice": image.copyright_notice,
        }
    )


def offer_node(offer: OfferData) -> dict[str, Any]:
    price = f"{offer.price:.2f}"
    availability = _AVAILABILITY.get(offer.availability, "")
    until = offer.price_valid_until.isoformat() if offer.price_valid_until else ""
    return _clean(
        {
            "@type": "Offer",
            "@id": offer.node_id or f"{offer.url}#offer",
            "url": offer.url,
            "price": price,
            "priceCurrency": offer.currency,
            "priceSpecification": {
                "@type": "UnitPriceSpecification",
                "price": price,
                "priceCurrency": offer.currency,
                "valueAddedTaxIncluded": offer.price_includes_vat,
            },
            "availability": f"{SCHEMA}/{availability}" if availability else "",
            "priceValidUntil": until,
            "itemCondition": f"{SCHEMA}/NewCondition",
            "seller": {"@id": offer.seller_id} if offer.seller_id else "",
            "hasMerchantReturnPolicy": (
                {"@id": offer.return_policy_id} if offer.return_policy_id else ""
            ),
            "shippingDetails": [{"@id": ref} for ref in offer.shipping_ids],
        }
    )


def _variant_node(variant: VariantData, *, product_url: str) -> dict[str, Any]:
    return _clean(
        {
            "@type": "Product",
            "@id": f"{product_url}#variant-{quote(variant.sku, safe='')}",
            "url": variant.url,
            "name": variant.name,
            "sku": variant.sku,
            "image": [image_node(image) for image in variant.images],
            "offers": offer_node(variant.offer) if variant.offer else "",
        }
        | _identifiers(variant.gtin, variant.mpn)
    )


def _millimetres(value: int | None) -> dict[str, Any] | str:
    """A dimension as a `QuantitativeValue` in millimetres (UN/CEFACT `MMT`); nothing when
    unknown or not positive."""
    if not value or value <= 0:
        return ""
    return {"@type": "QuantitativeValue", "value": value, "unitCode": "MMT"}


def product_node(data: ProductData) -> dict[str, Any]:
    """The `Product`, or the `ProductGroup` of a product with variants (each variant a
    `Product` with its own offer, Google's variant markup)."""
    grouped = bool(data.variants)
    node: dict[str, Any] = {
        "@type": "ProductGroup" if grouped else "Product",
        "@id": f"{data.url}#product",
        "url": data.url,
        "name": data.name,
        "sku": data.sku,
        "productID": data.product_id,
        "description": data.description,
        "brand": {"@id": data.brand_id} if data.brand_id else "",
        "manufacturer": {"@id": data.manufacturer_id} if data.manufacturer_id else "",
        "image": [image_node(image) for image in data.images],
        "weight": (
            {"@type": "QuantitativeValue", "value": float(data.weight_kg), "unitCode": "KGM"}
            if data.weight_kg is not None
            else ""
        ),
        "width": _millimetres(data.width_mm),
        "height": _millimetres(data.height_mm),
        "depth": _millimetres(data.depth_mm),
        "material": data.material,
        "color": data.color,
        "countryOfOrigin": (
            {"@type": "Country", "name": data.country_of_origin} if data.country_of_origin else ""
        ),
    }
    node.update(_identifiers(data.gtin, data.mpn))
    if grouped:
        node["productGroupID"] = data.sku
        node["variesBy"] = list(data.varies_by)
        node["hasVariant"] = [_variant_node(v, product_url=data.url) for v in data.variants]
    elif data.offer is not None:
        node["offers"] = offer_node(data.offer)
    return _clean(node)


def breadcrumb_node(*, page_url: str, items: Sequence[Crumb]) -> dict[str, Any]:
    return {
        "@type": "BreadcrumbList",
        "@id": f"{page_url}#breadcrumb",
        "itemListElement": [
            {"@type": "ListItem", "position": position, "name": crumb.name, "item": crumb.url}
            for position, crumb in enumerate(items, start=1)
        ],
    }


def organization_node(*, node_id: str, name: str, url: str) -> dict[str, Any]:
    """The vendor's `Organization`, the offers' `seller` (never the operator)."""
    return {"@type": "Organization", "@id": node_id, "name": name, "url": url}


def return_policy_node(
    *,
    node_id: str,
    countries: Sequence[str],
    return_country: str,
    days: int,
    link: str = "",
    paid_by: str = "",
    methods: Sequence[str] = (),
) -> dict[str, Any]:
    """A consumer return policy: `days` is the vendor's approved window or the 14-day floor,
    and a lack of conformity (`itemDefectReturnFees`) is free. `paid_by` ("vendor" or
    "customer") sets `returnFees` only when the vendor's approved policy says it; empty states
    nothing. Nothing is promised to business buyers beyond the terms."""
    fees = {"vendor": FREE_RETURN, "customer": CUSTOMER_PAYS_RETURN}.get(paid_by, "")
    return _clean(
        {
            "@type": "MerchantReturnPolicy",
            "@id": node_id,
            "applicableCountry": list(countries),
            "returnPolicyCountry": return_country,
            "returnPolicyCategory": FINITE_WINDOW,
            "merchantReturnDays": days,
            "returnFees": fees,
            "itemDefectReturnFees": FREE_RETURN,
            "merchantReturnLink": link,
            "returnMethod": [
                f"{SCHEMA}/{_RETURN_METHODS[m]}" for m in methods if m in _RETURN_METHODS
            ],
        }
    )


def shipping_node(
    *,
    node_id: str,
    countries: Sequence[str],
    rate: Decimal,
    currency: str,
    min_days: int | None = None,
    max_days: int | None = None,
    name: str = "",
    weight_from_kg: Decimal | None = None,
    weight_to_kg: Decimal | None = None,
) -> dict[str, Any]:
    """One `OfferShippingDetails` per zone and rate: the countries in one `DefinedRegion`
    (none for a world zone), not one node per country. `name` is the shipping service's name
    (SEO.12). `weight_from_kg` set marks a node for another weight band than the product's own
    (SEO.12): the band goes in a `ShippingService` with its `ShippingConditions.weight`, the
    upper bound only when the band has one."""
    band = (
        _clean(
            {
                "@type": "ShippingService",
                "name": name,
                "shippingConditions": {
                    "@type": "ShippingConditions",
                    "weight": _clean(
                        {
                            "@type": "QuantitativeValue",
                            "minValue": f"{weight_from_kg:f}",
                            "maxValue": "" if weight_to_kg is None else f"{weight_to_kg:f}",
                            "unitCode": "KGM",
                        }
                    ),
                },
            }
        )
        if weight_from_kg is not None
        else ""
    )
    transit = _clean(
        {
            "@type": "QuantitativeValue",
            "minValue": min_days,
            "maxValue": max_days,
            "unitCode": "DAY",
        }
    )
    timed = min_days is not None or max_days is not None
    return _clean(
        {
            "@type": "OfferShippingDetails",
            "@id": node_id,
            "name": name,
            "hasShippingService": band,
            "shippingRate": {
                "@type": "MonetaryAmount",
                "value": f"{rate:.2f}",
                "currency": currency,
            },
            "shippingDestination": (
                {"@type": "DefinedRegion", "addressCountry": list(countries)} if countries else ""
            ),
            "deliveryTime": (
                {"@type": "ShippingDeliveryTime", "transitTime": transit} if timed else ""
            ),
        }
    )


def vendor_role_refs(role: str, *, brand_id: str, maker_id: str, vendor_id: str) -> tuple[str, str]:
    """`(brand_id, manufacturer_id)` for the product node (SEO.10). The CAT.20 Brand and
    Manufacturer come first in every role. Only a producer vendor fills a gap: it is the brand
    when CAT.20 has none (one GTIN, one brand `@id`) and the manufacturer when CAT.20 names
    none. A reseller or an unknown role never names the vendor as either (never invented)."""
    if role == "producer" and vendor_id:
        return brand_id or vendor_id, maker_id or vendor_id
    return brand_id, maker_id


def product_graph(
    *,
    product: ProductData,
    breadcrumb: Mapping[str, Any],
    seller: Mapping[str, Any] | None,
    return_policy: Mapping[str, Any] | None,
    shipping: Sequence[Mapping[str, Any]],
    linked: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """`linked` are the nodes the product refers to that other apps own (brand, manufacturer)."""
    nodes: list[Mapping[str, Any]] = [product_node(product), breadcrumb]
    nodes += [node for node in (seller, return_policy) if node is not None]
    nodes += [*shipping, *linked]
    return {"@context": SCHEMA, "@graph": [dict(node) for node in nodes]}


def graph_text(graph: Mapping[str, Any]) -> str:
    """The graph as text safe inside `<script type="application/ld+json">`."""
    return jsonld_text(graph)
