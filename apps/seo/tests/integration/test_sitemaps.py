"""I18N.6: hreflang, canonical and per-language sitemaps without fallback copies (spec 09 §9.2).

Done when: on a sample of URLs, fallback copies have the primary-language canonical and are
absent from hreflang and from the language sitemap. Never a literal path: the suite also runs
with `MPMV_LANGUAGES=it,de`, where the primary language is not English and URLs carry prefixes.
"""

import datetime as dt
import re
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

import pytest
from django.apps import apps
from django.conf import settings
from django.test import Client, override_settings
from django.urls import reverse
from wagtail.models import Locale, Site

from apps.catalog import slugs
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
)
from apps.cms.services import translate_page
from apps.cms.tests.factories import StandardPageFactory
from apps.core.i18n import configured_languages
from apps.core.models import TranslationStatus
from apps.events.models import EventIndexPage, EventPage
from apps.events.tests.factories import EventPageFactory, primary_index
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED
PRIMARY = settings.LANGUAGE_CODE
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


@pytest.fixture(autouse=True)
def _indexing_on() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


def other_language() -> str:
    return next(code for code in configured_languages() if code != PRIMARY)


def sitemap_paths(client: Client, language: str) -> set[str]:
    response = client.get(reverse("sitemap_language", kwargs={"language": language}))
    assert response.status_code == 200
    root = ElementTree.fromstring(response.content)  # noqa: S314
    return {urlsplit(loc.text or "").path for loc in root.findall("s:url/s:loc", NS)}


def head_links(html: str) -> tuple[list[str], set[str]]:
    """The canonical paths and the hreflang languages (without x-default) of a page."""
    canonical = [
        urlsplit(url).path for url in re.findall(r'<link rel="canonical" href="([^"]+)"', html)
    ]
    codes = re.findall(r'<link rel="alternate" hreflang="([^"]+)"', html)
    return canonical, {code for code in codes if code != "x-default"}


def alternate_paths(html: str) -> set[str]:
    found = re.findall(r'<link rel="alternate" hreflang="(?!x-default)[^"]+" href="([^"]+)"', html)
    return {urlsplit(url).path for url in found}


def product(languages: list[str], **kwargs: Any) -> Product:
    kwargs.setdefault("vendor", VendorFactory.create(status=Vendor.Status.ACTIVE))
    kwargs.setdefault("status", Product.Status.PUBLISHED)
    item = ProductFactory.create(**kwargs)
    for language in languages:
        ProductTranslationFactory.create(
            product=item, language=language, slug=f"item-{language}-{item.pk}", status=PUBLISHED
        )
    return item


def product_path(language: str, item: Product, *, text: str | None = None) -> str:
    """The page in `language`, carrying the slug of the text in `text` (default: its own)."""
    slug = f"item-{text or language}-{item.pk}"
    return slugs.product_path(language, slug, default_language=PRIMARY)


def event(title: str) -> EventPage:
    index = EventIndexPage.objects.filter(locale=Locale.get_default()).first() or primary_index()
    page: EventPage = EventPageFactory.create(parent=index, title=title)
    page.save_revision().publish()
    return page


# --- The index and robots.txt


def test_the_index_lists_one_sitemap_per_language(client: Client) -> None:
    response = client.get(reverse("sitemap"))
    assert response.status_code == 200
    root = ElementTree.fromstring(response.content)  # noqa: S314
    found = [urlsplit(loc.text or "").path for loc in root.findall("s:sitemap/s:loc", NS)]
    assert found == [
        reverse("sitemap_language", kwargs={"language": code}) for code in configured_languages()
    ]


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_robots_txt_lists_the_sitemap_index(client: Client) -> None:
    text = client.get("/robots.txt").content.decode()
    assert text.endswith(f"Sitemap: http://testserver{reverse('sitemap')}\n")


def test_an_unknown_language_has_no_sitemap(client: Client) -> None:
    assert client.get(reverse("sitemap_language", kwargs={"language": "xx"})).status_code == 404


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_with_indexing_off_there_is_no_sitemap(client: Client) -> None:
    apps.get_model("seo", "SeoSettings").objects.update(allow_indexing=False)
    assert client.get(reverse("sitemap")).status_code == 404
    assert client.get(reverse("sitemap_language", kwargs={"language": PRIMARY})).status_code == 404


@override_settings(FORCE_NOINDEX=True)
def test_a_forced_noindex_environment_has_no_sitemap(client: Client) -> None:
    assert client.get(reverse("sitemap")).status_code == 404


# --- Products


def test_a_fallback_product_copy_has_the_primary_canonical_and_is_not_listed(
    client: Client,
) -> None:
    other = other_language()
    only_primary = product([PRIMARY])
    fallback_path = product_path(other, only_primary, text=PRIMARY)
    response = client.get(fallback_path)
    assert response.status_code == 200
    html = response.content.decode()
    canonical, hreflang = head_links(html)
    assert canonical == [product_path(PRIMARY, only_primary)]
    assert hreflang == {PRIMARY}
    assert "noindex" not in html
    assert fallback_path not in sitemap_paths(client, other)
    assert product_path(PRIMARY, only_primary) in sitemap_paths(client, PRIMARY)


