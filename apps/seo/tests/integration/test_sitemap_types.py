"""SEO.2: sitemaps per language and per type, valid XML (spec 10, technical SEO).

Done when: valid sitemaps. Every file is well-formed, in the sitemaps.org namespace, lists
absolute `<loc>` URLs on the site root and ISO `<lastmod>` dates; each type has its own file,
listed by the index; no fallback copy, noindex page or hidden event is in any of them.
"""

import datetime as dt
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

import pytest
from django.apps import apps
from django.conf import settings
from django.test import Client
from django.urls import reverse
from wagtail.models import Site

from apps.catalog import slugs
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
)
from apps.cms.tests.factories import StandardPageFactory, TermLandingPageFactory
from apps.core.i18n import configured_languages
from apps.core.models import TranslationStatus
from apps.events.models import EventIndexPage
from apps.events.tests.factories import EventPageFactory, primary_index
from apps.seo import sitemap_engine
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED
PRIMARY = settings.LANGUAGE_CODE
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
KINDS = ("pages", "events", "products", "listings", "vendors")


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


def section_url(kind: str, language: str = PRIMARY) -> str:
    return reverse("sitemap_section", kwargs={"language": language, "kind": kind})


def parse(client: Client, path: str) -> ElementTree.Element:
    response = client.get(path)
    assert response.status_code == 200, path
    assert response["Content-Type"].startswith("application/xml")
    return ElementTree.fromstring(response.content)  # noqa: S314 (parses our own generated XML)


def locs(client: Client, kind: str, language: str = PRIMARY) -> set[str]:
    root = parse(client, section_url(kind, language))
    return {urlsplit(loc.text or "").path for loc in root.findall("s:url/s:loc", NS)}


def published_product(**kwargs: object) -> tuple[Product, str]:
    kwargs.setdefault("vendor", VendorFactory.create(status=Vendor.Status.ACTIVE))
    item = ProductFactory.create(status=Product.Status.PUBLISHED, **kwargs)
    ProductTranslationFactory.create(
        product=item, language=PRIMARY, slug=f"item-{item.pk}", status=PUBLISHED
    )
    return item, slugs.product_path(PRIMARY, f"item-{item.pk}", default_language=PRIMARY)


def test_each_type_has_its_own_file_and_the_index_lists_them(client: Client) -> None:
    _item, product_path = published_product()
    page = StandardPageFactory.create()
    page.save_revision().publish()
    index = parse(client, reverse("sitemap"))
    assert index.tag == f"{{{NS['s']}}}sitemapindex"
    listed = [urlsplit(loc.text or "").path for loc in index.findall("s:sitemap/s:loc", NS)]
    assert section_url("products") in listed
    assert section_url("pages") in listed
    assert section_url("vendors") in listed
    assert product_path in locs(client, "products")
    assert product_path not in locs(client, "pages")
    assert urlsplit(page.get_url()).path in locs(client, "pages")
    assert urlsplit(page.get_url()).path not in locs(client, "products")


def test_the_index_lists_only_types_that_have_urls(client: Client) -> None:
    StandardPageFactory.create().save_revision().publish()
    listed = [
        urlsplit(loc.text or "").path
        for loc in parse(client, reverse("sitemap")).findall("s:sitemap/s:loc", NS)
    ]
    assert section_url("pages") in listed
    assert section_url("events") not in listed
    assert section_url("products") not in listed


def test_a_listed_file_is_valid_sitemap_xml(client: Client) -> None:
    published_product()
    root = settings.WAGTAILADMIN_BASE_URL.rstrip("/")
    index = parse(client, reverse("sitemap"))
    for loc in index.findall("s:sitemap/s:loc", NS):
        assert (loc.text or "").startswith(root + "/sitemap-")
    urlset = parse(client, section_url("products"))
    assert urlset.tag == f"{{{NS['s']}}}urlset"
    rows = urlset.findall("s:url", NS)
    assert rows
    for row in rows:
        url = row.find("s:loc", NS)
        assert url is not None
        assert (url.text or "").startswith(root + "/")
        lastmod = row.find("s:lastmod", NS)
        assert lastmod is not None
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", lastmod.text or "")
        dt.date.fromisoformat(lastmod.text or "")


def test_the_xml_declaration_and_encoding(client: Client) -> None:
    body = client.get(reverse("sitemap")).content.decode()
    assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>')


def test_categories_and_storefronts_go_to_their_own_files(client: Client) -> None:
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=category, language=PRIMARY, status=PUBLISHED, slug="cat-types"
    )
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    published_product(category=category, vendor=vendor)
    category_path = slugs.category_path(PRIMARY, ["cat-types"], default_language=PRIMARY)
    vendor_path = slugs.vendor_path(PRIMARY, vendor.slug, default_language=PRIMARY)
    assert category_path in locs(client, "listings")
    assert vendor_path in locs(client, "vendors")
    assert vendor_path not in locs(client, "listings")
    assert category_path not in locs(client, "vendors")


def test_events_have_their_own_file_without_hidden_ones(client: Client) -> None:
    index = EventIndexPage.objects.first() or primary_index()
    shown = EventPageFactory.create(parent=index, title="Open fair")
    shown.save_revision().publish()
    hidden = EventPageFactory.create(parent=index, title="Closed fair")
    hidden.save_revision().publish()
    from apps.events.models import EventPage

    EventPage.objects.filter(pk=hidden.pk).update(hidden_at=dt.datetime.now(dt.UTC))
    listed = locs(client, "events")
    assert urlsplit(shown.url).path in listed
    assert all("closed-fair" not in path for path in listed)
    assert urlsplit(shown.url).path not in locs(client, "pages")


def test_a_noindex_landing_page_is_in_no_file(client: Client) -> None:
    landing = TermLandingPageFactory.create()
    landing.save_revision().publish()
    StandardPageFactory.create().save_revision().publish()
    everything: set[str] = set()
    for kind in KINDS:
        response = client.get(section_url(kind))
        if response.status_code == 200:
            root = ElementTree.fromstring(response.content)  # noqa: S314 (own XML)
            everything |= {urlsplit(loc.text or "").path for loc in root.findall("s:url/s:loc", NS)}
    assert urlsplit(landing.get_url()).path not in everything


def test_a_fallback_copy_is_in_no_other_language_file(client: Client) -> None:
    other = next(code for code in configured_languages() if code != PRIMARY)
    published_product()
    assert locs(client, "products", other) == set()


def test_a_type_is_split_into_its_own_numbered_parts(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    for _ in range(5):
        published_product()
    monkeypatch.setattr(sitemap_engine, "MAX_URLS", 2)
    listed = [
        urlsplit(loc.text or "").path
        for loc in parse(client, reverse("sitemap")).findall("s:sitemap/s:loc", NS)
    ]
    parts = [
        reverse("sitemap_part", kwargs={"language": PRIMARY, "kind": "products", "part": str(n)})
        for n in range(1, 4)
    ]
    assert set(parts) <= set(listed)
    assert section_url("products") not in listed
    assert client.get(section_url("products")).status_code == 404
    total = sum(len(parse(client, part).findall("s:url", NS)) for part in parts)
    assert total >= 5
    assert client.get(parts[0].replace("-1.xml", "-9.xml")).status_code == 404
