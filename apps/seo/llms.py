"""`llms.txt` (SEO.2): the site summary and the main public sections of one language, from the
same data as the sitemaps (so the same exclusions: no fallback copy, no noindex page, no hidden
event, no inactive vendor)."""

from django.http import HttpRequest
from wagtail.models import Locale, Site

from . import llms_engine, sitemaps

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


def llms_txt(language: str, request: HttpRequest) -> str:
    site = Site.find_for_request(request)
    title, summary = _site_summary(site, language)
    sections = [
        (heading, list(sitemaps.kind_entries(kind, language, request)))
        for heading, kind in SECTIONS
    ]
    return llms_engine.llms_text(title or "Marketplace", summary, sections)
