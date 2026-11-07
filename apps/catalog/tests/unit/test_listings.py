"""CAT.8 (part 1/3): the pure listing engine (spec 03 §3.2, URLs; owner decision D-07).

Filter parameters are parsed and normalised without the database: category, taxonomy terms
(keyed by the taxonomy's slug in the page language), vendor, a price range as Decimal, and the
page. The canonical query string has a fixed parameter order, so every filtered or paginated
listing has exactly one indexable URL built on the page's current URL.
"""

from decimal import Decimal

import pytest

from apps.catalog import listings
from apps.catalog.listings import ListingParams

TERM_KEYS = frozenset({"colour", "occasion"})


def parse(query: dict[str, list[str]]) -> listings.ParsedParams:
    return listings.parse_listing_params(query, term_keys=TERM_KEYS)


# --- Parsing


def test_an_empty_query_has_no_filters_and_page_one() -> None:
    parsed = parse({})
    assert parsed.params == ListingParams()
    assert parsed.params.page == 1
    assert not parsed.params.has_filters
    assert parsed.errors == ()
    assert parsed.ignored == ()


def test_all_filters_are_parsed() -> None:
    parsed = parse(
        {
            "category": ["bags"],
            "colour": ["red"],
            "vendor": ["vendor-a"],
            "price_min": ["10"],
            "price_max": ["49.90"],
            "page": ["3"],
        }
    )
    assert parsed.errors == ()
    assert parsed.params == ListingParams(
        category="bags",
        terms=(("colour", ("red",)),),
        vendor="vendor-a",
        price_min=Decimal("10.00"),
        price_max=Decimal("49.90"),
        page=3,
    )
    assert parsed.params.has_filters
    assert parsed.params.has_price_filter


def test_prices_are_decimals_with_two_places() -> None:
    params = parse({"price_min": ["5"], "price_max": ["7.5"]}).params
    assert params.price_min == Decimal("5.00")
    assert params.price_max == Decimal("7.50")
    assert isinstance(params.price_min, Decimal)


@pytest.mark.parametrize("raw", ["abc", "-1", "1.234", "NaN", "Infinity", "1e400x", ""])
def test_an_invalid_price_is_dropped_with_an_error(raw: str) -> None:
    parsed = parse({"price_min": [raw]})
    assert parsed.params.price_min is None
    assert parsed.errors
    assert parsed.errors[0].startswith("price_min")


def test_a_reversed_price_range_is_swapped() -> None:
    params = parse({"price_min": ["50"], "price_max": ["10"]}).params
    assert (params.price_min, params.price_max) == (Decimal("10.00"), Decimal("50.00"))


def test_term_values_are_collected_from_repeats_and_commas_sorted_and_deduplicated() -> None:
    parsed = parse({"colour": ["red,Blue", " red ", "green"], "occasion": ["party"]})
    assert parsed.params.terms == (
        ("colour", ("blue", "green", "red")),
        ("occasion", ("party",)),
    )
    assert parsed.params.term_slugs("colour") == ("blue", "green", "red")
    assert parsed.params.term_slugs("season") == ()


def test_an_invalid_slug_is_dropped_with_an_error() -> None:
    parsed = parse({"colour": ["red", "../etc"], "category": ["Bags & more"]})
    assert parsed.params.terms == (("colour", ("red",)),)
    assert parsed.params.category is None
    assert len(parsed.errors) == 2


def test_a_single_value_parameter_keeps_its_first_value_and_reports_the_rest() -> None:
    parsed = parse({"category": ["bags", "hats"], "vendor": ["vendor-a", "vendor-b"]})
    assert parsed.params.category == "bags"
    assert parsed.params.vendor == "vendor-a"
    assert len(parsed.errors) == 2


@pytest.mark.parametrize("raw", ["0", "-2", "x", "1.5", ""])
def test_an_invalid_page_falls_back_to_page_one_with_an_error(raw: str) -> None:
    parsed = parse({"page": [raw]})
    assert parsed.params.page == 1
    assert parsed.errors


