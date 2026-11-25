"""The site icon (THM.38): links in the page head, and `/favicon.ico` never a 404."""

import re
from pathlib import Path

import pytest
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.themes.services import clear_caches, icon_url
from apps.themes.tests.factories import SiteFactory, ThemeSettingsFactory

pytestmark = pytest.mark.django_db
ICON = re.compile(r'<link rel="(icon|apple-touch-icon)" href="([^"]+)"')


def home(client: Client, theme: str, settings: SettingsWrapper) -> tuple[str, str]:
    settings.MPMV_THEME_DEFAULT = theme
    settings.ALLOWED_HOSTS = [".example.test"]
    clear_caches()
    site = SiteFactory.create()
    response = client.get("/", HTTP_HOST=site.hostname, follow=True)
    assert response.status_code == 200
    return response.content.decode(), site.hostname


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_home_links_the_icon_set_and_each_file_exists(
    client: Client, settings: SettingsWrapper, theme: str
) -> None:
    html, _ = home(client, theme, settings)
    links = ICON.findall(html)
    assert sorted(rel for rel, _ in links) == ["apple-touch-icon", "icon", "icon"]
    for _, href in links:
        assert f"themes/{theme}/site-icon/" in href
        # The static server (WhiteNoise) is not in the test stack: the file must exist.
        relative = href.removeprefix(settings.STATIC_URL)
        assert (Path(settings.MPMV_THEMES_DIR) / theme / "static" / relative).is_file()


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_favicon_ico_redirects_to_the_active_themes_icon(
    client: Client, settings: SettingsWrapper, theme: str
) -> None:
    _, host = home(client, theme, settings)
    response = client.get("/favicon.ico", HTTP_HOST=host)
    assert response.status_code == 302
    assert f"themes/{theme}/site-icon/icon-32" in response["Location"]
    assert "max-age=" in response["Cache-Control"]
    # The redirect leads to a file that exists (no static server in the test stack).
    relative = response["Location"].removeprefix(settings.STATIC_URL)
    assert (Path(settings.MPMV_THEMES_DIR) / theme / "static" / relative).is_file()


def test_favicon_ico_follows_the_sites_own_theme_not_the_default(
    client: Client, settings: SettingsWrapper
) -> None:
    """Review of #1104: the site's `ThemeSettings` choice wins over `MPMV_THEME_DEFAULT`."""
    settings.MPMV_THEME_DEFAULT = "base"
    settings.ALLOWED_HOSTS = [".example.test"]
    clear_caches()
    site = SiteFactory.create()
    ThemeSettingsFactory.create(site=site, theme="ride")
    response = client.get("/favicon.ico", HTTP_HOST=site.hostname)
    assert response.status_code == 302
    assert "themes/ride/site-icon/icon-32" in response["Location"]


def test_a_theme_without_icons_inherits_the_base_icon(settings: SettingsWrapper) -> None:
    assert "themes/base/site-icon/icon.svg" in icon_url("no-such-theme", "icon.svg")
