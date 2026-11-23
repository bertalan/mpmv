"""THM.16 follow-up: where `ride` builds a `base` component on the Bootstrap grid, the grid wins.

`base` (and the ride home blocks) lay `.product-grid` and `.cart-line` out as CSS grids in layers
above `bootstrap`. On STAGE that squeezed the listing cards to a ninth of the row, put the whole
cart line into a 6rem column, and let the quantity stepper run under the remove button. These
checks read the built `ride` stylesheet, where the layer order decides.
"""

import re
from pathlib import Path

import pytest

from apps.themes.build import render_themes

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def ride_css() -> str:
    return render_themes(ROOT / "themes")["themes/ride/theme.css"]


def rule(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
    assert match, f"no rule for {selector}"
    return match.group(1)


def test_a_bootstrap_product_grid_is_a_flex_row(ride_css: str) -> None:
    body = rule(ride_css, ".product-grid.row")
    assert "display:flex" in body
    assert "grid-template-columns:none" in body
    assert "display:block" in rule(ride_css, ".product-grid.row>li")


def ride_selectors(css: str) -> list[str]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [
        selector.strip()
        for prelude in re.findall(r"([^{}]+)\{", css)
        if not prelude.lstrip().startswith("@")
        for selector in prelude.split(",")
    ]


def test_no_ride_tile_grid_rule_reaches_a_bootstrap_product_row() -> None:
    """THM.19: the home tile grid (`display: grid`, `container-type` on each item) skips a row.

    A rule on a bare `.product-grid` also matches the listing's `ul.product-grid.row`: on the
    gap-analysis build it squeezed every listing card to about 95 px. Every ride selector on
    `.product-grid` names the Bootstrap row, either to style it or to leave it out.
    """
    sources = sorted((ROOT / "themes/ride/static/themes/ride/css").glob("*.css"))
    selectors = [s for path in sources for s in ride_selectors(path.read_text(encoding="utf-8"))]
    on_grid = [s for s in selectors if re.search(r"\.product-grid(?![\w-])", s)]
    assert on_grid
    leaking = [s for s in on_grid if ".product-grid.row" not in s and ":not(.row)" not in s]
    assert leaking == []


def test_a_bootstrap_cart_line_is_not_a_narrow_grid(ride_css: str) -> None:
    body = rule(ride_css, ".cart-line.list-group-item")
    assert "display:block" in body
    assert "max-inline-size:none" in body


def test_the_quantity_stepper_fits_two_44px_buttons_and_the_field(ride_css: str) -> None:
    widths = re.findall(r"\.quantity\{max-inline-size:(\d+)rem", ride_css)
    assert widths
    assert int(widths[-1]) >= 14


@pytest.mark.parametrize("name", ["listing.html", "search.html"])
def test_listings_show_four_cards_on_wide_screens(name: str) -> None:
    html = (ROOT / "themes/ride/templates/catalog" / name).read_text()
    assert "row-cols-sm-2 row-cols-lg-3 row-cols-xxl-4" in html
