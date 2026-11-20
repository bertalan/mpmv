"""I18N.17: the deferred hreflang and sitemap rules, checked structurally (spec 09 §9.2).

Rules: `x-default` is the default-language URL and only when that page exists; CMS pages
reciprocate (every alternate lists the others and itself); retired products and products of a
suspended vendor are in no language sitemap and in no alternate set; a sample of the URLs the
sitemaps list agrees with the pages' own hreflang. Languages come from `configured_languages()`,
so the suite also passes with `MPMV_LANGUAGES=it,de`; no literal path.
"""

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

import pytest
from django.apps import apps
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from wagtail.models import Locale, Site

from apps.catalog import slugs
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.cms.services import translate_page
from apps.cms.tests.factories import StandardPageFactory
from apps.core.i18n import configured_languages, primary_language
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import other_language

pytestmark = pytest.mark.django_db

NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
LINK = re.compile(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)"')
SAMPLE_SIZE = 20


@pytest.fixture(autouse=True, params=["base", "ride"])
def _setup(monkeypatch: pytest.MonkeyPatch, settings: Any, request: pytest.FixtureRequest) -> None:
    settings.MPMV_THEME_DEFAULT = request.param  # every rule holds under both themes
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )
    cache.clear()
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


def path_of(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def alternates(html: str) -> dict[str, str]:
    """`{hreflang: path}` of a page's head, `x-default` included."""
    return {code: path_of(url) for code, url in LINK.findall(html)}


def sitemap_urls(client: Client, language: str) -> list[str]:
    """Every page path the index lists for `language`, in order, over all its type files."""
    index = ElementTree.fromstring(client.get(reverse("sitemap")).content)  # noqa: S314
    found: list[str] = []
    for loc in index.findall("s:sitemap/s:loc", NS):
        path = path_of(loc.text or "")
        if path.startswith(f"/sitemap-{language}-"):
            body = ElementTree.fromstring(client.get(path).content)  # noqa: S314
            found += [path_of(u.text or "") for u in body.findall("s:url/s:loc", NS)]
    return found


def all_sitemap_urls(client: Client) -> dict[str, list[str]]:
    return {language: sitemap_urls(client, language) for language in configured_languages()}


def product(
    languages: list[str], *, status: str = Product.Status.PUBLISHED, suspended: bool = False
) -> Product:
    vendor = VendorFactory.create(
        status=Vendor.Status.SUSPENDED if suspended else Vendor.Status.ACTIVE
    )
    item = ProductFactory.create(vendor=vendor, status=status)
    for language in languages:
        ProductTranslationFactory.create(
            product=item,
            language=language,
            slug=f"item-{language}-{item.pk}",
            status=TranslationStatus.PUBLISHED,
        )
    return item


def product_path(language: str, item: Product) -> str:
    return slugs.product_path(
        language, f"item-{language}-{item.pk}", default_language=primary_language()
    )


def translated_page(title: str, languages: list[str]) -> dict[str, str]:
    """A StandardPage in the primary language, translated into `languages`: `{lang: path}`."""
    home = Site.objects.get(is_default_site=True).root_page
    page = StandardPageFactory.create(title=title, parent=home)
    page.save_revision().publish()
    if languages:
        translate_page(page, languages)
    found = {primary_language(): path_of(page.url)}
    for language in languages:
        found[language] = path_of(
            page.get_translation(Locale.objects.get(language_code=language)).url
        )
    return found


def walk(urls: dict[str, list[str]]) -> Iterator[tuple[str, str]]:
    for language, paths in urls.items():
        for path in paths:
            yield language, path


# --- x-default


def test_x_default_is_the_default_language_url_when_it_exists(client: Client) -> None:
    item = product(configured_languages())
    for language in configured_languages():
        links = alternates(client.get(product_path(language, item)).content.decode())
        assert links["x-default"] == product_path(primary_language(), item)
        assert links[primary_language()] == links["x-default"]
        assert {code for code in links if code != "x-default"} == set(configured_languages())


def test_there_is_no_x_default_when_the_default_language_page_does_not_exist(
    client: Client,
) -> None:
    other = other_language()
    item = product([other])
    # The default-language URL of this product is a fallback copy: not an alternate, no x-default.
    html = client.get(product_path(other, item)).content.decode()
    links = alternates(html)
    assert "x-default" not in links
    assert set(links) == {other}
    assert primary_language() not in links


def test_a_cms_page_without_a_default_language_version_has_no_x_default(client: Client) -> None:
    other = other_language()
    found = translated_page("Both", [other])
    # Retire the default-language version: only the translation stays live.
    primary_page = (
        Site.objects.get(is_default_site=True)
        .root_page.get_children()
        .get(url_path__endswith="/both/")
    )
    primary_page.unpublish()
    links = alternates(client.get(found[other]).content.decode())
    assert "x-default" not in links
    assert set(links) == {other}


# --- Reciprocity of CMS pages


def test_cms_alternates_reciprocate_and_list_themselves(client: Client) -> None:
    languages = configured_languages()
    found = translated_page("Reciprocal", languages[1:])
    expected = {code: found[code] for code in languages}
    for language, path in found.items():
        links = alternates(client.get(path).content.decode())
        assert {c: p for c, p in links.items() if c != "x-default"} == expected
        assert links[language] == path  # lists itself
        assert links["x-default"] == found[primary_language()]


def test_cms_alternates_are_sitemap_entries_of_their_languages(client: Client) -> None:
    found = translated_page("Listed", configured_languages()[1:])
    listed = all_sitemap_urls(client)
    for language, path in found.items():
        assert path in listed[language]
        for code, other_path in alternates(client.get(path).content.decode()).items():
            if code != "x-default":
                assert other_path in listed[code]


# --- Retired and suspended-vendor products


def test_retired_and_suspended_products_are_in_no_sitemap_and_no_alternate_set(
    client: Client,
) -> None:
    languages = configured_languages()
    live = product(languages)
    retired = product(languages, status=Product.Status.RETIRED)
    suspended = product(languages, suspended=True)
    hidden = {
        product_path(language, item) for item in (retired, suspended) for language in languages
    }
    listed = all_sitemap_urls(client)
    for language in languages:
        assert product_path(language, live) in listed[language]
        assert not hidden & set(listed[language])
    for language in languages:
        html = client.get(product_path(language, live)).content.decode()
        assert not hidden & set(alternates(html).values())
    # A retired page stays live: its alternates are its own translations and no other product.
    own = {product_path(language, retired) for language in languages}
    for language in languages:
        response = client.get(product_path(language, retired))
        assert response.status_code == 200
        assert set(alternates(response.content.decode()).values()) <= own


def test_a_suspended_vendor_product_page_is_not_served(client: Client) -> None:
    item = product(configured_languages(), suspended=True)
    for language in configured_languages():
        assert client.get(product_path(language, item)).status_code == 404


# --- A sample of live URLs


def test_a_sample_of_sitemap_urls_agrees_with_their_own_hreflang(client: Client) -> None:
    languages = configured_languages()
    for _ in range(3):
        product(languages)
    product([other_language()])
    product(languages, status=Product.Status.RETIRED)
    product(languages, suspended=True)
    for index in range(3):
        translated_page(f"Sampled {index}", languages[1:] if index else [])
    listed = all_sitemap_urls(client)
    every = [pair for pair in walk(listed) if pair[1]]
    step = max(1, len(every) // SAMPLE_SIZE)
    sample = every[::step][:SAMPLE_SIZE]
    assert sample
    checked: dict[str, Any] = {}
    for language, path in sample:
        response = client.get(path)
        assert response.status_code == 200, path
        html = response.content.decode()
        links = alternates(html)
        checked[path] = links
        assert links.get(language) == path, path  # a listed URL lists itself
        for code, other_path in links.items():
            if code == "x-default":
                assert other_path == links.get(primary_language()), path
                continue
            assert other_path in listed[code], (path, code)  # every alternate is a sitemap entry
            back = alternates(client.get(other_path).content.decode())
            assert back.get(code) == other_path
            assert {c: p for c, p in back.items() if c != "x-default"} == {
                c: p for c, p in links.items() if c != "x-default"
            }, (path, other_path)  # reciprocity
