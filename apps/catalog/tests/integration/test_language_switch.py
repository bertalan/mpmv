"""I18N.6 (owner bug): the language switch stays on the same object.

On a listing, storefront or CMS page the switch leads to the object's translation, the same
targets as the `hreflang` alternates (one source). A language without a translation stays on the
same object under the new prefix (I18N.22, owner decision 2026-11-21): the primary-language
fallback, with its notice, no `hreflang` for that language and a canonical to the primary page.
"""

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.tests.factories import BrandFactory, BrandTranslationFactory
from apps.catalog.tests.integration.test_listing_views import compat, listed  # noqa: F401
from apps.core.i18n import configured_languages, primary_language
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorTranslationFactory
from tests.i18n import fallback_notice, language_prefix, localized, other_language, third_language

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


FORM = re.compile(
    r'<input type="hidden" name="exact" value="(?P<exact>[01])">\s*'
    r'<input type="hidden" name="notice" value="(?P<notice>[01])">\s*'
    r'<input type="hidden" name="next" value="(?P<next>[^"]*)">\s*'
    r'<button type="submit" name="language" value="(?P<code>[a-z-]+)"'
)


def switch_targets(html: str) -> dict[str, tuple[str, bool]]:
    """{language: (target URL, notice shown)} of the switcher forms."""
    return {m["code"]: (m["next"], m["notice"] == "1") for m in FORM.finditer(html)}


def untranslated_languages() -> list[str]:
    """The configured languages that the test objects have no translation in (not the first two)."""
    taken = {primary_language(), other_language()}
    return [code for code in configured_languages() if code not in taken]


def hreflang(html: str) -> dict[str, str]:
    return dict(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)"', html))


def same_as_hreflang(html: str) -> None:
    """Every hreflang language is a switch target without notice, and no other one is."""
    targets = switch_targets(html)
    alternates = {code: url for code, url in hreflang(html).items() if code != "x-default"}
    for code, url in alternates.items():
        assert url.endswith(targets[code][0]), (code, url, targets[code])
        assert targets[code][1] is False
    for code, (url, _flash) in targets.items():
        if code not in alternates:  # the same object under the new prefix, never the home
            assert url.startswith(f"{language_prefix(code)}/")
            assert url != f"{language_prefix(code)}/"


def assert_stays_on_the_object(client: Client, html: str, code: str) -> None:
    """The switch to `code` (no translation) leads to the primary-language object under its
    prefix: fallback content, the notice, no `hreflang` for `code`, canonical to the primary."""
    targets = switch_targets(html)
    primary_url = targets[primary_language()][0]
    target, flash = targets[code]
    assert flash is False  # the fallback page shows the notice itself
    assert target.startswith(f"{language_prefix(code)}/")
    assert target != f"{language_prefix(code)}/"
    page = client.get(target)
    assert page.status_code == 200
    body = page.content.decode()
    assert fallback_notice(primary_language(), code) in body
    assert code not in hreflang(body)
    found = re.search(r'<link rel="canonical" href="([^"]+)"', body)
    assert found
    assert found.group(1).endswith(primary_url)


def test_a_term_page_switches_to_its_translation(client: Client, compat: dict[str, Any]) -> None:  # noqa: F811
    html = client.get("/compatibility/maker/").content.decode()
    targets = switch_targets(html)
    assert targets[other_language()] == (
        f"{language_prefix(other_language())}/compatibilita/marca/",
        False,
    )
    assert targets[primary_language()][0] == "/compatibility/maker/"
    same_as_hreflang(html)
    for code in untranslated_languages():
        assert_stays_on_the_object(client, html, code)


def test_a_brand_page_switches_to_its_translation(client: Client) -> None:
    brand = BrandFactory.create()
    BrandTranslationFactory.create(brand=brand, slug="acme", status=PUBLISHED)
    BrandTranslationFactory.create(
        brand=brand, language=other_language(), slug="acme-it", status=PUBLISHED
    )
    listed(brand=brand)
    html = client.get(localized("/brand/acme/")).content.decode()
    targets = switch_targets(html)
    assert targets[other_language()] == (
        localized("/brand/acme-it/", other_language()),
        False,
    )
    same_as_hreflang(html)
    for code in untranslated_languages():
        assert_stays_on_the_object(client, html, code)


