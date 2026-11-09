"""THM.6: the `ride` vertical theme (spec 14 §14.4, §14.7, §14.8, §14.11).

`ride` is a child of `base`: it sets tokens, fonts, icons, shapes and a few template overrides
(header, hero, footer, home blocks). Written clean-room from the lane-a brief and the public
spec; nothing derives from a reference template. These tests pin the brief: the colour values
and every text pair of it (WCAG AA), the type scale, shapes, breakpoints, reduced motion, the
self-hosted fonts and icon sprite with their licences, and the THM.5 gates.
"""

import re
import tomllib
from collections.abc import Iterator

import pytest

from apps.themes.build import (
    read_css_sources,
    read_manifest_sources,
    read_tokens,
    render_themes,
)
from apps.themes.contract import TOKEN_CONTRACT
from apps.themes.engine import (
    build_registry,
    check_tokens,
    contrast_ratio,
    resolve_tokens,
)
from apps.themes.gates import CSS_BUDGET, compressed_size, run_gates
from apps.themes.tests.builders import SHIPPED_THEMES

RIDE = SHIPPED_THEMES / "ride"
STATIC = RIDE / "static" / "themes" / "ride"
AA = 4.5
UI = 3.0
RAW_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklch|oklab)\(")


@pytest.fixture(scope="module")
def tokens() -> dict[str, str]:
    registry = build_registry(read_manifest_sources(SHIPPED_THEMES))
    chain = registry.chain("ride")
    return resolve_tokens(chain, {slug: read_tokens(SHIPPED_THEMES / slug) for slug in chain})


@pytest.fixture(scope="module")
def theme_css() -> str:
    return render_themes(SHIPPED_THEMES)["themes/ride/theme.css"]


@pytest.fixture(scope="module")
def sources() -> str:
    return "\n".join(read_css_sources(RIDE, "ride"))


# --- Manifest and gates


def test_ride_is_a_registered_child_of_base() -> None:
    registry = build_registry(read_manifest_sources(SHIPPED_THEMES))
    assert registry.errors == ()
    assert registry.chain("ride") == ("ride", "base")


def test_ride_passes_every_thm5_gate() -> None:
    failures = [f for f in run_gates(SHIPPED_THEMES) if f.theme == "ride"]
    assert failures == [], "\n".join(str(f) for f in failures)


def test_ride_tokens_meet_the_token_contract(tokens: dict[str, str]) -> None:
    assert check_tokens(tokens, TOKEN_CONTRACT) == []


def test_the_compressed_css_is_within_the_budget(theme_css: str) -> None:
    assert compressed_size(theme_css.encode()) < CSS_BUDGET


# --- Colours (brief): values and every text pair


def test_the_palette_values_of_the_brief(tokens: dict[str, str]) -> None:
    assert tokens["color.accent-fill"].upper() == "#E3051A"
    assert tokens["color.link"].upper() == "#C40417"
    assert tokens["color.surface-inverse"].upper() == "#30373F"
    assert tokens["color.heading"].upper() == "#242424"
    assert tokens["color.text"].upper() == "#343434"
    assert tokens["color.border"].upper() == "#E5E5E5"
    assert tokens["color.border-strong"].upper() == "#D5D5D5"
    assert tokens["color.surface"].upper() == "#FFFFFF"
    assert tokens["color.surface-raised"].upper() == "#F9F9F9"
    assert tokens["color.accent-tint"].upper() == "#FFE8E8"
    assert tokens["color.overlay"].upper() == "#051026"


def test_muted_text_is_no_lighter_than_646464(tokens: dict[str, str]) -> None:
    assert contrast_ratio(tokens["color.text-muted"], "#FFFFFF") >= contrast_ratio(
        "#646464", "#FFFFFF"
    )


# (foreground token, background token, minimum): every text pair the brief allows
TEXT_PAIRS = [
    ("color.on-accent", "color.accent-fill", AA),  # white on the red fill, 4.88:1
    ("color.link", "color.surface", AA),  # accent text on white, 6.22:1
    ("color.link", "color.surface-raised", AA),
    ("color.link", "color.accent-tint", AA),
    ("color.heading", "color.surface", AA),
    ("color.heading", "color.surface-raised", AA),
    ("color.heading", "color.accent-tint", AA),
    ("color.text", "color.surface", AA),
    ("color.text", "color.surface-raised", AA),
    ("color.text", "color.accent-tint", AA),
    ("color.text-muted", "color.surface", AA),
    ("color.text-muted", "color.surface-raised", AA),
    ("color.text-muted", "color.accent-tint", AA),
    ("color.text-inverse", "color.surface-inverse", AA),  # header bar and footer
    ("color.on-secondary", "color.secondary", AA),  # filled dark button
    ("color.on-secondary", "color.secondary-hover", AA),
    ("color.secondary-text", "color.surface", AA),  # outline dark button
    ("color.link", "color.surface", AA),  # outline accent button
    ("color.text-inverse", "color.overlay", AA),  # hero text on the scrim
]
UI_PAIRS = [
    ("color.accent-fill", "color.surface", UI),
    ("color.border-control", "color.surface", UI),
    ("color.focus", "color.surface", UI),
    ("color.focus", "color.surface-inverse", UI),
    ("color.focus", "color.accent-fill", 1.0),
]


@pytest.mark.parametrize(("fg", "bg", "minimum"), [*TEXT_PAIRS, *UI_PAIRS])
def test_every_pair_of_the_brief_passes(
    tokens: dict[str, str], fg: str, bg: str, minimum: float
) -> None:
    ratio = contrast_ratio(tokens[fg], tokens[bg])
    assert ratio >= minimum, f"{fg} on {bg}: {ratio:.2f}:1"


def test_white_on_the_accent_fill_is_the_measured_488(tokens: dict[str, str]) -> None:
    assert round(contrast_ratio("#FFFFFF", tokens["color.accent-fill"]), 2) == 4.88
    assert round(contrast_ratio(tokens["color.link"], "#FFFFFF"), 2) == 6.22


def test_the_accent_is_never_text_on_the_dark_surface_or_the_tint(
    tokens: dict[str, str],
) -> None:
    """The link and the fill both fail on the dark surface, so the CSS must not pair them."""
    assert contrast_ratio(tokens["color.link"], tokens["color.surface-inverse"]) < AA
    assert contrast_ratio(tokens["color.accent-fill"], tokens["color.accent-tint"]) < AA


def test_the_hero_scrim_keeps_white_text_readable_over_the_brightest_image(
    tokens: dict[str, str],
) -> None:
    """Axe cannot measure text over an image, so the worst case is computed: a pure white
    photo under the scrim at its weakest opacity (`--scrim-opacity`)."""
    opacity = float(tokens["scrim.opacity"])
    navy = tokens["color.overlay"].lstrip("#")
    channels = [int(navy[i : i + 2], 16) for i in (0, 2, 4)]
    blended = "#" + "".join(f"{round(c * opacity + 255 * (1 - opacity)):02X}" for c in channels)
    assert contrast_ratio("#FFFFFF", blended) >= AA, f"{blended} under opacity {opacity}"


# --- Type, spacing, shape


def _clamp_max_px(value: str) -> float:
    """The upper bound of `clamp(min, preferred, max)` in px (1rem = 16px)."""
    maximum = value.rsplit(",", 1)[-1].strip(" )")
    number = float(re.sub(r"[a-z%]+$", "", maximum))
    return number * 16 if maximum.endswith("rem") else number


def test_the_body_is_18px_on_desktop_and_16px_on_mobile(tokens: dict[str, str]) -> None:
    value = tokens["step.0"]
    assert value.startswith("clamp(1rem,")
    assert _clamp_max_px(value) == 18


@pytest.mark.parametrize(
    ("step", "desktop"), [("step.4", 48), ("step.3", 36), ("step.2", 24), ("step.1", 20)]
)
def test_headings_follow_the_scale_h1_48_h2_36_h3_24_h4_20(
    tokens: dict[str, str], step: str, desktop: int
) -> None:
    assert _clamp_max_px(tokens[step]) == desktop
    assert tokens[step].startswith("clamp(")


def test_the_hero_display_size_is_fluid_up_to_88px(tokens: dict[str, str]) -> None:
    assert _clamp_max_px(tokens["step.display"]) == 88
    assert tokens["step.display"].startswith("clamp(")


def test_fonts_are_barlow_and_barlow_condensed_with_fallbacks(tokens: dict[str, str]) -> None:
    assert tokens["font.body"].startswith("'Barlow', ")
    assert tokens["font.display"].startswith("'Barlow Condensed', ")
    assert "sans-serif" in tokens["font.body"]
    assert "sans-serif" in tokens["font.display"]


def test_section_rhythm_and_card_padding(tokens: dict[str, str]) -> None:
    assert _clamp_max_px(tokens["space.section"]) == 112
    assert tokens["space.section"].startswith("clamp(4rem,")
    assert _clamp_max_px(tokens["space.section-end"]) < 112
    assert tokens["card.padding"].startswith("clamp(1.5rem,")
    assert _clamp_max_px(tokens["card.padding"]) == 48


def test_the_radii_of_the_brief(tokens: dict[str, str]) -> None:
    assert tokens["radius.control"] == "4px"
    assert tokens["radius.card"] == "10px"
    assert tokens["radius.pill"] == "999px"
    assert tokens["radius.round"] == "50%"


def test_buttons_are_at_least_44px_high(tokens: dict[str, str]) -> None:
    assert tokens["target.primary"] == "2.75rem"


def test_line_height_is_about_one_and_a_half(sources: str) -> None:
    match = re.search(r"body\s*\{[^}]*line-height:\s*([0-9.]+)", sources)
    assert match is not None
    assert 1.45 <= float(match.group(1)) <= 1.55


# --- CSS rules


def test_ride_css_uses_colours_only_through_tokens(sources: str) -> None:
    code = re.sub(r"/\*.*?\*/", "", sources, flags=re.S)
    assert RAW_COLOUR.findall(code) == []


def test_ride_css_has_no_external_request(theme_css: str) -> None:
    assert "@import" not in theme_css
    assert not re.search(r"url\(\s*['\"]?(?:https?:)?//", theme_css)


def _media_blocks(css: str) -> Iterator[tuple[str, str]]:
    """(prelude, body) of every top-level or nested `@media` and `@container` block."""
    for match in re.finditer(r"@(?:media|container)[^{]*\{", css):
        depth, index = 1, match.end()
        while depth and index < len(css):
            depth += {"{": 1, "}": -1}.get(css[index], 0)
            index += 1
        yield match.group(0), css[match.end() : index - 1]


def test_breakpoints_are_40_64_and_80rem(sources: str) -> None:
    for prelude, _ in _media_blocks(sources):
        if "@media" in prelude and "prefers-" not in prelude:
            assert re.search(r"\b(?:40|64|80)rem\b", prelude), prelude


def test_ride_uses_container_queries_for_cards_and_grids(sources: str) -> None:
    assert "container-type:" in sources.replace(" ", "")
    assert "@container" in sources


def test_transitions_only_inside_prefers_reduced_motion_no_preference(sources: str) -> None:
    code = re.sub(r"/\*.*?\*/", "", sources, flags=re.S)
    allowed = [
        body
        for prelude, body in _media_blocks(code)
        if "prefers-reduced-motion: no-preference" in prelude
    ]
    outside = code
    for body in allowed:
        outside = outside.replace(body, "")
    assert "transition" not in outside
    assert "animation" not in outside
    assert any("transition" in body for body in allowed)


def test_the_primary_button_hover_shadow_is_the_only_shadow_on_buttons(sources: str) -> None:
    assert re.search(r"\.button:not\([^)]*\):hover[^{]*\{[^}]*box-shadow", sources)
    assert "text-shadow" not in sources


def test_headings_are_uppercase_only_at_display_sizes(sources: str) -> None:
    uppercase = re.findall(r"([^{}]+)\{[^}]*text-transform:\s*uppercase", sources)
    assert uppercase, "display headings are uppercase"
    for selector in uppercase:
        assert not re.search(r"(?<![\w-])h[2-4]\b", selector), selector


def test_nothing_the_brief_drops_is_shipped(sources: str) -> None:
    for banned in ("@keyframes", "marquee", "preloader", "font-family: cursive", "icomoon"):
        assert banned not in sources, banned
    for path in (RIDE / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert "autoplay" not in text, path
        assert "<video" not in text, path
        assert "marquee" not in text, path


# --- Fonts and icons


FONT_FILES = (
    "fonts/barlow-400.woff2",
    "fonts/barlow-700.woff2",
    "fonts/barlow-condensed-600.woff2",
    "fonts/barlow-condensed-700.woff2",
)


@pytest.mark.parametrize("name", FONT_FILES)
def test_fonts_are_self_hosted_woff2_files(name: str) -> None:
    data = (STATIC / name).read_bytes()
    assert data[:4] == b"wOF2"
    assert len(data) < 40 * 1024  # latin + latin-ext subset


def test_font_face_rules_swap_and_point_at_shipped_files(theme_css: str) -> None:
    faces = re.findall(r"@font-face\s*\{[^}]*\}", theme_css)
    assert len(faces) == len(FONT_FILES)
    urls = []
    for face in faces:
        assert "font-display:swap" in face.replace(" ", "")
        urls += re.findall(r"url\(\s*['\"]?([^'\")\s]+)", face)
    assert sorted(urls) == sorted(FONT_FILES)
    assert {"'Barlow'", "'Barlow Condensed'"} <= set(
        re.findall(r"font-family:\s*([^;}]+)", "".join(faces))
    )


def test_licences_list_every_third_party_asset() -> None:
    data = tomllib.loads((RIDE / "LICENSES.toml").read_text(encoding="utf-8"))
    entries = {entry["name"]: entry for entry in data["asset"]}
    assert {"Barlow", "Barlow Condensed", "Lucide"} <= set(entries)
    for name in ("Barlow", "Barlow Condensed"):
        assert entries[name]["license"] == "OFL-1.1"
        assert entries[name]["kind"] == "font"
        assert entries[name]["source"].startswith("https://github.com/google/fonts")
        assert re.search(r"[0-9a-f]{40}", entries[name]["source"]), "pinned to a commit"
    assert entries["Lucide"]["license"] in {"ISC", "MIT"}
    assert entries["Lucide"]["kind"] == "icons"
    assert re.search(r"[0-9a-f]{40}|v?\d+\.\d+\.\d+", entries["Lucide"]["source"])
    listed = {file for entry in data["asset"] for file in entry["files"]}
    assert set(FONT_FILES) <= listed


def test_the_sprite_has_a_symbol_for_every_icon_name_of_the_base_component() -> None:
    base_icon = (SHIPPED_THEMES / "base/templates/components/icon.html").read_text()
    names = set(re.findall(r'name == "([a-z-]+)"', base_icon))
    sprite = (STATIC / "icons" / "sprite.svg").read_text(encoding="utf-8")
    symbols = set(re.findall(r'<symbol id="([a-z-]+)"', sprite))
    assert names <= symbols, names - symbols
    assert {"chevron-down", "arrow-right"} <= symbols
    assert "<script" not in sprite
    assert "href=" not in sprite  # no external reference


def test_placeholder_images_are_our_own_svg_with_a_licence_entry() -> None:
    data = tomllib.loads((RIDE / "LICENSES.toml").read_text(encoding="utf-8"))
    images = [entry for entry in data["asset"] if entry["kind"] == "image"]
    assert images, "the placeholder imagery is recorded"
    for entry in images:
        assert entry["license"] == "CC0-1.0"
        for file in entry["files"]:
            assert file.endswith(".svg")
            svg = (STATIC / file).read_text(encoding="utf-8")
            assert "<image" not in svg  # no embedded raster (stock photo)
            assert "<script" not in svg
