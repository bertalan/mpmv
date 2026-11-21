"""THM.31: the palette stylesheet is the diff between a theme and a palette (spec 14 §14.12)."""

from pathlib import Path

import pytest

from apps.themes.build import read_tokens
from apps.themes.engine import resolve_tokens
from apps.themes.palettes import PRESETS, ROLES, css_digest, render_palette_css

THEMES = Path(__file__).resolve().parents[4] / "themes"
CHAIN = ("ride", "base")
TOKENS = {slug: read_tokens(THEMES / slug) for slug in CHAIN}


def roles(slug: str) -> dict[str, str]:
    return {role: PRESETS[slug][role] for role in ROLES}


def test_the_ride_default_is_red_so_the_red_palette_changes_nothing() -> None:
    assert render_palette_css(CHAIN, TOKENS, roles("red")) == ""
    assert resolve_tokens(CHAIN, TOKENS)["color.accent"] == "#C40417"


def test_blue_overrides_the_palette_tokens_and_the_tokens_derived_from_them() -> None:
    css = render_palette_css(CHAIN, TOKENS, roles("blue"))
    assert css.startswith(":root{")
    assert "@layer" not in css  # unlayered: it beats the theme
    assert "--color-accent:#046BD2" in css
    assert "--color-link:#046BD2" in css
    assert "--button-bg:#046BD2" in css  # `button.bg = "{color.accent}"` follows the palette
    assert "--color-secondary:#FBA926" in css


def test_the_bootstrap_variables_follow_when_the_theme_has_a_foundation() -> None:
    css = render_palette_css(CHAIN, TOKENS, roles("blue"), bootstrap=True)
    assert "--bs-primary:#046BD2" in css
    assert "--bs-primary-rgb:4,107,210" in css
    assert "--bs-primary" not in render_palette_css(CHAIN, TOKENS, roles("blue"))


def test_only_the_changed_properties_are_written() -> None:
    css = render_palette_css(CHAIN, TOKENS, roles("petrol"))
    assert "--color-surface:" not in css
    assert "--font-body" not in css


def test_the_digest_follows_the_content() -> None:
    blue = render_palette_css(CHAIN, TOKENS, roles("blue"))
    petrol = render_palette_css(CHAIN, TOKENS, roles("petrol"))
    assert len(css_digest(blue)) == 12
    assert css_digest(blue) == css_digest(blue) != css_digest(petrol)


@pytest.mark.parametrize("slug", sorted(PRESETS))
def test_the_css_is_safe_to_serve(slug: str) -> None:
    css = render_palette_css(CHAIN, TOKENS, roles(slug), bootstrap=True)
    assert not any(bad in css for bad in ("<", "url(", "@import"))
