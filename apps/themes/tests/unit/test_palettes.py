"""THM.31: the predefined palettes and `check_brand_palette` (spec 14 §14.12)."""

import pytest

from apps.themes.engine import contrast_ratio
from apps.themes.palettes import (
    DARK_SURFACE,
    DEFAULT_PALETTE,
    PRESETS,
    ROLE_TOKENS,
    ROLES,
    check_brand_palette,
    palette_tokens,
)

RED = PRESETS["red"]


def roles(slug: str) -> dict[str, str]:
    return {role: PRESETS[slug][role] for role in ROLES}


@pytest.mark.parametrize("slug", sorted(PRESETS))
def test_predefined_palette_passes_the_contrast_check(slug: str) -> None:
    assert check_brand_palette(roles(slug)) == []


def test_red_is_the_default() -> None:
    assert DEFAULT_PALETTE == "red"
    assert RED["primary"] == "#C40417"


@pytest.mark.parametrize(
    ("slug", "role", "background", "minimum"),
    [
        ("red", "on_primary", "primary", 6.22),
        ("red", "primary_dark", DARK_SURFACE, 6.65),
        ("blue", "on_primary", "primary", 5.20),
        ("blue", "on_secondary", "secondary", 8.95),
        ("blue", "secondary_text", "#FFFFFF", 4.55),
        ("blue", "primary_dark", DARK_SURFACE, 4.81),
        ("petrol", "on_primary", "primary", 7.43),
        ("petrol", "primary_dark", DARK_SURFACE, 9.87),
    ],
)
def test_ratios_match_the_spec_table(slug: str, role: str, background: str, minimum: float) -> None:
    bg = PRESETS[slug].get(background, background)
    assert contrast_ratio(PRESETS[slug][role], bg) >= minimum - 0.005


def test_a_failing_text_pair_is_named_with_its_ratio() -> None:
    palette = roles("red") | {"primary": "#FF6B6B"}  # light red: white on it is low
    problems = check_brand_palette(palette)
    names = [v.pair for v in problems]
    assert "on_primary on primary" in names
    failing = next(v for v in problems if v.pair == "on_primary on primary")
    assert failing.kind == "contrast"
    assert "#FFFFFF on #FF6B6B" in failing.message
    assert "below the 4.5:1 minimum" in failing.message


def test_a_3_to_1_ui_pair_fails_when_text_would_pass() -> None:
    palette = roles("red") | {"control_border": "#C8C8C8"}  # 1.7:1 on white
    problems = check_brand_palette(palette)
    assert [v.pair for v in problems] == ["control_border on surface #FFFFFF"]
    assert "below the 3:1 minimum" in problems[0].message


def test_the_ratio_is_floored_not_rounded_up() -> None:
    palette = roles("red") | {"on_primary": "#767676"}  # 4.54:1 on white, far below on red
    assert all("4.50:1" not in v.message for v in check_brand_palette(palette))


def test_a_newly_registered_theme_surface_is_checked() -> None:
    """Primary text on a darker surface fails: the palette is invalid for that theme."""
    problems = check_brand_palette(roles("red"), ["#6E6E6E"])
    assert any(v.pair.endswith("on surface #6E6E6E") for v in problems)


def test_a_missing_role_and_a_bad_colour_are_reported_before_any_contrast() -> None:
    palette = roles("red")
    del palette["focus"]
    palette["primary"] = "red"
    kinds = {(v.kind, v.pair) for v in check_brand_palette(palette)}
    assert kinds == {("missing", "focus"), ("invalid", "primary")}


def test_palette_tokens_cover_every_role_and_set_the_link() -> None:
    tokens = palette_tokens(roles("blue"))
    assert set(ROLE_TOKENS.values()) <= set(tokens)
    assert tokens["color.link"] == tokens["color.accent"] == "#046BD2"
