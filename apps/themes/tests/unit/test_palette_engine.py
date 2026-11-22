"""THM.33: `derive_palette`, `nearest_compliant` and `suggest_foreground` (spec 14 §14.12)."""

import pytest

from apps.themes.engine import (
    DARK_SURFACE,
    ThemeError,
    contrast_ratio,
    derive_palette,
    nearest_compliant,
    suggest_foreground,
)
from apps.themes.palettes import PRESETS, ROLES, check_brand_palette


def channels(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def test_nearest_compliant_fixes_the_legacy_orange_close_to_the_blue_palette_text() -> None:
    fixed = nearest_compliant("#C65D00", "#FFFFFF", 4.5)
    assert fixed is not None
    assert contrast_ratio(fixed, "#FFFFFF") >= 4.5
    assert all(abs(a - b) <= 8 for a, b in zip(channels(fixed), channels("#BE5900"), strict=True))


def test_nearest_compliant_lightens_a_colour_on_the_dark_surface() -> None:
    fixed = nearest_compliant("#046BD2", DARK_SURFACE, 4.5)
    assert fixed is not None
    assert contrast_ratio(fixed, DARK_SURFACE) >= 4.5
    assert sum(channels(fixed)) > sum(channels("#046BD2"))


def test_a_colour_that_already_passes_is_returned_unchanged() -> None:
    assert nearest_compliant("#BE5900", "#FFFFFF", 4.5) == "#BE5900"
    assert nearest_compliant("#1A1A1A", "#FFFFFF", 4.5) == "#1A1A1A"


def test_nearest_compliant_is_none_when_no_lightness_passes() -> None:
    assert nearest_compliant("#FFFFFF", "#FBA926", 4.5) is None


def test_nearest_compliant_keeps_the_hue() -> None:
    fixed = nearest_compliant("#0B5C7A", "#30373F", 4.5)
    assert fixed is not None
    red, _green, blue = channels(fixed)
    assert blue > red  # still a blue-green, not grey
    assert contrast_ratio(fixed, "#30373F") >= 4.5


def test_suggest_foreground_picks_the_higher_ratio() -> None:
    assert suggest_foreground("#FBA926", 4.5) == "#1A1A1A"
    assert suggest_foreground("#C40417", 4.5) == "#FFFFFF"


def test_suggest_foreground_is_none_when_neither_passes() -> None:
    assert suggest_foreground("#777777", 4.5) is None


@pytest.mark.parametrize("slug", sorted(PRESETS))
def test_derive_palette_on_each_predefined_palettes_base_colours_passes(slug: str) -> None:
    preset = PRESETS[slug]
    base = {key: preset[key] for key in ("primary", "secondary", "band")}
    palette = derive_palette(base)
    assert tuple(palette) == ROLES
    assert check_brand_palette(palette) == []
    surfaces = ["#F8F9FA", "#FFFFFF"]
    assert check_brand_palette(derive_palette(base, surfaces=surfaces), surfaces) == []
    assert {key: palette[key] for key in base} == base


def test_an_empty_optional_base_colour_takes_the_starting_palette_value() -> None:
    palette = derive_palette({"primary": "#046BD2"}, start=PRESETS["blue"])
    assert palette["secondary"] == PRESETS["blue"]["secondary"]
    assert palette["band"] == PRESETS["blue"]["band"]
    assert check_brand_palette(palette) == []


def test_a_missing_optional_colour_without_a_start_is_an_error() -> None:
    with pytest.raises(ThemeError):
        derive_palette({"primary": "#046BD2"})


def test_an_invalid_base_colour_is_an_error() -> None:
    with pytest.raises(ThemeError):
        derive_palette({"primary": "blue", "secondary": "#FBA926", "band": "#30373F"})


def test_a_dark_foreground_is_derived_for_a_light_accent() -> None:
    palette = derive_palette({"primary": "#046BD2", "secondary": "#FBA926", "band": "#30373F"})
    assert palette["on_secondary"] == "#1A1A1A"
    assert contrast_ratio(palette["on_secondary"], palette["secondary_hover"]) >= 4.5


def test_the_derived_palette_is_deterministic() -> None:
    base = {"primary": "#7A1FA2", "secondary": "#F2C94C", "band": "#222831"}
    assert derive_palette(base) == derive_palette(base)