def test_a_vendor_storefront_switches_to_its_translation(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    VendorTranslationFactory.create(vendor=vendor, language=primary_language(), status=PUBLISHED)
    VendorTranslationFactory.create(vendor=vendor, language=other_language(), status=PUBLISHED)
    listed(vendor=vendor)
    html = client.get(localized(f"/shop/{vendor.slug}/")).content.decode()
    targets = switch_targets(html)
    assert targets[other_language()][0].startswith(f"{language_prefix(other_language())}/")
    assert vendor.slug in targets[other_language()][0]
    assert targets[other_language()][1] is False
    same_as_hreflang(html)
    for code in untranslated_languages():
        assert_stays_on_the_object(client, html, code)


def test_a_vendor_storefront_in_the_fallback_language_stays_on_the_storefront(
    client: Client,
) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    listed(vendor=vendor)
    targets = switch_targets(
        client.get(localized(f"/shop/{vendor.slug}/", other_language())).content.decode()
    )
    for code in untranslated_languages():
        assert targets[code][0] != f"{language_prefix(code)}/"


def test_switching_to_a_translation_goes_there_without_a_notice(
    client: Client,
    compat: dict[str, Any],  # noqa: F811
) -> None:
    response = client.post(
        reverse("switch_language"),
        {
            "language": other_language(),
            "next": f"{language_prefix(other_language())}/compatibilita/marca/",
            "exact": "1",
            "notice": "0",
        },
        follow=True,
    )
    assert (
        response.redirect_chain[-1][0]
        == f"{language_prefix(other_language())}/compatibilita/marca/"
    )
    assert fallback_notice(primary_language(), other_language()) not in response.content.decode()
    assert fallback_notice(other_language(), other_language()) not in response.content.decode()


def test_a_switch_to_a_page_in_another_language_flashes_the_notice_once(
    client: Client,
    compat: dict[str, Any],  # noqa: F811
) -> None:
    """An event (no fallback) stays on its original-language page: the switch flashes the notice,
    with the endonym of the language of that page, in the language the visitor chose."""
    shown, chosen = other_language(), third_language()
    page = f"{language_prefix(shown)}/"
    response = client.post(
        reverse("switch_language"),
        {"language": chosen, "next": page, "exact": "1", "notice": "1"},
        follow=True,
    )
    assert response.redirect_chain[-1][0] == page
    assert fallback_notice(shown, chosen) in response.content.decode()
    assert fallback_notice(shown, chosen) not in client.get(page).content.decode()


def test_a_hostile_next_still_goes_to_the_home_with_the_flash(client: Client) -> None:
    response = client.post(
        reverse("switch_language"),
        {
            "language": other_language(),
            "next": "https://evil.example/",
            "exact": "1",
            "notice": "1",
        },
    )
    assert response["Location"] == "/"


def test_every_configured_language_has_a_switch_target(client: Client) -> None:
    listed()
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    html = client.get(localized(f"/shop/{vendor.slug}/")).content.decode()
    assert set(switch_targets(html)) == set(configured_languages())


def test_a_listing_switch_stays_on_the_listing_not_on_a_home_page(
    client: Client,
    compat: dict[str, Any],  # noqa: F811
) -> None:
    targets = switch_targets(client.get("/compatibility/maker/?utm_source=x").content.decode())
    for code in untranslated_languages():
        assert targets[code][0] != f"{language_prefix(code)}/"


@pytest.fixture(autouse=True)
def _site_allows_indexing(db: None) -> None:
    """The canonical and hreflang links are for indexable pages: the site's switch is on."""
    from django.apps import apps
    from wagtail.models import Site

    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )
