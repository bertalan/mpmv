"""Template tags of the events app (EVT.7)."""

from typing import Any

from django import template
from django.utils import timezone
from wagtail.models import Locale

from apps.events import listing, services
from apps.events.models import EventIndexPage

register = template.Library()

STOREFRONT_EVENTS = 3


@register.inclusion_tag("events/_storefront.html")
def storefront_events(vendor: Any, language: str) -> dict[str, Any]:
    """The vendor's next three public events in `language` (EVT.8 rule: the translation, else
    the original-language page marked as such) and the link to the vendor's events. Empty when
    there is no vendor, no events locale or nothing upcoming, so the section is then omitted.
    Read at render time on every request: a hidden, cancelled or newly published event shows
    on the next response."""
    locale = Locale.objects.filter(language_code=language).first()
    if vendor is None or locale is None:
        return {"items": []}
    index = EventIndexPage.objects.live().filter(locale=locale).first()
    events = services.events_for_locale(
        locale,
        listing.Filters(vendor=vendor.slug),
        site=index.get_site() if index is not None else None,
    )
    items = listing.upcoming(events, timezone.now())[:STOREFRONT_EVENTS]
    link = f"{index.url}?vendor={vendor.slug}" if index is not None and index.url else ""
    return {"items": items, "events_url": link, "vendor": vendor}


@register.inclusion_tag("events/_fitment_badge.html", takes_context=True)
def event_fitment_badge(context: template.Context, page: Any) -> dict[str, Any]:
    """The "For <profile> owners" badge when the active garage matches a tag of the event (FIT.11).
    Empty without a request, on error pages and for a general event or a generic visitor."""
    from apps.events import fitment
    from apps.fitment.garage_state import garage_state

    request = context.get("request")
    if request is None or "exception" in context or not fitment.matches_garage(request, page):
        return {}
    return {"label": garage_state(request)["active_label"]}
