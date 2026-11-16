"""THM.13 part 3: Bootstrap components in the look of `ride` (spec 14 §14.4, §14.5, §14.9).

The CSS maps the components (buttons, forms, alerts, badges, cards, pagination and the
overlapping accordion, breadcrumb, table, toast) on tokens, gives each focusable one a visible
focus ring, and the collision whitelist says why each overlap is still accepted. The browser
checks (axe, keyboard) are in tests/e2e/test_ride_bootstrap_components.py.
"""

import re

import pytest

from apps.themes.build import read_css_sources, read_manifest_sources, read_tokens
from apps.themes.collisions import ALLOWED_COLLISIONS
from apps.themes.engine import build_registry, contrast_ratio, resolve_tokens
from apps.themes.tests.builders import SHIPPED_THEMES

RIDE = SHIPPED_THEMES / "ride"
TEMPLATES = RIDE / "templates"
RAW_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(")


@pytest.fixture(scope="module")
def css() -> str:
    return "\n".join(read_css_sources(RIDE, "ride"))


@pytest.fixture(scope="module")
def tokens() -> dict[str, str]:
    registry = build_registry(read_manifest_sources(SHIPPED_THEMES))
    chain = registry.chain("ride")
    return resolve_tokens(chain, {slug: read_tokens(SHIPPED_THEMES / slug) for slug in chain})


MAPPED = ("btn", "form-control", "alert", "badge", "card", "pagination", "accordion", "breadcrumb")


@pytest.mark.parametrize("component", MAPPED)
def test_each_component_is_mapped_on_tokens(component: str, css: str) -> None:
    pattern = rf"--bs-{re.escape(component)}[\w-]*\s*:\s*var\(--"
    if component == "form-control":  # Bootstrap's form controls read the shared --bs-* map
        pattern = r"\.form-control[^{]*\{[^}]*var\(--color-"
    assert re.search(pattern, css), f"ride CSS does not map {component} on tokens"


def test_the_table_and_toast_are_mapped_too(css: str) -> None:
    assert re.search(r"--bs-table-[\w-]*\s*:\s*var\(--", css)
    assert re.search(r"--bs-toast-[\w-]*\s*:\s*var\(--", css)


def test_the_component_css_holds_no_raw_colour(css: str) -> None:
    bootstrap_block = css[css.find("Bootstrap components") :]
    assert "Bootstrap components" in css
    assert RAW_COLOUR.findall(bootstrap_block) == []


FOCUSABLE = (
    ".btn",
    ".form-control",
    ".form-select",
    ".form-check-input",
    ".page-link",
    ".dropdown-item",
    ".btn-close",
    ".accordion-button",
)


@pytest.mark.parametrize("selector", FOCUSABLE)
def test_each_focusable_component_has_a_visible_focus_ring(selector: str, css: str) -> None:
    assert re.search(rf"{re.escape(selector)}:focus(-visible)?\b", css), selector
    rule = re.search(r"\.btn:focus-visible[^{]*\{([^}]*)\}", css)
    assert rule is not None
    assert "outline: var(--focus-width) solid var(--color-focus)" in rule.group(1)


def test_bootstrap_focus_shadows_are_replaced_by_the_outline(css: str) -> None:
    # Bootstrap draws the ring as a 25 % box-shadow (below 3:1) and sets `outline: 0`.
    assert "box-shadow: none" in css
    assert "--bs-btn-focus-box-shadow" in css


# Foreground and background tokens the component CSS pairs (WCAG 1.4.3, THM.5 contract).
PAIRS = (
    ("color.text-inverse", "color.accent"),
    ("color.text-inverse", "color.accent-hover"),
    ("color.on-secondary", "color.secondary"),
    ("color.on-secondary", "color.secondary-hover"),
    ("color.text-inverse", "color.surface-inverse"),
    ("color.text-inverse", "color.danger"),
    ("color.text-inverse", "color.success"),
    ("color.text-inverse", "color.info"),
    ("color.accent", "color.surface"),
    ("color.link", "color.surface"),
)


@pytest.mark.parametrize(("fg", "bg"), PAIRS)
def test_component_colour_pairs_meet_aa(fg: str, bg: str, tokens: dict[str, str]) -> None:
    assert contrast_ratio(tokens[fg], tokens[bg]) >= 4.5


def test_the_component_css_uses_only_pairs_of_the_table(css: str) -> None:
    block = css[css.find("Bootstrap components") :]
    for token in ("--color-text-inverse", "--color-on-secondary", "--color-accent-hover"):
        assert token in block


def test_the_warning_family_never_carries_light_text(css: str) -> None:
    rule = re.search(r"\.btn-warning\s*\{([^}]*)\}", css)
    assert rule is not None
    assert "--bs-btn-color: var(--color-on-secondary)" in rule.group(1)


def test_collision_reasons_no_longer_wait_for_part_3() -> None:
    for name, reason in ALLOWED_COLLISIONS.items():
        assert "part 3" not in reason, name


def test_every_overlapping_component_but_the_carousel_is_mapped(css: str) -> None:
    for name in ALLOWED_COLLISIONS:
        if name in {"visually-hidden", "carousel"}:
            continue
        assert f"--bs-{name}-" in css, f"{name} is on the whitelist but ride maps nothing"


def test_the_styleguide_has_a_foundation_slot_that_ride_fills() -> None:
    base = (SHIPPED_THEMES / "base" / "templates" / "styleguide.html").read_text()
    assert "styleguide_foundation.html" in base
    ride = (TEMPLATES / "styleguide_foundation.html").read_text()
    assert (SHIPPED_THEMES / "base" / "templates" / "styleguide_foundation.html").exists()
    for needle in ('data-bs-toggle="modal"', 'data-bs-toggle="dropdown"', "btn-close"):
        assert needle in ride
    assert not re.search(r"\son\w+=|\sstyle=", ride)
