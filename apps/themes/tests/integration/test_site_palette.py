"""THM.31: a site's palette is chosen through the audited service, linked after `theme.css`
as a hashed same-origin stylesheet, and ignored (with a check warning) once it fails against a
registered theme (spec 14 §14.12)."""

from pathlib import Path

import pytest
from django.db import connection
from django.db.models import ProtectedError
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.themes.checks import check_site_themes
from apps.themes.models import BrandPalette
from apps.themes.services import (
    active_palette,
    clear_caches,
    palette_href,
    palette_violations,
    set_site_theme,
)
from apps.themes.tests.builders import write_theme
from apps.themes.tests.factories import SiteFactory, ThemeSettingsFactory

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="staff:theme-admin@example.com", source=AuditLog.Source.ADMIN)
LINK = 'href="/palette/'


@pytest.fixture
def ride(settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    settings.ALLOWED_HOSTS = [".example.test"]
    clear_caches()


def page(client: Client, host: str) -> str:
    response = client.get("/", HTTP_HOST=host, follow=True)
    assert response.status_code == 200
    return response.content.decode()


def test_a_fresh_site_renders_ride_in_its_own_red_with_no_palette_link(
    client: Client, ride: None
) -> None:
    site = SiteFactory.create()
    html = page(client, site.hostname)
    assert "themes/ride/theme.css" in html
    assert LINK not in html


def test_choosing_blue_is_audited_and_switches_the_hashed_palette_stylesheet(
    client: Client, ride: None
) -> None:
    site = SiteFactory.create()
    blue = BrandPalette.objects.get(slug="blue")
    set_site_theme(site, "ride", palette=blue, ctx=CTX)
    entry = AuditLog.objects.get(action="themes.theme_settings.configured")
    assert entry.before is None
    assert entry.after is not None
    assert entry.after["palette"] == "blue"

    html = page(client, site.hostname)
    href = html.split(LINK)[1].split('"')[0]
    assert html.index("themes/ride/theme.css") < html.index(LINK)  # linked after theme.css
    response = client.get("/palette/" + href, HTTP_HOST=site.hostname)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/css")
    assert "immutable" in response["Cache-Control"]
    assert b"--color-accent:#046BD2" in response.content

    set_site_theme(site, "ride", palette=BrandPalette.objects.get(slug="petrol"), ctx=CTX)
    changed = AuditLog.objects.filter(action="themes.theme_settings.configured").latest("pk")
    assert changed.before is not None
    assert changed.after is not None
    assert changed.before["palette"] == "blue"
    assert changed.after["palette"] == "petrol"
    other = page(client, site.hostname).split(LINK)[1].split('"')[0]
    assert other != href  # the URL names the content


def test_a_stale_digest_gets_the_current_css_without_a_long_cache(
    client: Client, ride: None
) -> None:
    site = SiteFactory.create()
    set_site_theme(site, "ride", palette=BrandPalette.objects.get(slug="blue"), ctx=CTX)
    response = client.get("/palette/ride/blue.000000000000.css", HTTP_HOST=site.hostname)
    assert response.status_code == 200
    assert response["Cache-Control"] == "public, max-age=60"
    for path in ("ride/nope", "ghost/blue"):
        missing = client.get(f"/palette/{path}.000000000000.css", HTTP_HOST=site.hostname)
        assert missing.status_code == 404


def test_choosing_red_on_ride_links_nothing(client: Client, ride: None) -> None:
    site = SiteFactory.create()
    set_site_theme(site, "ride", palette=BrandPalette.objects.get(slug="red"), ctx=CTX)
    assert LINK not in page(client, site.hostname)


def test_a_dry_run_changes_nothing_and_a_failing_palette_is_refused(ride: None) -> None:
    site = SiteFactory.create()
    blue = BrandPalette.objects.get(slug="blue")
    changes = set_site_theme(site, "ride", palette=blue, ctx=CTX, dry_run=True)
    after = changes.changes[0].after
    assert after is not None
    assert after["palette"] == "blue"
    assert not AuditLog.objects.exists()
    blue.primary = "#FF6B6B"
    with pytest.raises(ValueError, match="on_primary on primary"):
        set_site_theme(site, "ride", palette=blue, ctx=CTX)


def test_a_stored_palette_that_fails_a_newly_registered_theme_is_not_applied(
    tmp_path: Path, settings: SettingsWrapper
) -> None:
    root = tmp_path / "themes"
    write_theme(root, "base", tokens='[color]\nsurface = "#FFFFFF"\n')
    settings.MPMV_THEMES_DIR = root
    settings.MPMV_THEME_DEFAULT = "base"
    settings.ALLOWED_HOSTS = [".example.test"]
    clear_caches()
    site = SiteFactory.create()
    red = BrandPalette.objects.get(slug="red")
    ThemeSettingsFactory.create(site=site, theme="base", palette=red)
    request = RequestFactory().get("/", HTTP_HOST=site.hostname)
    assert active_palette(request) == red
    assert palette_href(request).startswith("/palette/base/red.")
    assert check_site_themes(None, databases=["default"]) == []

    write_theme(root, "dim", tokens='[color]\nsurface = "#6E6E6E"\n')  # a darker surface
    clear_caches()
    request = RequestFactory().get("/", HTTP_HOST=site.hostname)
    assert active_palette(request) is None
    assert palette_href(request) == ""
    warnings = check_site_themes(None, databases=["default"])
    assert [w.id for w in warnings] == ["themes.W006"]
    assert "'red'" in warnings[0].msg
    assert site.hostname in warnings[0].msg


def test_the_palette_css_is_layered_and_the_page_gains_no_inline_style(
    client: Client, ride: None
) -> None:
    site = SiteFactory.create()
    set_site_theme(site, "ride", palette=BrandPalette.objects.get(slug="blue"), ctx=CTX)
    html = page(client, site.hostname)
    href = "/palette/" + html.split(LINK)[1].split('"')[0]
    css = client.get(href, HTTP_HOST=site.hostname).content.decode()
    assert css.startswith("@layer overrides{:root{")
    assert "<style" not in html
    assert " style=" not in html


def test_an_unknown_theme_is_a_404_before_any_palette_query(client: Client, ride: None) -> None:
    site = SiteFactory.create()
    with CaptureQueriesContext(connection) as captured:
        response = client.get("/palette/ghost/blue.000000000000.css", HTTP_HOST=site.hostname)
    assert response.status_code == 404
    assert not [q for q in captured if "themes_brandpalette" in q["sql"]]


def test_an_unknown_palette_is_a_404(client: Client, ride: None) -> None:
    site = SiteFactory.create()
    response = client.get("/palette/ride/nope.000000000000.css", HTTP_HOST=site.hostname)
    assert response.status_code == 404


@pytest.mark.parametrize("digest", ["short", "ZZZZZZZZZZZZ", "0000000000000", "00000000000g"])
def test_a_malformed_digest_does_not_reach_the_view(
    client: Client, ride: None, digest: str
) -> None:
    site = SiteFactory.create()
    response = client.get(f"/palette/ride/blue.{digest}.css", HTTP_HOST=site.hostname)
    assert response.status_code == 404
    assert resolve_url_matches(f"/palette/ride/blue.{digest}.css") is False


def resolve_url_matches(path: str) -> bool:
    try:
        resolve(path)
    except Resolver404:
        return False
    return True


def test_a_palette_in_use_cannot_be_deleted(ride: None) -> None:
    site = SiteFactory.create()
    blue = BrandPalette.objects.get(slug="blue")
    set_site_theme(site, "ride", palette=blue, ctx=CTX)
    with pytest.raises(ProtectedError):
        blue.delete()
    assert BrandPalette.objects.filter(slug="blue").exists()


def test_the_violations_are_computed_once_per_palette(ride: None) -> None:
    blue = BrandPalette.objects.get(slug="blue")
    palette_violations.cache_clear()
    assert blue.violations() == []
    assert blue.violations() == []
    info = palette_violations.cache_info()
    assert (info.hits, info.misses) == (1, 1)


def test_the_palette_stylesheet_answers_only_get(client: Client) -> None:
    url = "/palette/ride/blue.000000000000.css"
    for method in (client.post, client.put, client.delete):
        response = method(url)
        assert response.status_code == 405
        assert response["Allow"] == "GET"
