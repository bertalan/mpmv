"""Event feeds per language (EVT.9, spec 15 "Feeds and calendars"): RSS 2.0 and ICS for the
platform, a vendor or an event type, and the ICS of one event.

The serializers work from an allow list of fields (title, summary, dates, type, vendor business
name, venue, address and coordinates, event URL): never the author, a member name, an email
address, a phone number or the body. The ICS text is built by the pure serializer in `engine`;
the RSS by Django's `Rss201rev2Feed`. Every response carries a strong `ETag`, `Last-Modified`
and `Cache-Control` (304 on a match), and every request counts towards a per-IP, per-path limit.
"""

import datetime as dt
import hashlib
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.db.models import Max
from django.http import Http404, HttpRequest, HttpResponse
from django.utils import timezone, translation
from django.utils.cache import get_conditional_response
from django.utils.feedgenerator import Rss201rev2Feed
from django.utils.http import http_date, quote_etag
from django.utils.translation import gettext as _

from apps.core.http import client_ip
from apps.core.services import consume_rate_limit
from apps.core.site_url import absolute_url
from apps.events import engine, services
from apps.vendors.models import Vendor
from apps.vendors.seller_name import public_name

if TYPE_CHECKING:
    from apps.events.models import EventIndexPage, EventPage

logger = logging.getLogger(__name__)

MAX_ITEMS = 500
RATE_LIMIT = 30  # requests per window, per IP and feed path (CORE.5 limiter)
RATE_WINDOW = dt.timedelta(minutes=1)
CACHE_CONTROL = "public, max-age=300, s-maxage=60"
RSS_PATH = "feed.rss"
GEORSS_NS = "http://www.georss.org/georss"
DC_NS = "http://purl.org/dc/elements/1.1/"


@dataclass(frozen=True)
class Scope:
    """What a feed covers: the whole platform (both empty), one vendor or one event type. It is
    the `LocaleFilters` of `events_for_locale`."""

    vendor: str = ""
    type: str = ""
    translated_only: bool = False


def _vendor_for(scope: Scope) -> Vendor | None:
    if not scope.vendor:
        return None
    vendor = Vendor.objects.filter(slug=scope.vendor, status=Vendor.Status.ACTIVE).first()
    if vendor is None:
        raise Http404
    return vendor


def _check_scope(scope: Scope) -> None:
    if scope.type and scope.type not in engine.EVENT_TYPES:
        raise Http404


def feed_events(
    index: "EventIndexPage", scope: Scope, now: dt.datetime
) -> list[services.LocalisedEvent]:
    """The public events of the index's language in `scope`, soonest first, at most 500: not
    ended more than 30 days ago, and a cancelled one only for the retention window."""
    from apps.events.models import EventSettings

    site = index.get_site()
    retention = dt.timedelta(
        days=EventSettings.current(site).cancelled_retention_days if site is not None else 30
    )
    kept = []
    for found in services.events_for_locale(index.locale, scope, site=site):
        page = found.page
        if page.cancelled_at is not None:
            if now - page.cancelled_at > retention:
                continue
        elif engine.is_noindex(page.recurrence_spec(), cancelled=False, now=now):
            continue
        kept.append(found)
    kept.sort(key=lambda found: (found.page.sort_key(), found.page.pk))
    return kept[:MAX_ITEMS]


def last_modified(scope: Scope) -> dt.datetime | None:
    """The latest content change, hide or cancellation of any event in the scope (hidden ones
    included), so a hide moves it."""
    from apps.events.models import EventPage

    pages = EventPage.objects.all()
    if scope.vendor:
        pages = pages.filter(vendor__slug=scope.vendor)
    if scope.type:
        pages = pages.filter(event_type=scope.type)
    found = pages.aggregate(
        changed=Max("content_changed_at"), hidden=Max("hidden_at"), cancelled=Max("cancelled_at")
    )
    moments: list[dt.datetime] = [m for m in found.values() if m is not None]
    if not moments:  # events published before `content_changed_at` existed
        published: dt.datetime | None = pages.aggregate(p=Max("last_published_at"))["p"]
        return published
    return max(moments)


# --- Responses


def _too_many(wait: dt.timedelta) -> HttpResponse:
    retry = str(max(1, math.ceil(wait.total_seconds())))
    return HttpResponse(
        "Too many requests.",
        status=429,
        headers={"Retry-After": retry, "Cache-Control": "no-store"},
    )


