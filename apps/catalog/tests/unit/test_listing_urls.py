"""CAT.8 (part 2/3): listing page paths, query canonicalisation and robots rules (pure)."""

from decimal import Decimal

import pytest

from apps.catalog import listings, slugs
from apps.catalog.listings import ListingParams


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("en", "/product-category/bags/backpacks/"),
        ("it", "/it/categoria-prodotto/bags/backpacks/"),
        ("de", "/de/product-category/bags/backpacks/"),
    ],
)
def test_category_path(lang: str, expected: str) -> None:
    assert slugs.category_path(lang, ("bags", "backpacks"), default_language="en") == expected


def test_term_brand_and_vendor_paths() -> None:
    assert slugs.term_path("en", "fit", ("maker",), default_language="en") == "/fit/maker/"
    assert (
        slugs.term_path("it", "adatto", ("marca", "modello"), default_language="en")
        == "/it/adatto/marca/modello/"
    )
    assert slugs.brand_path("it", "acme", default_language="en") == "/it/marchio/acme/"
    assert slugs.brand_path("fr", "acme", default_language="en") == "/fr/brand/acme/"
    assert slugs.vendor_path("it", "shop-1", default_language="en") == "/it/shop/shop-1/"


def test_reserved_segments_cover_every_fixed_catalog_segment() -> None:
    assert {"product", "prodotto", "product-category", "categoria-prodotto"} <= (
        slugs.RESERVED_SEGMENTS
    )
    assert {"brand", "marchio", "shop"} <= slugs.RESERVED_SEGMENTS


def test_split_query_keeps_the_given_order_and_separates_tracking() -> None:
    given, other = listings.split_query(
        "utm_source=x&price_max=20&fit=a/b&price_min=5", term_keys={"fit"}
    )
    assert given == "price_max=20&fit=a/b&price_min=5"
    assert other == "utm_source=x"


def test_a_canonical_request_splits_to_its_canonical_query() -> None:
    params = ListingParams(terms=(("fit", ("a/b",)),), price_min=Decimal(5), page=2)
    query = listings.canonical_query(params)
    assert listings.split_query(query, term_keys={"fit"}) == (query, "")


def test_join_query() -> None:
    assert listings.join_query("/a/", "", "") == "/a/"
    assert listings.join_query("/a/", "page=2", "utm_source=x") == "/a/?page=2&utm_source=x"
    assert listings.join_query("/a/", "", "utm_source=x") == "/a/?utm_source=x"


@pytest.mark.parametrize(
    ("params", "total", "expected"),
    [
        (ListingParams(), 0, listings.ROBOTS_INDEX),  # an empty term page stays indexable
        (ListingParams(vendor="v"), 3, listings.ROBOTS_INDEX),
        (ListingParams(vendor="v"), 0, listings.ROBOTS_NOINDEX),
        (ListingParams(page=listings.INDEXED_PAGES), 500, listings.ROBOTS_INDEX),
        (ListingParams(page=listings.INDEXED_PAGES + 1), 500, listings.ROBOTS_NOINDEX),
    ],
)
def test_listing_robots(params: ListingParams, total: int, expected: str) -> None:
    assert listings.listing_robots(params, total=total) == expected
