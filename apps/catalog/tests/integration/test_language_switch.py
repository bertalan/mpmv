"""I18N.6 (owner bug): the language switch stays on the same object.

On a listing, storefront or CMS page the switch leads to the object's translation, the same
targets as the `hreflang` alternates (one source). A language without a translation leads to
the language's home with a notice that the content is not available in it.
"""

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.tests.factories import BrandFactory, BrandTranslationFactory
from apps.catalog.tests.integration.test_listing_views import compat, listed  # noqa: F401
from apps.core.i18n import configured_languages
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorTranslationFactory

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED
NOTICE = "not available in this language"
FORM = re.compile(
    r'<input type="hidden" name="exact" value="(?P<exact>[01])">\s*'
    r'<input type="hidden" name="notice" value="(?P<notice>[01])">\s*'
    r'<input type="hidden" name="next" value="(?P<next>[^"]*)">\s*'
    r'<button type="submit" name="language" value="(?P<code>[a-z-]+)"'
)


def switch_targets(html: str) -> dict[str, tuple[str, bool]]:
    """{language: (target URL, notice shown)} of the switcher forms."""
    return {m["code"]: (m["next"], m["notice"] == "1") for m in FORM.finditer(html)}


def hreflang(html: str) -> dict[str, str]:
    return dict(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)"', html))


def same_as_hreflang(html: str) -> None:
    """Every hreflang language is a switch target without notice, and no other one is."""
    targets = switch_targets(html)
    alternates = {code: url for code, url in hreflang(html).items() if code != "x-default"}
    for code, url in alternates.items():
        assert url.endswith(targets[code][0]), (code, url, targets[code])
        assert targets[code][1] is False
    for code, (_url, notice) in targets.items():
        if code not in alternates:
            assert notice is True, code


def test_a_term_page_switches_to_its_translation(client: Client, compat: dict[str, Any]) -> None:  # noqa: F811
    html = client.get("/compatibility/maker/").content.decode()
    targets = switch_targets(html)
    assert targets["it"] == ("/it/compatibilita/marca/", False)
    assert targets["en"][0] == "/compatibility/maker/"
    for code in ("de", "fr", "es"):
        assert targets[code] == (f"/{code}/", True)
    same_as_hreflang(html)


def test_a_brand_page_switches_to_its_translation(client: Client) -> None:
    brand = BrandFactory.create()
    BrandTranslationFactory.create(brand=brand, slug="acme", status=PUBLISHED)
    BrandTranslationFactory.create(brand=brand, language="it", slug="acme-it", status=PUBLISHED)
    listed(brand=brand)
    html = client.get("/brand/acme/").content.decode()
    targets = switch_targets(html)
    assert targets["it"] == ("/it/marchio/acme-it/", False)
    assert targets["de"] == ("/de/", True)
    same_as_hreflang(html)


def test_a_vendor_storefront_switches_to_its_translation(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    VendorTranslationFactory.create(vendor=vendor, language="en", status=PUBLISHED)
    VendorTranslationFactory.create(vendor=vendor, language="it", status=PUBLISHED)
    listed(vendor=vendor)
    html = client.get(f"/shop/{vendor.slug}/").content.decode()
    targets = switch_targets(html)
    assert targets["it"][0].startswith("/it/")
    assert vendor.slug in targets["it"][0]
    assert targets["it"][1] is False
    assert targets["fr"] == ("/fr/", True)
    same_as_hreflang(html)


def test_a_vendor_storefront_in_the_fallback_language_offers_the_other_languages_home(
    client: Client,
) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    listed(vendor=vendor)
    targets = switch_targets(client.get(f"/it/shop/{vendor.slug}/").content.decode())
    assert targets["de"] == ("/de/", True)


def test_switching_to_a_translation_goes_there_without_a_notice(
    client: Client,
    compat: dict[str, Any],  # noqa: F811
) -> None:
    response = client.post(
        reverse("switch_language"),
        {"language": "it", "next": "/it/compatibilita/marca/", "exact": "1", "notice": "0"},
        follow=True,
    )
    assert response.redirect_chain[-1][0] == "/it/compatibilita/marca/"
    assert NOTICE not in response.content.decode()


def test_switching_to_a_language_without_translation_shows_the_notice_once(
    client: Client,
    compat: dict[str, Any],  # noqa: F811
) -> None:
    response = client.post(
        reverse("switch_language"),
        {"language": "de", "next": "/de/", "exact": "1", "notice": "1"},
        follow=True,
    )
    assert response.redirect_chain[-1][0] == "/de/"
    assert NOTICE in response.content.decode()
    assert NOTICE not in client.get("/de/").content.decode()


def test_every_configured_language_has_a_switch_target(client: Client) -> None:
    listed()
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    html = client.get(f"/shop/{vendor.slug}/").content.decode()
    assert set(switch_targets(html)) == set(configured_languages())


def test_a_listing_never_carries_its_query_to_a_home_page(
    client: Client,
    compat: dict[str, Any],  # noqa: F811
) -> None:
    targets = switch_targets(client.get("/compatibility/maker/?utm_source=x").content.decode())
    assert targets["de"] == ("/de/", True)