def throttled(request: HttpRequest) -> HttpResponse | None:
    """A 429 with `Retry-After` when this IP asked for this feed path too often (304s count).

    Call it only after the scope and the vendor are validated, so a 404 never writes a row. The
    key holds a hash of the path, so a long path cannot overflow `Throttle.key`. Without a client
    address (no `REMOTE_ADDR`: not a real HTTP request) it does not throttle and logs a warning:
    one shared bucket would let a single client lock everybody out of the feeds.
    """
    ip = client_ip(request)
    if ip is None:
        logger.warning("Feed request without a client address: not rate limited")
        return None
    digest = hashlib.sha256(request.path.encode()).hexdigest()[:32]
    wait = consume_rate_limit(f"events.feed:{ip}:{digest}", limit=RATE_LIMIT, window=RATE_WINDOW)
    return _too_many(wait) if wait else None


def respond(
    request: HttpRequest,
    body: str,
    content_type: str,
    modified: dt.datetime | None,
    *,
    filename: str = "",
) -> HttpResponse:
    """The body with a strong ETag, `Last-Modified` and `Cache-Control`, or a 304 on the ETag."""
    response = HttpResponse(body, content_type=content_type)
    etag = quote_etag(hashlib.sha256(body.encode()).hexdigest()[:32])
    response["ETag"] = etag
    response["Cache-Control"] = CACHE_CONTROL
    if modified is not None:
        response["Last-Modified"] = http_date(modified.timestamp())
    if filename:
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
    # `Last-Modified` is informational: it can go backwards (an un-hide clears `hidden_at`) or
    # stay stale (events age out of the window), so `If-Modified-Since` is not honoured and the
    # strong ETag, a hash of the body, decides.
    conditional = get_conditional_response(request, etag=etag, response=response)
    return conditional if conditional is not None else response


# --- ICS


def _location(page: "EventPage") -> str:
    town = " ".join(part for part in (page.postcode, page.city) if part)
    parts = (page.venue_name, page.street, town, page.country)
    return ", ".join(part for part in parts if part)


def ics_event(
    page: "EventPage", request: HttpRequest, *, language: str = "", name: str = ""
) -> engine.IcsEvent:
    """One event as the ICS allow list shows it; `language` marks an original-language fallback."""
    site = page.get_site()
    host = site.hostname if site is not None else request.get_host()
    return engine.IcsEvent(
        uid=f"{page.translation_key}@{host}",
        stamp=page.content_changed_at or page.last_published_at or timezone.now(),
        created=page.first_published_at,
        sequence=page.sequence,
        summary=page.title,
        description=page.summary,
        location=_location(page),
        url=absolute_url(request, page.url or ""),
        category=str(page.get_event_type_display()),
        span=engine.first_span(page.recurrence_spec()),
        cancelled=page.cancelled_at is not None,
        calendar_name=name or _("Events"),
        rrule=page.rrule,
        exdates=tuple(page.exdates),
        exdate_days=tuple(page.exdate_days),
        latitude=str(page.latitude) if page.latitude is not None else "",
        longitude=str(page.longitude) if page.longitude is not None else "",
        language=language,
    )


def event_ics_response(page: "EventPage", request: HttpRequest) -> HttpResponse:
    """The single-event ICS download."""
    blocked = throttled(request)
    if blocked is not None:
        return blocked
    modified = page.content_changed_at or page.last_published_at
    return respond(
        request,
        engine.event_ics(ics_event(page, request)),
        "text/calendar; charset=utf-8",
        modified,
        filename=f"{page.slug}.ics",
    )


def _calendar_name(vendor: Vendor | None, scope: Scope) -> str:
    return _feed_title(vendor, scope)


def _feed_title(vendor: Vendor | None, scope: Scope) -> str:
    shown = public_name(vendor) if vendor is not None else None
    if shown:
        return _("Events of %(vendor)s") % {"vendor": shown}
    if scope.type:
        label = dict(_type_labels()).get(scope.type, scope.type)
        return _("Events: %(type)s") % {"type": label}
    return _("Events")


def scope_title(scope: Scope) -> str:
    """The translated title of the feed of `scope` (the active language), for discovery links."""
    return _feed_title(_vendor_for(scope), scope)


def _type_labels() -> list[tuple[str, str]]:
    from apps.events.models import EventType

    return [(str(value), str(label)) for value, label in EventType.choices]


def index_ics_response(index: "EventIndexPage", request: HttpRequest, scope: Scope) -> HttpResponse:
    _check_scope(scope)
    vendor = _vendor_for(scope)
    blocked = throttled(request)
    if blocked is not None:
        return blocked
    with translation.override(index.locale.language_code):
        name = _calendar_name(vendor, scope)
        events = [
            ics_event(
                found.page,
                request,
                language=found.language if found.fallback else "",
                name=name,
            )
            for found in feed_events(index, scope, timezone.now())
        ]
        body = engine.calendar_ics(events, name)
    return respond(request, body, "text/calendar; charset=utf-8", last_modified(scope))


