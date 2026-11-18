"""Template tags of the event feeds (EVT.9 part 2): the `<link rel="alternate">` tags and the
Subscribe block. They take the scope as arguments so that any app's template (the vendor
storefront too) can use them without importing events code."""

from typing import Any

from django import template
from django.utils import translation

from apps.events import discovery

register = template.Library()


def _links(context: template.Context, vendor: str, kind: str) -> dict[str, Any]:
    """The scope's feed links, looked up once per request: a page with the alternates and the
    Subscribe block asks twice (#640 review)."""
    language = (translation.get_language() or "").split("-")[0]
    request = context.get("request")
    cache: dict[tuple[str, str, str], Any] | None = (
        request.__dict__.setdefault("_event_feed_links", {}) if request is not None else None
    )
    key = (language, vendor, kind)
    if cache is not None and key in cache:
        return {"links": cache[key]}
    links = discovery.feed_links(language, vendor=vendor, kind=kind)
    if cache is not None:
        cache[key] = links
    return {"links": links}


@register.inclusion_tag("events/_feed_alternates.html", takes_context=True)
def event_feed_alternates(
    context: template.Context, vendor: str = "", kind: str = ""
) -> dict[str, Any]:
    """`<link rel="alternate">` for the RSS and the ICS feed of the scope. A vendor and a type
    together mean the platform feeds (no feed has both)."""
    if vendor and kind:
        vendor = kind = ""
    return _links(context, vendor, kind)


@register.inclusion_tag("events/_subscribe.html", takes_context=True)
def event_subscribe(context: template.Context, vendor: str = "", kind: str = "") -> dict[str, Any]:
    """The visible Subscribe block: RSS, the ICS download, `webcal://` and a link to copy."""
    if vendor and kind:
        vendor = kind = ""
    return _links(context, vendor, kind)
