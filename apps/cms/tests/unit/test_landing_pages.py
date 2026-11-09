"""CMS.4: the pure rules of the term landing page (spec 10 §10.2, D-07)."""

import pytest

from apps.cms.engine import (
    LANDING_CATEGORY_DEFAULT,
    LANDING_CATEGORY_MAX,
    landing_category_limit,
    listing_slug_path,
    shows_category_sections,
)


@pytest.mark.parametrize(
    ("path", "prefix", "expected"),
    [
        ("/product-category/bags/", "", "bags"),
        ("/product-category/bags/leather/", "", "bags/leather"),
        ("/it/categoria-prodotto/borse/", "/it", "borse"),
        ("/it/categoria-prodotto/borse/pelle/", "/it", "borse/pelle"),
    ],
)
def test_the_filter_value_of_a_category_is_its_slug_path(
    path: str, prefix: str, expected: str
) -> None:
    """A listing's `category` filter takes the slug path from the top of the tree (CAT.8)."""
    assert listing_slug_path(path, prefix) == expected


def test_a_path_without_slugs_has_no_filter_value() -> None:
    assert listing_slug_path("/product-category/", "") is None


@pytest.mark.parametrize(
    ("limit", "expected"),
    [(None, LANDING_CATEGORY_DEFAULT), (0, 0), (3, 3), (-1, 0), (99, LANDING_CATEGORY_MAX)],
)
def test_the_category_limit_is_clamped(limit: int | None, expected: int) -> None:
    assert landing_category_limit(limit) == expected


@pytest.mark.parametrize(
    ("categories", "limit", "expected"),
    [(0, 4, False), (1, 4, False), (2, 4, True), (5, 4, True), (3, 0, False)],
)
def test_sections_show_for_two_categories_or_more(
    categories: int, limit: int, expected: bool
) -> None:
    """One category would repeat the listing below it; a zero limit turns them off."""
    assert shows_category_sections(categories, limit) is expected
