"""SEO.8 (1/2): the environment floor `MPMV_FORCE_NOINDEX` (D-35), pure parts."""

import pytest
from django.test import override_settings

from apps.catalog import listings
from apps.seo import robots

pytestmark = pytest.mark.unit


def test_forced_robots_txt_disallows_everything() -> None:
    assert robots.robots_txt(forced=True) == "User-agent: *\nDisallow: /\n"


def test_open_robots_txt_allows_and_disallows_private_areas() -> None:
    text = robots.robots_txt(forced=False)
    assert text.startswith("User-agent: *\n")
    assert "Disallow: /\n" not in text.replace("Disallow: /manager/", "")
    for path in ("/manager/", "/account/", "/django-admin/", "/admin/", "/i18n/"):
        assert f"Disallow: {path}\n" in text


def test_demo_mode_implies_forced_noindex() -> None:
    with override_settings(FORCE_NOINDEX=False, DEMO_MODE=True):
        assert robots.is_forced() is True
    with override_settings(FORCE_NOINDEX=False, DEMO_MODE=False):
        assert robots.is_forced() is False
    with override_settings(FORCE_NOINDEX=True, DEMO_MODE=False):
        assert robots.is_forced() is True


def test_listing_robots_is_noindex_nofollow_when_forced() -> None:
    params = listings.ListingParams()
    with override_settings(FORCE_NOINDEX=True):
        assert listings.listing_robots(params, total=5) == "noindex, nofollow"
    with override_settings(FORCE_NOINDEX=False, DEMO_MODE=False):
        assert listings.listing_robots(params, total=5) == listings.ROBOTS_INDEX