def test_a_translated_product_is_in_both_sitemaps_with_both_alternates(client: Client) -> None:
    other = other_language()
    item = product([PRIMARY, other])
    assert product_path(PRIMARY, item) in sitemap_paths(client, PRIMARY)
    assert product_path(other, item) in sitemap_paths(client, other)
    canonical, hreflang = head_links(client.get(product_path(other, item)).content.decode())
    assert canonical == [product_path(other, item)]
    assert hreflang == {PRIMARY, other}


def test_drafts_and_inactive_vendors_are_not_listed(client: Client) -> None:
    draft = product([PRIMARY], status=Product.Status.DRAFT)
    inactive = product([PRIMARY], vendor=VendorFactory.create(status=Vendor.Status.PENDING))
    listed = sitemap_paths(client, PRIMARY)
    assert product_path(PRIMARY, draft) not in listed
    assert product_path(PRIMARY, inactive) not in listed


# --- Listings and storefronts


def test_a_category_published_in_the_primary_language_only_is_not_in_the_other_sitemap(
    client: Client,
) -> None:
    other = other_language()
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=category, language=PRIMARY, status=PUBLISHED, slug="cat-only"
    )
    product([PRIMARY], category=category)
    primary_path = slugs.category_path(PRIMARY, ["cat-only"], default_language=PRIMARY)
    fallback_path = slugs.category_path(other, ["cat-only"], default_language=PRIMARY)
    assert primary_path in sitemap_paths(client, PRIMARY)
    assert fallback_path not in sitemap_paths(client, other)
    canonical, hreflang = head_links(client.get(fallback_path).content.decode())
    assert canonical == [primary_path]
    assert hreflang == {PRIMARY}


def test_an_active_vendor_storefront_is_listed_in_its_published_language_only(
    client: Client,
) -> None:
    other = other_language()
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    product([PRIMARY], vendor=vendor)
    primary_path = slugs.vendor_path(PRIMARY, vendor.slug, default_language=PRIMARY)
    other_path = slugs.vendor_path(other, vendor.slug, default_language=PRIMARY)
    assert primary_path in sitemap_paths(client, PRIMARY)
    assert other_path not in sitemap_paths(client, other)


# --- CMS pages


def test_a_cms_page_has_a_canonical_and_the_hreflang_of_its_translations(client: Client) -> None:
    other = other_language()
    home = Site.objects.get(is_default_site=True).root_page
    page = StandardPageFactory.create(title="Both languages", parent=home)
    page.save_revision().publish()
    translate_page(page, [other])
    translated = page.get_translation(Locale.objects.get(language_code=other))
    html = client.get(page.url).content.decode()
    canonical, hreflang = head_links(html)
    assert canonical == [urlsplit(page.url).path]
    assert hreflang == {PRIMARY, other}
    assert alternate_paths(html) == {urlsplit(page.url).path, urlsplit(translated.url).path}
    assert urlsplit(page.url).path in sitemap_paths(client, PRIMARY)
    assert urlsplit(translated.url).path in sitemap_paths(client, other)


def test_a_cms_page_in_the_primary_language_only_is_in_no_other_sitemap(client: Client) -> None:
    home = Site.objects.get(is_default_site=True).root_page
    page = StandardPageFactory.create(title="Only primary", parent=home)
    page.save_revision().publish()
    canonical, hreflang = head_links(client.get(page.url).content.decode())
    assert canonical == [urlsplit(page.url).path]
    assert hreflang == {PRIMARY}
    assert urlsplit(page.url).path in sitemap_paths(client, PRIMARY)
    assert all("only-primary" not in path for path in sitemap_paths(client, other_language()))


def test_a_draft_cms_page_is_not_listed(client: Client) -> None:
    home = Site.objects.get(is_default_site=True).root_page
    StandardPageFactory.create(title="Unfinished", parent=home, live=False)
    assert all("unfinished" not in path for path in sitemap_paths(client, PRIMARY))


# --- Events: the stricter rule, no fallback copies


def test_an_event_is_listed_only_in_the_languages_it_exists_in(client: Client) -> None:
    other = other_language()
    solo = event("Solo fair")
    shared = event("Shared fair")
    translate_page(shared, [other])
    shared_other = shared.get_translation(Locale.objects.get(language_code=other))
    primary_listed = sitemap_paths(client, PRIMARY)
    other_listed = sitemap_paths(client, other)
    assert {urlsplit(solo.url).path, urlsplit(shared.url).path} <= primary_listed
    assert urlsplit(shared_other.url).path in other_listed
    assert all("solo-fair" not in path for path in other_listed)
    _canonical, hreflang = head_links(client.get(solo.url).content.decode())
    assert hreflang == {PRIMARY}


def test_a_hidden_event_is_not_listed(client: Client) -> None:
    page = event("Secret fair")
    EventPage.objects.filter(pk=page.pk).update(hidden_at=dt.datetime.now(dt.UTC))
    assert all("secret-fair" not in path for path in sitemap_paths(client, PRIMARY))
