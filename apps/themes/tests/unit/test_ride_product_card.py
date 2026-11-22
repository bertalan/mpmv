"""THM.25: contrast of the `ride` product card and the banner over an image (spec 14 §14.4).

The price, the vendor line and the note sit on the card's own light surface; the banner title and
lead sit on an image under a scrim, measured over the worst image (white). Every palette passes
4.5:1. The CSS stays in the overrides layer, with colours only through tokens.
"""

import re
from pathlib import Path

import pytest

from apps.themes.build import read_tokens
from apps.themes.engine import contrast_ratio
from apps.themes.palettes import PRESETS, ROLES, resolve_with_palette

THEMES = Path(__file__).resolve().parents[4] / "themes"
CHAIN = ("ride", "base")
TOKENS = {slug: read_tokens(THEMES / slug) for slug in CHAIN}
CSS = THEMES / "ride/static/themes/ride/css/45-catalog.css"
BANNER_SCRIM = 0.7  # `color-mix(... 70%, transparent)` in 60-banner-chrome.css


def resolved(palette: str) -> dict[str, str]:
    return resolve_with_palette(CHAIN, TOKENS, {role: PRESETS[palette][role] for role in ROLES})


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def over_white(overlay: str, alpha: float) -> str:
    return "#" + "".join(f"{round(c * alpha + 255 * (1 - alpha)):02X}" for c in _rgb(overlay))


@pytest.mark.parametrize("palette", sorted(PRESETS))
def test_card_text_passes_on_the_light_surface(palette: str) -> None:
    tokens = resolved(palette)
    background = tokens["surface.light.bg"]
    for name in ("surface.light.text", "surface.light.heading", "surface.light.link"):
        assert contrast_ratio(tokens[name], background) >= 4.5, name
    assert contrast_ratio(tokens["color.text-muted"], background) >= 4.5


@pytest.mark.parametrize("palette", sorted(PRESETS))
def test_banner_text_passes_over_the_lightest_image_under_the_scrim(palette: str) -> None:
    tokens = resolved(palette)
    field = over_white(tokens["color.overlay"], BANNER_SCRIM)
    assert contrast_ratio(tokens["color.text-inverse"], field) >= 4.5


def test_the_card_css_uses_tokens_only_and_lifts_only_without_reduced_motion() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert "@layer overrides" in css
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert "prefers-reduced-motion: no-preference" in css
    assert ".product-card:hover" in css
    assert "product-card__media" in css
    assert "var(--s-focus)" in css
