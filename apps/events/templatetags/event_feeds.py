"""Template tags of the event feeds (EVT.9 part 2): the `<link rel="alternate">` tags and the
Subscribe block. They take the scope as arguments so that any app's template (the vendor
storefront too) can use them without importing events code."""

from typing import Any

from django import template
from django.utils import translation

from apps.events import discovery

register = template.Library()


def _links(vendor: str, kind: str) -> dict[str, Any]:
    language = translation.get_language() or ""
    return {"links": discovery.feed_links(language.split("-")[0], vendor=vendor, kind=kind)}


@register.inclusion_tag("events/_feed_alternates.html")
def event_feed_alternates(vendor: str = "", kind: str = "") -> dict[str, Any]:
    """`<link rel="alternate">` for the RSS and the ICS feed of the scope. A vendor and a type
    together mean the platform feeds (no feed has both)."""
    if vendor and kind:
        vendor = kind = ""
    return _links(vendor, kind)


@register.inclusion_tag("events/_subscribe.html")
def event_subscribe(vendor: str = "", kind: str = "") -> dict[str, Any]:
    """The visible Subscribe block: RSS, the ICS download, `webcal://` and a link to copy."""
    if vendor and kind:
        vendor = kind = ""
    return _links(vendor, kind)
