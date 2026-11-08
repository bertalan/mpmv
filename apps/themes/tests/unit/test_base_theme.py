"""THM.3: the `base` theme's layouts and CSS rules (spec 14 §14.2, §14.5, §14.6).

The built `theme.css` of `base` carries the accessibility rules every theme inherits: a visible
`:focus-visible` outline in the focus colour that is never removed, `scroll-padding-top` for the
sticky header, the skip link, 24 px and 44 px target sizes, and animation only when the visitor
has not asked for reduced motion. CSS sources use colours only through `var(--…)`.
"""

import re
from pathlib import Path

import pytest

from apps.themes.build import render_themes
from apps.themes.tests.builders import SHIPPED_THEMES

BASE = SHIPPED_THEMES / "base"
RAW_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklch|oklab)\(")


@pytest.fixture(scope="module")
def base_css() -> str:
    return render_themes(SHIPPED_THEMES)["themes/base/theme.css"]


@pytest.mark.parametrize(
    "name",
    [
        "layouts/base.html",
        "layouts/account.html",
        "components/skip_link.html",
        "components/header.html",
        "components/language_switcher.html",
        "components/help_link.html",
    ],
)
def test_base_ships_the_layouts_and_shell_components(name: str) -> None:
    assert (BASE / "templates" / name).is_file()


def test_focus_is_a_visible_outline_in_the_focus_colour(base_css: str) -> None:
    rule = re.search(r":focus-visible\{([^}]*)\}", base_css)
    assert rule is not None
    assert "outline:" in rule.group(1)
    assert "var(--color-focus)" in rule.group(1)
    assert "var(--focus-width)" in rule.group(1)
    assert not re.search(r"outline:\s*(?:none|0)\b", base_css)


def test_the_focus_width_token_is_at_least_two_pixels(base_css: str) -> None:
    width = re.search(r"--focus-width:([0-9.]+)px", base_css)
    assert width is not None
    assert float(width.group(1)) >= 2


def test_the_sticky_header_never_hides_the_focused_element(base_css: str) -> None:
    assert "scroll-padding-top:var(--header-height)" in base_css


def test_the_skip_link_is_styled_to_appear_on_focus(base_css: str) -> None:
    assert ".skip-link" in base_css
    assert ".skip-link:focus" in base_css


def test_targets_are_at_least_24px_and_primary_controls_44px(base_css: str) -> None:
    assert re.search(r"--target-min:(?:24px|1\.5rem)", base_css)
    assert re.search(r"--target-primary:(?:44px|2\.75rem)", base_css)
    assert "min-block-size:var(--target-min)" in base_css
    assert "min-block-size:var(--target-primary)" in base_css


def test_motion_only_without_a_reduced_motion_preference(base_css: str) -> None:
    assert "@media (prefers-reduced-motion:reduce)" in base_css
    assert "transition" in base_css
    for match in re.finditer(r"transition:[^;}]+", base_css):
        before = base_css[: match.start()]
        assert before.rfind("prefers-reduced-motion:no-preference") > before.rfind("@layer ")


def test_the_disclosure_navigation_collapses_only_once_enhanced(base_css: str) -> None:
    assert ".site-nav.is-enhanced:not(.is-open)" in base_css


def test_css_sources_use_colours_only_through_tokens() -> None:
    offenders = [
        f"{path.relative_to(SHIPPED_THEMES)}: {match.group(0)}"
        for path in sorted(SHIPPED_THEMES.glob("*/static/themes/*/css/*.css"))
        for match in RAW_COLOUR.finditer(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_theme_js_module_is_built_next_to_the_css() -> None:
    outputs = render_themes(SHIPPED_THEMES)
    module = outputs["themes/base/js/base.js"]
    assert 'Alpine.data("disclosure"' in module
    assert "alpine:init" in module


def test_layouts_link_the_built_theme_css_and_vendored_scripts() -> None:
    layout = (BASE / "templates" / "layouts" / "base.html").read_text(encoding="utf-8")
    assert "theme.css" in layout
    assert "'vendor/htmx.min.js'" in layout
    assert "'vendor/alpine-csp.min.js'" in layout
    assert "htmx-config" in layout
    assert "cdn" not in layout.lower()


def test_no_template_still_extends_the_pre_theme_bases() -> None:
    root = Path(__file__).resolve().parents[4]
    for path in [*root.glob("apps/*/templates/**/*.html"), *BASE.glob("templates/**/*.html")]:
        text = path.read_text(encoding="utf-8")
        assert '"cms/base.html"' not in text, path
        assert '"core/base.html"' not in text, path