def test_unknown_keys_are_ignored_and_listed() -> None:
    parsed = parse({"utm_source": ["x"], "season": ["summer"], "colour": ["red"]})
    assert parsed.params.terms == (("colour", ("red",)),)
    assert parsed.ignored == ("season", "utm_source")
    assert parsed.errors == ()


def test_a_taxonomy_cannot_take_a_reserved_key() -> None:
    with pytest.raises(ValueError, match="page"):
        listings.parse_listing_params({}, term_keys={"page"})


# --- Canonical URLs


def test_the_canonical_query_has_a_fixed_parameter_order() -> None:
    a = parse(
        {
            "page": ["2"],
            "price_max": ["30"],
            "vendor": ["vendor-a"],
            "occasion": ["party"],
            "colour": ["red,blue"],
            "category": ["bags"],
            "price_min": ["9.5"],
        }
    ).params
    b = parse(
        {
            "category": ["bags"],
            "colour": ["blue", "red"],
            "occasion": ["party"],
            "price_min": ["9.50"],
            "price_max": ["30.00"],
            "vendor": ["vendor-a"],
            "page": ["2"],
        }
    ).params
    expected = (
        "category=bags&colour=blue,red&occasion=party&vendor=vendor-a"
        "&price_min=9.50&price_max=30&page=2"
    )
    assert listings.canonical_query(a) == expected
    assert listings.canonical_query(b) == expected


def test_page_one_and_empty_filters_give_no_query() -> None:
    assert listings.canonical_query(ListingParams()) == ""
    assert listings.canonical_query(ListingParams(page=1, category="bags")) == "category=bags"


def test_the_listing_url_builds_on_the_current_url_of_the_page() -> None:
    params = ListingParams(terms=(("colour", ("red",)),), page=2)
    assert listings.listing_url("/it/occasione/festa/", params) == (
        "/it/occasione/festa/?colour=red&page=2"
    )
    assert listings.listing_url("/brand/brand-1/", ListingParams()) == "/brand/brand-1/"


def test_with_page_keeps_the_filters() -> None:
    params = ListingParams(category="bags", page=3)
    assert params.with_page(1) == ListingParams(category="bags")
    assert params.with_page(4).page == 4
    with pytest.raises(ValueError, match="page"):
        params.with_page(0)


def test_parsing_the_canonical_query_gives_the_same_params() -> None:
    params = parse(
        {"category": ["bags"], "colour": ["red,blue"], "price_min": ["1.10"], "page": ["5"]}
    ).params
    reparsed = listings.parse_listing_params(
        listings.query_to_lists(listings.canonical_query(params)), term_keys=TERM_KEYS
    )
    assert reparsed.params == params


# --- Pagination


def test_page_window_maths() -> None:
    window = listings.page_window(total=45, page=2, per_page=20)
    assert window.page_count == 3
    assert (window.offset, window.limit) == (20, 20)
    assert window.has_previous
    assert window.has_next
    assert (window.previous_page, window.next_page) == (1, 3)
    assert not window.out_of_range


def test_the_last_page_and_the_first_page() -> None:
    last = listings.page_window(total=45, page=3, per_page=20)
    assert (last.has_next, last.next_page) == (False, None)
    first = listings.page_window(total=45, page=1, per_page=20)
    assert (first.has_previous, first.previous_page) == (False, None)


def test_an_empty_listing_has_one_page() -> None:
    window = listings.page_window(total=0, page=1, per_page=20)
    assert window.page_count == 1
    assert not window.out_of_range
    assert not window.has_next


def test_a_page_past_the_end_is_out_of_range() -> None:
    window = listings.page_window(total=40, page=3, per_page=20)
    assert window.page_count == 2
    assert window.out_of_range


@pytest.mark.parametrize(("total", "page", "per_page"), [(-1, 1, 20), (10, 0, 20), (10, 1, 0)])
def test_page_window_refuses_invalid_input(total: int, page: int, per_page: int) -> None:
    with pytest.raises(ValueError, match="must be"):
        listings.page_window(total=total, page=page, per_page=per_page)
