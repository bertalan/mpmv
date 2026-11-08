"""CMS.10: the bounds of the product grid (spec 10 §10.2), pure rules."""

import pytest

from apps.cms.engine import PRODUCT_GRID_DEFAULT, PRODUCT_GRID_MAX, grid_limit


def test_the_bounds() -> None:
    assert PRODUCT_GRID_MAX == 24
    assert 1 <= PRODUCT_GRID_DEFAULT <= PRODUCT_GRID_MAX


@pytest.mark.parametrize(
    ("given", "shown"),
    [(None, PRODUCT_GRID_DEFAULT), (0, 1), (-3, 1), (1, 1), (8, 8), (24, 24), (25, 24), (500, 24)],
)
def test_a_stored_limit_out_of_bounds_is_clamped(given: int | None, shown: int) -> None:
    """The block validates the limit; a value stored before a bound changed is clamped."""
    assert grid_limit(given) == shown
