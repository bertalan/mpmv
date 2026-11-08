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
        "components/footer.html",
        "components/field.html",
        "layouts/content.html",
        "layouts/error.html",
        "400.html",
        "403.html",
        "403_csrf.html",
        "404.html",
        "500.html",
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
    # --header-offset is the header's measured height (base.js); the token is the fallback.
    assert "scroll-padding-top:var(--header-offset,var(--header-height))" in base_css


def test_the_scroll_padding_follows_the_real_header_height() -> None:
    """THM.4: a long menu wraps the sticky header; the padding must follow it (WCAG 2.4.11)."""
    script = (BASE / "static" / "themes" / "base" / "js" / "base.js").read_text(encoding="utf-8")
    assert "new ResizeObserver(" in script
    assert 'setProperty("--header-offset"' in script
    # The token sets the header's own minimum height: writing it back would grow it forever.
    assert 'setProperty("--header-height"' not in script


def test_buttons_and_controls_have_their_component_rules(base_css: str) -> None:
    """THM.4 (1/3): the variant, size, state and group rules of the form and button components."""
    for selector in (
        ".button--danger",
        ".button--link",
        ".button--small",
        ".button--large",
        ".button--icon",
        ".button--loading",
        ".button__spinner",
        ".visually-hidden",
        ".field--check",
        ".field--group",
        ".field--disabled",
        ".error-summary",
    ):
        assert selector in base_css, selector


def narrow_rules(css: str) -> str:
    """The rules inside every `@media (width<48rem)` block of the minified CSS."""
    rules = []
    for match in re.finditer(r"@media \(width ?< ?48rem\)\{", css):
        depth, end = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[end], 0)
            end += 1
        rules.append(css[match.end() : end - 1])
    return "".join(rules)


def test_the_header_is_not_sticky_on_narrow_screens(base_css: str) -> None:
    # Below 48rem the header wraps to about 90 px: taller than the 4rem scroll padding, so a
    # sticky header could hide the focused element (WCAG 2.4.11). It scrolls away instead.
    narrow = narrow_rules(base_css)
    assert re.search(r"\.site-header\{[^}]*position:static", narrow), narrow
    assert re.search(r"html\{[^}]*scroll-padding-top:0", narrow), narrow


def test_form_fields_mark_required_and_invalid_states(base_css: str) -> None:
    assert ".field__required" in base_css
    assert '[aria-invalid="true"]' in base_css or "[aria-invalid=true]" in base_css
    assert ".errorlist" in base_css
    assert ".helptext" in base_css


@pytest.mark.parametrize("variant", ["info", "success", "warning", "error"])
def test_alerts_have_a_style_per_level(base_css: str, variant: str) -> None:
    assert f".alert--{variant}" in base_css


@pytest.mark.parametrize("variant", ["secondary", "ghost"])
def test_buttons_have_secondary_and_ghost_variants(base_css: str, variant: str) -> None:
    assert f".button--{variant}" in base_css


def test_the_content_shell_limits_the_text_width(base_css: str) -> None:
    assert re.search(r"\.page-content\{[^}]*max-inline-size:var\(--measure-text\)", base_css)


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
