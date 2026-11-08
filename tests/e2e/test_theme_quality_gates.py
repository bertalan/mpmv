"""E2E (THM.5): the browser gates of every registered theme (spec 14 §14.6, §14.10, §14.11).

For each theme under `themes/`, the staff styleguide is rendered with that theme and

- axe-core (vendored, tags `wcag2a` … `wcag22aa`) reports zero violations;
- full-page screenshots at 360, 768 and 1280 px are compared with the CI-only baselines
  (skipped outside CI with a message; see tests/e2e/visual.py).

A contrast check proves the axe gate catches a 3:1 text pair.
"""

from pathlib import Path

import pytest
from playwright.sync_api import Page
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.themes.build import read_manifest_sources
from apps.themes.engine import build_registry
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.test_styleguide import IMAGES_LOADED, open_styleguide
from tests.e2e.visual import WIDTHS, assert_matches_baseline, require_visual_mode

pytestmark = pytest.mark.django_db(transaction=True)

THEMES_DIR = Path(__file__).resolve().parents[2] / "themes"
REGISTERED = sorted(build_registry(read_manifest_sources(THEMES_DIR)).manifests)


def styleguide_with(
    theme: str, page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    page.evaluate("() => [...document.images].forEach((img) => { img.loading = 'eager'; })")
    page.wait_for_function(IMAGES_LOADED, timeout=5000)
    page.evaluate("() => document.fonts.ready")
    assert page.locator("html").get_attribute("data-theme") == theme


@pytest.mark.parametrize("theme", REGISTERED)
def test_axe_finds_no_wcag_22_aa_violation_on_the_styleguide(
    theme: str, page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    styleguide_with(theme, page, live_server, settings)
    violations = run_axe(page)
    assert violations == [], format_violations(violations)


def test_axe_reports_a_3_to_1_text_pair(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    styleguide_with("base", page, live_server, settings)
    # Scripted CSSOM changes are allowed by the CSP (only markup styles are blocked).
    page.evaluate(
        "() => { const p = document.querySelector('main p');"
        " p.id = 'thm5-pale'; p.style.color = '#949494'; p.style.background = '#ffffff'; }"
    )
    violations = run_axe(page, include="#thm5-pale")
    assert [v["id"] for v in violations] == ["color-contrast"], format_violations(violations)


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", REGISTERED)
def test_the_styleguide_matches_its_visual_baseline(
    theme: str, width: int, page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    require_visual_mode()  # skips outside CI before logging in
    styleguide_with(theme, page, live_server, settings)
    assert_matches_baseline(page, theme=theme, name="styleguide", width=width)