# --- RSS


class EventRssFeed(Rss201rev2Feed):
    """RSS 2.0 with the GeoRSS Simple and Dublin Core namespaces."""

    def rss_attributes(self) -> dict[str, str]:
        attrs: dict[str, str] = super().rss_attributes()
        attrs["xmlns:georss"] = GEORSS_NS
        attrs["xmlns:dc"] = DC_NS
        return attrs

    def latest_post_date(self) -> dt.datetime:
        """`lastBuildDate` from the items only (Django uses `now` for an empty feed), so the body
        stays the same between changes and the ETag holds."""
        dates = [item["pubdate"] for item in self.items if item.get("pubdate")]
        return max(dates) if dates else dt.datetime(1970, 1, 1, tzinfo=dt.UTC)

    def add_item_elements(self, handler: Any, item: dict[str, Any]) -> None:
        super().add_item_elements(handler, item)
        if item.get("language"):
            handler.addQuickElement("dc:language", item["language"])
        if item.get("georss_point"):
            handler.addQuickElement("georss:point", item["georss_point"])


def _when(span: engine.Span) -> str:
    from django.utils.formats import date_format

    zone = span.start.tzinfo
    if span.all_day:
        last = span.end - dt.timedelta(days=1)
        first_text = date_format(span.start.date(), "DATE_FORMAT")
        last_text = date_format(last.date(), "DATE_FORMAT")
        return first_text if first_text == last_text else f"{first_text} - {last_text}"
    start_text = date_format(span.start, "DATETIME_FORMAT")
    return f"{start_text} ({getattr(zone, 'key', 'UTC')})"


def storefront_url(vendor: Vendor, language: str) -> str:
    """The absolute URL of the vendor's public storefront in `language`; "" when it has none."""
    from apps.catalog import slugs
    from apps.core.i18n import primary_language
    from apps.vendors.services import has_public_storefront

    if not has_public_storefront(vendor):
        return ""
    path = slugs.vendor_path(language, vendor.slug, default_language=primary_language())
    return absolute_url(None, path)


def _description(found: services.LocalisedEvent, now: dt.datetime) -> str:
    page = found.page
    spec = page.recurrence_spec()
    spans = engine.event_spans(spec, now, engine.horizon_end(now))
    span = spans[0] if spans else engine.first_span(spec)
    parts = [page.summary, _("When: %(when)s") % {"when": _when(span)}]
    if page.vendor is not None:
        shown = public_name(page.vendor)
        if shown:
            parts.append(_("By: %(vendor)s") % {"vendor": shown})
        shop = storefront_url(
            page.vendor, found.language if found.fallback else page.locale.language_code
        )
        if shop:
            parts.append(_("Shop: %(url)s") % {"url": shop})
    where = _location(page)
    if where:
        parts.append(_("Where: %(where)s") % {"where": where})
    return "\n".join(part for part in parts if part)


def rss_items(
    request: HttpRequest, events: Sequence[services.LocalisedEvent], now: dt.datetime
) -> list[dict[str, Any]]:
    items = []
    for found in events:
        page = found.page
        title = page.title
        if page.cancelled_at is not None:
            title = _("Cancelled: %(title)s") % {"title": title}
        point = ""
        if page.latitude is not None and page.longitude is not None:
            point = f"{page.latitude} {page.longitude}"
        items.append(
            {
                "title": title,
                "link": absolute_url(request, page.url or ""),
                "description": _description(found, now),
                "unique_id": f"urn:uuid:{page.translation_key}",
                "unique_id_is_permalink": False,
                "pubdate": page.first_published_at
                or page.content_changed_at
                or page.last_published_at,
                "categories": [str(page.get_event_type_display())],
                "language": found.language if found.fallback else "",
                "georss_point": point,
            }
        )
    return items


def index_rss_response(index: "EventIndexPage", request: HttpRequest, scope: Scope) -> HttpResponse:
    _check_scope(scope)
    vendor = _vendor_for(scope)
    blocked = throttled(request)
    if blocked is not None:
        return blocked
    now = timezone.now()
    with translation.override(index.locale.language_code):
        title = _feed_title(vendor, scope)
        feed = EventRssFeed(
            title=title,
            link=absolute_url(request, index.url or ""),
            description=title,
            language=index.locale.language_code,
            feed_url=absolute_url(request, request.path),
        )
        for item in rss_items(request, feed_events(index, scope, now), now):
            feed.add_item(**item)
        body: str = feed.writeString("utf-8")
    return respond(request, body, "application/rss+xml; charset=utf-8", last_modified(scope))
