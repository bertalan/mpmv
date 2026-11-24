"""THM.30: the `ride` 404 and 500 pages (spec 14 §14.9, ride fidelity 12/12).

A full-bleed own illustration, a large display code, a way home and (on the 404) a search. The 500
page is fully static: its templates load no database-backed tag library, and rendering it needs no
query (the unit suite blocks every query, so any access fails here).
"""

import re
import tomllib
from pathlib import Path

import pytest
from django.http import HttpRequest
from django.templatetags.static import static
from django.test import RequestFactory
from django.urls import reverse
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.themes import views
from apps.themes.contract import TEXT
from apps.themes.engine import contrast_ratio
from apps.themes.palettes import PRESETS, resolve_with_palette
from apps.themes.services import get_registry, theme_tokens, themes_dir
from tests.i18n import translated

pytestmark = pytest.mark.usefixtures("english_interface")

RIDE = Path(__file__).resolve().parents[4] / "themes" / "ride"
TEMPLATES = RIDE / "templates"
STATIC = RIDE / "static" / "themes" / "ride"
SAFE_LIBRARIES = {"i18n", "static"}  # no database, session or request needed


@pytest.fixture
def request_() -> HttpRequest:
    return RequestFactory().get(reverse("wagtail_serve", args=["broken/"]))


@pytest.fixture
def ride(settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    settings.MPMV_HELP_URL = "/help/"


def loaded_libraries(path: Path) -> set[str]:
    found: set[str] = set()
    for line in re.findall(r"\{%\s*load\s+([^%]+?)\s*%\}", path.read_text()):
        found.update(line.split())
    return found


@pytest.mark.parametrize("name", ["404.html", "500.html", "layouts/error_display.html"])
def test_the_error_templates_load_no_database_backed_tag_library(name: str) -> None:
    assert (TEMPLATES / name).is_file()
    assert loaded_libraries(TEMPLATES / name) <= SAFE_LIBRARIES


@pytest.mark.usefixtures("ride")
def test_the_500_page_is_a_full_bleed_hero_with_a_display_code(request_: HttpRequest) -> None:
    response = views.server_error(request_)
    html = response.content.decode()
    assert response.status_code == 500
    assert 'data-theme="ride"' in html
    assert re.search(r'<section class="error-hero full-bleed"', html)
    assert re.search(r'<p class="error-hero__code" aria-hidden="true">500</p>', html)
    assert len(re.findall(r"<h1\b", html)) == 1
    assert translated("Something went wrong") in html.split("<h1", 1)[1].split("</h1>", 1)[0]
    assert f'class="btn btn-primary" href="{reverse("home")}"' in html
    assert 'id="error-search"' not in html  # static page: the search belongs to the 404


@pytest.mark.usefixtures("ride")
def test_the_500_page_loads_no_palette_or_site_settings_css(request_: HttpRequest) -> None:
    html = views.server_error(request_).content.decode()
    assert html.count('rel="stylesheet"') == 1
    assert static("themes/ride/theme.css") in html
    assert 'class="garage' not in html


@pytest.mark.usefixtures("ride")
def test_the_404_page_has_the_display_code_a_way_home_and_a_search() -> None:
    request = RequestFactory().get(reverse("wagtail_serve", args=["nope/"]))
    html = views._render_plain(request, "404.html")
    assert re.search(r'<p class="error-hero__code" aria-hidden="true">404</p>', html)
    assert len(re.findall(r"<h1\b", html)) == 1
    assert f'class="btn btn-primary" href="{reverse("home")}"' in html
    form = html.split('for="error-search"', 1)[1].split("</form>", 1)[0]
    assert 'name="s"' in form
    action = re.escape(reverse("wagtail_serve", args=[""]))
    assert re.search(
        rf'<form[^>]+role="search"[^>]+action="{action}"', html.split("error-hero", 1)[1]
    )


@pytest.mark.usefixtures("ride")
def test_the_404_page_does_not_echo_the_path_or_the_query() -> None:
    request = RequestFactory().get(reverse("wagtail_serve", args=["secret-x/"]), {"s": "probe-y"})
    html = views._render_plain(request, "404.html")
    assert "secret-x" not in html
    assert "probe-y" not in html


@pytest.mark.parametrize("slug", sorted(PRESETS))
def test_the_error_hero_text_has_enough_contrast_in_every_ride_palette(slug: str) -> None:
    """The hero is `color.text-inverse` on `color.surface-inverse`, which a palette sets (band)."""
    tokens = resolve_with_palette(
        get_registry().chain("ride"), theme_tokens(themes_dir()), PRESETS[slug]
    )
    ratio = contrast_ratio(tokens["color.text-inverse"], tokens["color.surface-inverse"])
    assert ratio >= TEXT, f"{slug}: {ratio:.2f}"


def test_the_error_hero_sits_flush_under_the_header() -> None:
    """`.site-main` pads its top by `--space-l`; the display hero pulls that padding back."""
    rules = (STATIC / "css" / "62-error-pages.css").read_text()
    block = re.search(r"\.error-page--display\s*\{([^}]*)\}", rules)
    assert block is not None
    assert "margin-block-start: calc(-1 * var(--space-l))" in block.group(1)


def test_the_illustration_is_an_own_work_svg_under_4kb_listed_in_the_licences() -> None:
    path = STATIC / "images" / "error-road.svg"
    assert path.stat().st_size < 4096
    svg = path.read_text()
    assert svg.startswith("<svg")
    assert "<script" not in svg
    assert "href" not in svg
    assets = tomllib.loads((RIDE / "LICENSES.toml").read_text())["asset"]
    entries = [a for a in assets if "images/error-road.svg" in a["files"]]
    assert len(entries) == 1
    assert entries[0]["license"] == "CC0-1.0"


def test_the_error_css_uses_tokens_only_and_the_illustration() -> None:
    source = (STATIC / "css" / "62-error-pages.css").read_text()
    assert "images/error-road.svg" in source
    assert "@layer overrides {" in source
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source), "colours only through tokens"
    # Static page: no animation or transition exists, so there is no motion to switch off.
    rules = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    assert not re.search(r"\b(animation|transition|scroll-behavior)\b", rules)
