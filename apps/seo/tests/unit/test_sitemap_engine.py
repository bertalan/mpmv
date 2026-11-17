"""I18N.6: the pure side of the per-language sitemaps (no database)."""

import datetime as dt
from xml.etree import ElementTree

import pytest

from apps.seo import sitemap_engine as engine

pytestmark = pytest.mark.unit

NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def test_a_language_lists_a_page_only_when_it_has_its_own_published_text() -> None:
    paths = {"en": "/a/", "it": "/it/a/", "de": "/de/a/"}
    published = ("en", "it")
    assert engine.sitemap_path(paths, published, "it") == "/it/a/"
    assert engine.sitemap_path(paths, published, "en") == "/a/"


def test_a_fallback_copy_is_not_listed() -> None:
    """German shows the source text on a /de/ path (`paths` has it) but has no published text."""
    paths = {"en": "/a/", "de": "/de/a/"}
    assert engine.sitemap_path(paths, ("en",), "de") is None


def test_a_published_language_without_a_path_is_not_listed() -> None:
    assert engine.sitemap_path({"en": "/a/"}, ("en", "it"), "it") is None


def test_the_urlset_lists_locations_and_dates_escaped() -> None:
    entries = [
        engine.Entry("https://shop.test/a/?x=1&y=2", dt.date(2027, 1, 5)),
        engine.Entry("https://shop.test/b/", None),
    ]
    root = ElementTree.fromstring(engine.urlset_xml(entries))  # noqa: S314 (parses our own generated XML)
    urls = root.findall("s:url", NS)
    assert [u.findtext("s:loc", namespaces=NS) for u in urls] == [e.loc for e in entries]
    assert urls[0].findtext("s:lastmod", namespaces=NS) == "2027-01-05"
    assert urls[1].find("s:lastmod", NS) is None


def test_an_empty_urlset_is_valid_xml() -> None:
    root = ElementTree.fromstring(engine.urlset_xml([]))  # noqa: S314 (parses our own generated XML)
    assert root.findall("s:url", NS) == []


def test_the_index_lists_one_sitemap_per_location() -> None:
    xml = engine.index_xml(["https://shop.test/s-en.xml", "https://shop.test/s-it.xml"])
    root = ElementTree.fromstring(xml)  # noqa: S314 (parses our own generated XML)
    assert [e.findtext("s:loc", namespaces=NS) for e in root.findall("s:sitemap", NS)] == [
        "https://shop.test/s-en.xml",
        "https://shop.test/s-it.xml",
    ]
