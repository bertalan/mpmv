"""Feed discovery (EVT.9 part 2, spec 15 "Discovery"): the RSS and ICS URLs of one scope in one
language, as absolute URLs on the site's base URL, with translated titles. The pages' `<link
rel="alternate">` tags, the Subscribe block and `llms.txt` all read this one function."""

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from django.utils import translation

from apps.core.site_url import absolute_url
from apps.events import engine, feeds
from apps.events.models import EventIndexPage
from apps.vendors.models import Vendor


@dataclass(frozen=True)
class FeedLinks:
    rss_url: str
    ics_url: str
    webcal_url: str  # the ICS URL with the `webcal:` scheme (same path)
    rss_title: str
    ics_title: str


def _webcal(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(("webcal", parts.netloc, parts.path, parts.query, ""))


def feed_links(language: str, *, vendor: str = "", kind: str = "") -> FeedLinks | None:
    """The feeds of `language` for the platform, one vendor (slug) or one event type; a vendor
    wins over a type. None when the language has no events index, or the scope is unknown."""
    index = EventIndexPage.objects.live().filter(locale__language_code=language).first()
    if index is None or not index.url:
        return None
    if kind and kind not in engine.EVENT_TYPES:
        kind = ""
    prefix = index.url
    scope = feeds.Scope()
    if vendor:
        if not Vendor.objects.filter(slug=vendor, status=Vendor.Status.ACTIVE).exists():
            return None
        prefix += f"vendor/{vendor}/"
        scope = feeds.Scope(vendor=vendor)
    elif kind:
        prefix += f"type/{kind}/"
        scope = feeds.Scope(type=kind)
    rss_url = absolute_url(None, prefix + feeds.RSS_PATH)
    ics_url = absolute_url(None, prefix + engine.ICS_PATH)
    with translation.override(language):
        title = feeds.scope_title(scope)
    return FeedLinks(rss_url, ics_url, _webcal(ics_url), title, title)
