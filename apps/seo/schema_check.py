"""A schema check for structured data graphs (SEO.1; SEO.7 extends it to the other page types).

`rich_result_errors` lists what Google's rich results require of the nodes it knows (Product,
BreadcrumbList, Article, ProfilePage and Event); `dangling_references` lists every `@id`
reference that resolves to no node of the same graph.
"""

from collections.abc import Collection, Iterator, Mapping
from typing import Any

from . import engine


def _nodes(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [node for node in document.get("@graph", []) if isinstance(node, Mapping)]


def _walk(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def dangling_references(document: Mapping[str, Any]) -> list[str]:
    """The `@id`s used as references (an object holding only `@id`) that no node defines."""
    nodes = _nodes(document)
    defined = {node["@id"] for node in nodes if "@id" in node}
    missing: list[str] = []
    for node in nodes:
        for child in _walk({key: value for key, value in node.items() if key != "@id"}):
            if set(child) == {"@id"} and child["@id"] not in defined:
                missing.append(str(child["@id"]))
    return list(dict.fromkeys(missing))


def _offer_errors(label: str, offer: Mapping[str, Any]) -> list[str]:
    errors = [
        f"{label}: offers.{key} is missing"
        for key in ("price", "priceCurrency", "availability")
        if not offer.get(key)
    ]
    spec = offer.get("priceSpecification")
    if not isinstance(spec, Mapping) or spec.get("price") != offer.get("price"):
        errors.append(f"{label}: offers.priceSpecification is missing or has another price")
    elif spec.get("priceCurrency") != offer.get("priceCurrency"):
        errors.append(f"{label}: offers.priceSpecification has another currency")
    # No `seller` is not an error: SEO.15 omits it when the vendor has no public name.
    return errors


def _product_errors(node: Mapping[str, Any]) -> list[str]:
    label = str(node.get("sku") or node.get("name") or "product")
    errors = [f"{label}: {key} is missing" for key in ("name",) if not node.get(key)]
    offers = node.get("offers")
    if isinstance(offers, Mapping):
        errors += _offer_errors(label, offers)
    for variant in node.get("hasVariant", []):
        errors += _product_errors(variant)
        if not variant.get("@id"):
            errors.append(f"{variant.get('sku') or 'variant'}: @id is missing")
    if node.get("hasVariant") and not node.get("variesBy"):
        errors.append(f"{label}: variesBy is missing")
    return errors


def rich_result_warnings(document: Mapping[str, Any]) -> list[str]:
    """Recommended properties that are missing (Google: `image` for a product; the gallery
    arrives with CAT.4)."""
    return [
        f"{node.get('sku') or node.get('name')}: image is missing"
        for node in _nodes(document)
        if node.get("@type") in {"Product", "ProductGroup"} and not node.get("image")
    ]


def _person_errors(document: Mapping[str, Any], person_names: Collection[str]) -> list[str]:
    """SEO.15: an `Organization` (the seller) named like a natural person's legal name."""
    return [
        f"Organization {node.get('@id')}: the name is a natural person's legal name"
        for node in _nodes(document)
        if node.get("@type") == "Organization"
        and engine.normalize_name(str(node.get("name") or "")) in person_names
    ]


def rich_result_errors(
    document: Mapping[str, Any], *, person_names: Collection[str] = ()
) -> list[str]:
    """`person_names` are the normalised names of the natural persons the document is about
    (`engine.natural_person_names`): no `Organization` may carry one (SEO.15)."""
    errors: list[str] = _person_errors(document, person_names) if person_names else []
    for node in _nodes(document):
        kind = node.get("@type")
        if kind in {"Product", "ProductGroup"}:
            errors += _product_errors(node)
        elif kind == "OfferShippingDetails":
            if not node.get("shippingRate"):
                errors.append("OfferShippingDetails: shippingRate is missing")
        elif kind in {"BlogPosting", "Article"}:
            errors += [
                f"{kind}: {key} is missing"
                for key in ("headline", "datePublished")
                if not node.get(key)
            ]
        elif kind == "ProfilePage":
            if not node.get("mainEntity"):
                errors.append("ProfilePage: mainEntity is missing")
        elif kind == "Event":
            errors += [
                f"Event: {key} is missing"
                for key in ("name", "startDate", "location")
                if not node.get(key)
            ]
        elif kind == "BreadcrumbList":
            items = node.get("itemListElement") or []
            if not items:
                errors.append("BreadcrumbList: itemListElement is missing")
            errors += [
                f"BreadcrumbList: item {i} has no position, name or item URL"
                for i, item in enumerate(items, start=1)
                if not (item.get("position") and item.get("name") and item.get("item"))
            ]
    return errors
