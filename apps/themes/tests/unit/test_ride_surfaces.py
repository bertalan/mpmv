"""THM.20: `ride` surfaces and decoration (spec 14 §14.4, §14.8, §14.12).

Every surface pair (heading, text, link, button, hover and focus ring) passes the token
contract under each predefined palette; the textures are own-work SVGs under 4 KB listed in
`LICENSES.toml`; the CSS sits in the utilities layer and carries no inline style.
"""

import re
import tomllib
from pathlib import Path

import pytest
from django.template.loader import render_to_string

from apps.themes.build import read_tokens
from apps.themes.engine import contrast_ratio
from apps.themes.palettes import PRESETS, ROLES, resolve_with_palette
from apps.themes.services import use_theme

THEMES = Path(__file__).resolve().parents[4] / "themes"
RIDE = THEMES / "ride"
CHAIN = ("ride", "base")
TOKENS = {slug: read_tokens(THEMES / slug) for slug in CHAIN}
SURFACES = ("light", "muted", "band", "primary")
TEXTURES = ("edge-torn.svg", "texture-topo.svg")
TEXT, UI = 4.5, 3.0
VARS = ("bg", "text", "heading", "link", "focus", "button-bg", "button-fg", "button-hover")
DARK = ("band", "primary")
FOCUS_ON_DARK = 4.5  # margin over the 3:1 floor on the dark and primary bands


def resolved(palette: str) -> dict[str, str]:
    return resolve_with_palette(CHAIN, TOKENS, {role: PRESETS[palette][role] for role in ROLES})


def pairs(surface: str) -> list[tuple[str, str, str, float]]:
    """(name, foreground token, background token, minimum) of one surface."""
    p = f"surface.{surface}"
    return [
        ("text", f"{p}.text", f"{p}.bg", TEXT),
        ("heading", f"{p}.heading", f"{p}.bg", TEXT),
        ("link", f"{p}.link", f"{p}.bg", TEXT),
        ("button label", f"{p}.button-fg", f"{p}.button-bg", TEXT),
        ("button label on hover", f"{p}.button-fg", f"{p}.button-hover", TEXT),
        ("button edge", f"{p}.button-bg", f"{p}.bg", UI),
        ("focus ring", f"{p}.focus", f"{p}.bg", FOCUS_ON_DARK if surface in DARK else UI),
    ]


@pytest.mark.parametrize("palette", sorted(PRESETS))
@pytest.mark.parametrize("surface", SURFACES)
def test_every_surface_pair_passes_under_each_predefined_palette(
    palette: str, surface: str
) -> None:
    tokens = resolved(palette)
    for name, fg, bg, minimum in pairs(surface):
        ratio = contrast_ratio(tokens[fg], tokens[bg])
        assert ratio >= minimum, (
            f"{palette}/{surface} {name}: {tokens[fg]} on {tokens[bg]} {ratio:.2f}"
        )


def test_the_surfaces_follow_the_palette() -> None:
    assert resolved("red")["surface.primary.bg"] == PRESETS["red"]["primary"]
    assert resolved("blue")["surface.band.bg"] == PRESETS["blue"]["band"]
    assert resolved("blue")["surface.primary.bg"] != resolved("red")["surface.primary.bg"]


def test_the_scrim_has_a_strong_and_a_soft_opacity() -> None:
    tokens = resolved("red")
    assert float(tokens["scrim.soft"]) < float(tokens["scrim.opacity"]) <= 1


@pytest.mark.parametrize("name", TEXTURES)
def test_a_texture_is_an_own_work_svg_under_4kb_listed_in_the_licences(name: str) -> None:
    path = RIDE / "static" / "themes" / "ride" / "images" / name
    assert path.stat().st_size < 4096
    svg = path.read_text()
    assert svg.startswith("<svg")
    assert "<script" not in svg
    assert "href" not in svg
    assets = tomllib.loads((RIDE / "LICENSES.toml").read_text())["asset"]
    entries = [a for a in assets if f"images/{name}" in a["files"]]
    assert len(entries) == 1
    assert entries[0]["license"] == "CC0-1.0"


def css() -> str:
    return (RIDE / "static/themes/ride/css/38-surfaces.css").read_text()


def test_the_utilities_live_in_the_utilities_layer_only() -> None:
    source = css()
    assert source.count("@layer ") == 1
    assert "@layer utilities {" in source
    for name in ("surface-light", "surface-muted", "surface-band", "surface-primary", "eyebrow"):
        assert f".{name}" in source
    assert "images/edge-torn.svg" in source
    assert "images/texture-topo.svg" in source
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source), "colours only through tokens"
    assert "!important" not in source


@pytest.mark.usefixtures("english_interface")
def test_the_styleguide_section_shows_every_surface_with_its_parts() -> None:
    with use_theme("ride"):
        html = render_to_string("styleguide_surfaces.html", {})
    assert 'id="sg-surfaces"' in html
    assert "style=" not in html
    for surface in SURFACES:
        block = re.search(rf'<div class="surface-{surface} [^"]*">(.*?)</div>', html, re.S)
        assert block, surface
        assert all(part in block.group(1) for part in ('class="eyebrow"', "<h3>", "<p>", "<a "))
        assert 'class="button"' in block.group(1)


def test_the_band_focus_ring_uses_the_lighter_on_dark_token() -> None:
    tokens = resolved("red")
    assert tokens["surface.band.focus"] == tokens["color.focus-on-dark"]
    assert tokens["color.focus-on-dark"] != tokens["color.focus"]


def test_the_heading_rule_covers_h1_to_h6() -> None:
    source = (RIDE / "static/themes/ride/css/10-type-shapes.css").read_text()
    rule = source[: source.index("--s-heading")]
    selectors = rule[rule.rindex("{", 0, rule.rindex("{")) :]
    for level in range(1, 7):
        assert re.search(rf"\bh{level}\b", selectors), f"h{level}"


@pytest.mark.parametrize("palette", sorted(PRESETS))
def test_a_light_surface_nested_in_a_band_has_its_own_readable_pairs(palette: str) -> None:
    """Nesting `.surface-light` resets every --s-* variable, so its pairs are the light ones."""
    tokens = resolved(palette)
    for part in ("text", "heading", "link"):
        assert contrast_ratio(tokens[f"surface.light.{part}"], tokens["surface.light.bg"]) >= TEXT
    assert tokens["surface.light.bg"] != tokens["surface.band.bg"]


def test_the_surfaces_stylesheet_documents_nesting_and_sets_every_variable_per_surface() -> None:
    source = css()
    assert "surface nested in another" in " ".join(source.split())
    for surface in SURFACES:
        block = re.search(rf"^  \.surface-{surface} \{{\n    --s-bg:(.*?)\}}", source, re.S | re.M)
        assert block
        for var in VARS:
            assert f"--s-{var}:" in "--s-bg:" + block.group(1), f"{surface} {var}"


@pytest.mark.usefixtures("english_interface")
def test_the_styleguide_nests_a_light_card_in_the_band() -> None:
    with use_theme("ride"):
        html = render_to_string("styleguide_surfaces.html", {})
    band = re.search(r'<div class="surface-band [^"]*">(.*?)<h3>', html, re.S)
    assert band
    assert re.search(r'<div class="surface-band [^"]*">.*?<div class="surface-light ', html, re.S)
