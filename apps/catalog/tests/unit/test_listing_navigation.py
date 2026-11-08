"""CAT.8 (part 3/3): applied-filter chips and pagination numbers for the listing templates (pure).

Every link a listing page shows builds on the page's current URL with the canonical query: a
chip's remove link drops one value and goes back to the first page; the pagination shows the
first and last page, a window around the current one, and a gap (None) for two pages or more.
"""

from decimal import Decimal

import pytest

from apps.catalog import listings
from apps.catalog.listings import FilterChip, ListingParams

BASE = "/it/compatibilita/marca/"


def test_without_drops_one_term_value_and_resets_the_page() -> None:
    params = ListingParams(terms=(("fit", ("a", "b")), ("size", ("m",))), page=3)
    assert listings.without(params, "fit", "a") == ListingParams(
        terms=(("fit", ("b",)), ("size", ("m",)))
    )
    # The last value of a taxonomy drops the key.
    assert listings.without(params, "size", "m") == ListingParams(terms=(("fit", ("a", "b")),))


def test_without_drops_single_value_filters() -> None:
    params = ListingParams(
        category="bags", vendor="acme", price_min=Decimal(10), price_max=Decimal(50), page=2
    )
    assert listings.without(params, listings.CATEGORY).category is None
    assert listings.without(params, listings.VENDOR).vendor is None
    assert listings.without(params, listings.PRICE_MIN).price_min is None
    assert listings.without(params, listings.PRICE_MAX).price_max is None
    assert listings.without(params, listings.PRICE_MAX).page == 1


def test_without_an_unknown_key_is_refused() -> None:
    with pytest.raises(ValueError, match="page"):
        listings.without(ListingParams(page=2), listings.PAGE)


def test_filter_chips_follow_the_canonical_order_and_link_to_the_page_url() -> None:
    params = ListingParams(
        category="bags",
        terms=(("fit", ("maker/model", "other")),),
        vendor="acme",
        price_min=Decimal("9.5"),
        price_max=Decimal(50),
        page=4,
    )
    labels = {("fit", "maker/model"): "Model", ("vendor", "acme"): "Acme shop"}
    chips = listings.filter_chips(params, BASE, labels)
    category, fit = "category=bags", "fit=maker/model,other"
    vendor, low, high = "vendor=acme", "price_min=9.50", "price_max=50"
    assert chips == [
        FilterChip("category", "bags", "bags", f"{BASE}?{fit}&{vendor}&{low}&{high}"),
        FilterChip(
            "fit", "maker/model", "Model", f"{BASE}?{category}&fit=other&{vendor}&{low}&{high}"
        ),
        FilterChip(
            "fit", "other", "other", f"{BASE}?{category}&fit=maker/model&{vendor}&{low}&{high}"
        ),
        FilterChip("vendor", "acme", "Acme shop", f"{BASE}?{category}&{fit}&{low}&{high}"),
        FilterChip("price_min", "9.50", "9.50", f"{BASE}?{category}&{fit}&{vendor}&{high}"),
        FilterChip("price_max", "50", "50", f"{BASE}?{category}&{fit}&{vendor}&{low}"),
    ]


def test_no_filters_no_chips() -> None:
    assert listings.filter_chips(ListingParams(page=3), BASE, {}) == []


def window(page: int, page_count: int) -> listings.PageWindow:
    return listings.page_window(total=page_count * 10, page=page, per_page=10)


@pytest.mark.parametrize(
    ("page", "page_count", "expected"),
    [
        (1, 1, [1]),
        (1, 5, [1, 2, 3, 4, 5]),
        (1, 10, [1, 2, 3, None, 10]),
        (5, 10, [1, 2, 3, 4, 5, 6, 7, None, 10]),  # a one-page gap shows the page
        (6, 12, [1, None, 4, 5, 6, 7, 8, None, 12]),
        (10, 10, [1, None, 8, 9, 10]),
        (4, 10, [1, 2, 3, 4, 5, 6, None, 10]),
    ],
)
def test_page_numbers(page: int, page_count: int, expected: list[int | None]) -> None:
    assert listings.page_numbers(window(page, page_count)) == expected


def test_page_links_build_on_the_page_url_with_the_filters() -> None:
    params = ListingParams(terms=(("fit", ("a",)),), page=2)
    links = listings.page_links(params, BASE, window(2, 3))
    assert links == [
        listings.PageLink(1, f"{BASE}?fit=a", current=False),
        listings.PageLink(2, f"{BASE}?fit=a&page=2", current=True),
        listings.PageLink(3, f"{BASE}?fit=a&page=3", current=False),
    ]


def test_page_links_mark_gaps() -> None:
    links = listings.page_links(ListingParams(), BASE, window(1, 10))
    assert [link.number for link in links] == [1, 2, 3, None, 10]
    assert links[3] == listings.PageLink(None, None, current=False)


def test_carried_filters_are_the_category_and_the_terms_the_form_does_not_offer() -> None:
    params = ListingParams(category="bags/small", terms=(("colour", ("blue", "red")),))
    assert listings.carried_filters(params, {("colour", "red")}) == [
        ("category", "bags/small"),
        ("colour", "blue"),
    ]
    assert listings.carried_filters(ListingParams(vendor="acme", page=2), set()) == []
