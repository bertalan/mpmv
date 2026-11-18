"""EVT.15: public maps (spec 15 "Maps", regression cases 21 and 22).

The event page with coordinates renders a map container (a labelled region carrying its data
attributes), a skip link past it, the text address above it, the three directions links and the
OSM attribution in the server-rendered fallback. With an address and no coordinates it renders
one "Open in maps" search link and no map; with neither it renders no map and no map link. The
index plots the listed events that have coordinates and renders no map when none has. The
assertions use markup hooks and URLs, never translated text, so they hold under any language set.
"""

import datetime as dt
import re
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest
from django.apps import apps
from django.test import Client
from django.utils import timezone

from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory, primary_index

pytestmark = pytest.mark.django_db

GOOGLE = "https://www.google.com/maps/dir/?api=1&amp;destination=45.464204,9.189982"
OSM_COPYRIGHT = "https://www.openstreetmap.org/copyright"


@pytest.fixture(autouse=True)
def _indexing_on() -> None:
    from wagtail.models import Site

    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def _event(days: int = 10, **fields: object) -> EventPage:
    start = (timezone.now() + dt.timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    page = EventPageFactory.create(starts_at=start, ends_at=start + dt.timedelta(hours=8), **fields)
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def _html(url: str) -> str:
    response = Client().get(url)
    assert response.status_code == 200, (url, response.status_code)
    return response.content.decode()


def _container(html: str) -> str:
    """The opening tag of the event map container."""
    match = re.search(r"<div[^>]*\bdata-event-map\b[^>]*>", html)
    assert match, "no event map container"
    return match.group(0)


def _attr(tag: str, name: str) -> str:
    match = re.search(rf'\b{re.escape(name)}="([^"]*)"', tag)
    assert match, (name, tag)
    return match.group(1)


# --- Regression case 21: the map only with coordinates


def test_event_with_coordinates_renders_the_map_container_and_links() -> None:
    page = _event(located=True, venue_name="Fiera Esempio")
    html = _html(str(page.url))
    tag = _container(html)
    assert _attr(tag, "data-lat") == "45.464204"
    assert _attr(tag, "data-lng") == "9.189982"
    assert _attr(tag, "data-zoom")
    assert _attr(tag, "data-label") == "Fiera Esempio"
    assert 'role="region"' in tag
    assert _attr(tag, "aria-label").strip()
    assert "Fiera Esempio" in _attr(tag, "aria-label")
    assert GOOGLE in html
    assert "https://www.openstreetmap.org/directions" in html
    assert 'href="geo:45.464204,9.189982"' in html
    assert "data-map-search" not in html


def test_the_coordinates_are_not_localised() -> None:
    """A comma decimal separator would break the data attributes and the geo: URI."""
    page = _event(located=True, latitude=Decimal("45.5"), longitude=Decimal("9.25"))
    html = _html(str(page.url))
    tag = _container(html)
    assert _attr(tag, "data-lat") == "45.5"
    assert _attr(tag, "data-lng") == "9.25"
    assert 'href="geo:45.5,9.25"' in html


def test_the_map_uses_the_tile_proxy_setting(settings: object) -> None:
    page = _event(located=True)
    tag = _container(_html(str(page.url)))
    assert _attr(tag, "data-tile-url") == "/maps/tiles/{z}/{x}/{y}.png"


def test_the_map_loads_leaflet_and_the_module_without_inline_code() -> None:
    page = _event(located=True)
    html = _html(str(page.url))
    assert "vendor/leaflet/leaflet.js" in html
    assert "events/map.js" in html
    assert re.search(r'<script[^>]*type="module"[^>]*events/map\.js', html)
    assert "<style" not in html
    assert " style=" not in html
    for script in re.findall(r"<script[^>]*>", html):
        assert "src=" in script or 'type="application/ld+json"' in script, script


def test_the_address_comes_above_the_map_and_a_skip_link_jumps_past_it() -> None:
    page = _event(located=True)
    html = _html(str(page.url))
    assert html.index("<address") < html.index("data-event-map")
    skip = re.search(r'<a[^>]*class="[^"]*event-map__skip[^"]*"[^>]*>', html)
    assert skip, "no skip link"
    target = _attr(skip.group(0), "href")
    assert target.startswith("#")
    # The skip link comes before the map and its target after it.
    assert html.index("event-map__skip") < html.index("data-event-map")
    assert html.index(f'id="{target[1:]}"') > html.index("data-event-map")


def test_address_without_coordinates_has_one_search_link_and_no_map() -> None:
    page = _event(street="Via Esempio 1", postcode="20121", city="Milano", country="IT")
    html = _html(str(page.url))
    assert "data-event-map" not in html
    assert "vendor/leaflet/leaflet.js" not in html
    links = re.findall(r"<a[^>]*data-map-search[^>]*>", html)
    assert len(links) == 1
    parts = urlsplit(_attr(links[0], "href").replace("&amp;", "&"))
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://www.openstreetmap.org/search"
    query = parse_qs(parts.query)["query"][0]
    assert "Via Esempio 1" in query
    assert "Milano" in query
    assert "geo:" not in html
    assert "google.com/maps" not in html


def test_neither_address_nor_coordinates_has_no_map_and_no_map_link() -> None:
    page = _event(promotion=True)
    html = _html(str(page.url))
    assert "data-event-map" not in html
    assert "data-map-search" not in html
    assert "openstreetmap.org" not in html


def test_online_event_has_no_map() -> None:
    page = _event(online=True)
    assert "data-event-map" not in _html(str(page.url))


# --- Regression case 22: the fallback without JavaScript


def test_fallback_in_the_container_has_the_address_and_the_attribution() -> None:
    page = _event(located=True, venue_name="Fiera Esempio")
    html = _html(str(page.url))
    start = html.index("data-event-map")
    end = html.index("</div>", start)
    inside = html[start:end]
    assert "Via Esempio 1" in inside  # no empty map box
    assert "Milano" in inside
    assert OSM_COPYRIGHT in inside
    assert "OpenStreetMap contributors" in inside


def test_attribution_text_and_link_come_from_the_settings(settings: object) -> None:
    settings.MAP_TILE_ATTRIBUTION = "Tiles by Example"  # type: ignore[attr-defined]
    settings.MAP_TILE_ATTRIBUTION_URL = "https://tiles.example.com/about"  # type: ignore[attr-defined]
    page = _event(located=True)
    html = _html(str(page.url))
    assert "Tiles by Example" in html
    assert 'href="https://tiles.example.com/about"' in html
    tag = _container(html)
    assert _attr(tag, "data-attribution") == "Tiles by Example"
    assert _attr(tag, "data-attribution-url") == "https://tiles.example.com/about"


# --- The index map


def test_index_without_coordinates_renders_no_map() -> None:
    _event(days=5, street="Via Esempio 1", city="Milano", country="IT")
    html = _html(str(primary_index().url))
    assert "data-events-map" not in html
    assert "vendor/leaflet/leaflet.js" not in html


def test_index_with_one_located_event_plots_one_and_lists_the_others() -> None:
    _event(days=5, located=True, title="Located fair")
    _event(days=6, promotion=True, title="Plain promotion")
    html = _html(str(primary_index().url))
    assert len(re.findall(r"<[^>]*\bdata-events-map\b[^>]*>", html)) == 1
    plotted = re.findall(r'<article[^>]*data-lat="([^"]+)"[^>]*data-lng="([^"]+)"', html)
    assert plotted == [("45.464204", "9.189982")]
    assert "Located fair" in html
    assert "Plain promotion" in html
    assert len(re.findall(r'<article class="event-summary"', html)) == 2
    assert "vendor/leaflet/leaflet.js" in html
    assert "events/map.js" in html


def test_index_map_is_a_labelled_region_with_a_skip_link_and_the_tile_setting() -> None:
    _event(days=5, located=True)
    html = _html(str(primary_index().url))
    tag = re.search(r"<[^>]*\bdata-events-map\b[^>]*>", html)
    assert tag
    assert _attr(tag.group(0), "data-tile-url") == "/maps/tiles/{z}/{x}/{y}.png"
    assert 'role="region"' in html[html.index("data-events-map") :]
    skip = re.search(r'<a[^>]*class="[^"]*event-map__skip[^"]*"[^>]*>', html)
    assert skip
    target = _attr(skip.group(0), "href")
    assert html.index(f'id="{target[1:]}"') > html.index("data-events-map")
    # Above the list.
    assert html.index("data-events-map") < html.index('class="event-summary"')


def test_the_index_plots_only_what_the_filter_lists() -> None:
    _event(days=5, located=True, title="Located fair")
    _event(days=6, promotion=True, title="Plain promotion")
    html = _html(str(primary_index().url) + "?type=promotion")
    assert "data-events-map" not in html
    assert "data-lat=" not in html
