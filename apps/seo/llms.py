"""`llms.txt` (SEO.2): the site summary and the main public sections of one language, from the
same data as the sitemaps (so the same exclusions: no fallback copy, no noindex page, no hidden
event, no inactive vendor)."""

from django.http import HttpRequest
from wagtail.models import Locale, Site

from apps.events import discovery

from . import llms_engine, sitemaps
from .sitemap_engine import Entry

SECTIONS = (
    ("Pages", "pages"),
    ("Events", "events"),
    ("Categories, terms and brands", "listings"),
    ("Shops", "vendors"),
    ("Products", "products"),
)


def _site_summary(site: Site | None, language: str) -> tuple[str, str]:
    """The site name and the description of the home page in `language` (its search
    description), when the home page has a text in that language."""
    if site is None:
        return "", ""
    root = site.root_page
    locale = Locale.objects.filter(language_code=language).first()
    home = root.get_translation_or_none(locale) if locale is not None else None
    if home is None and root.locale.language_code == language:
        home = root
    summary = home.search_description if home is not None else ""
    return site.site_name or site.hostname, summary


def _feed_entries(language: str) -> list[Entry]:
    """The event feeds of the language (EVT.9): RSS and ICS, on the site's base URL."""
    links = discovery.feed_links(language)
    if links is None:
        return []
    return [
        Entry(links.rss_url, title=f"{links.rss_title} (RSS)"),
        Entry(links.ics_url, title=f"{links.ics_title} (ICS)"),
    ]


def llms_txt(language: str, request: HttpRequest) -> str:
    site = Site.find_for_request(request)
    title, summary = _site_summary(site, language)
    sections = [
        (heading, list(sitemaps.kind_entries(kind, language, request, titles=True)))
        for heading, kind in SECTIONS
    ]
    sections.append(("Event feeds", _feed_entries(language)))
    return llms_engine.llms_text(title or "Marketplace", summary, sections)
