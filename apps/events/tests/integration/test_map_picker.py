"""EVT.13: the map picker in the "Where" panel of the Wagtail admin (spec 15 "Map picker").

The panel renders the picker for `events/map.js` (data attributes only, no inline script) with
the vendored Leaflet and the configured tile URL; the form stores what the picker sends:

- a point placed on the map (`geo_source = "map"`) or typed (`"manual"`), saved exactly as sent;
- a "Find coordinates" candidate (`"geocoded"`) only when the cached lookup of the form's address
  has a candidate at that point: the provider, precision and confidence come from the cache,
  never from the request;
- no point: no source and no geocoding metadata.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.geocoding import AddressQuery, engine
from apps.core.tests.factories import GeocodeCacheEntryFactory
from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory

pytestmark = pytest.mark.django_db

GEOCODED_AT = dt.datetime(2027, 1, 5, 10, 0, tzinfo=dt.UTC)
ADDRESS = AddressQuery(country="IT", postcode="20121", city="Milano", street="Via Esempio 1")
CANDIDATE = {
    "latitude": "45.464270",
    "longitude": "9.189510",
    "label": "Via Esempio 1, 20121 Milano",
    "precision": "rooftop",
    "confidence": 0.8,
    "provider": "photon",
}


def _staff(client: Client | None = None) -> User:
    user: User = UserFactory.create(superuser=True)
    if client is not None:
        verified_login(client, user)
    return user


def _event(**kwargs: Any) -> EventPage:
    defaults: dict[str, Any] = {
        "street": ADDRESS.street,
        "postcode": ADDRESS.postcode,
        "city": ADDRESS.city,
        "country": ADDRESS.country,
    }
    page: EventPage = EventPageFactory.create(**{**defaults, **kwargs})
    return page


def _data(page: EventPage, **changes: str) -> dict[str, str]:
    data = {
        "title": page.title,
        "slug": page.slug,
        "summary": page.summary,
        "event_type": page.event_type,
        "starts_at": "2027-03-10 09:00",
        "ends_at": "2027-03-10 17:00",
        "timezone": page.timezone,
        "attendance": page.attendance,
        "venue_name": page.venue_name,
        "street": page.street,
        "postcode": page.postcode,
        "city": page.city,
        "region": page.region,
        "country": page.country,
        "booth": page.booth,
        "latitude": "",
        "longitude": "",
        "geo_source": "",
        "body-count": "0",
        "exdates": "",
        "exdate_days": "",
    }
    data.update(changes)
    return data


def _form(page: EventPage, user: User, data: dict[str, str]) -> Any:
    form_class = EventPage.get_edit_handler().get_form_class()
    return form_class(data, instance=page, parent_page=page.get_parent(), for_user=user)


def _saved(page: EventPage, data: dict[str, str]) -> EventPage:
    form = _form(page, _staff(), data)
    assert form.is_valid(), form.errors
    saved: EventPage = form.save(commit=False)
    return saved


def _edit_url(page: EventPage) -> str:
    return reverse("wagtailadmin_pages:edit", args=(page.pk,))


def _cache_candidate(**candidate: Any) -> None:
    GeocodeCacheEntryFactory.create(
        key=engine.cache_key(ADDRESS),
        normalised_address=engine.normalised_address(ADDRESS),
        provider="photon",
        candidates=[{**CANDIDATE, **candidate}],
    )


# --- The panel


def test_the_where_panel_renders_the_picker_for_the_map_module(
    client: Client, settings: Any
) -> None:
    settings.MAP_TILE_URL = "/maps/tiles/{z}/{x}/{y}.png"
    _staff(client)
    page = _event()
    html = client.get(_edit_url(page)).content.decode()
    assert "data-map-picker" in html
    assert 'data-latitude-input="id_latitude"' in html
    assert 'data-longitude-input="id_longitude"' in html
    assert 'data-source-input="id_geo_source"' in html
    for name in ("street", "postcode", "city", "country"):
        assert f'data-{name}-input="id_{name}"' in html, name
    assert f'data-find-url="{reverse("geocoding_find")}"' in html
    assert 'data-tile-url="/maps/tiles/{z}/{x}/{y}.png"' in html
    assert "OpenStreetMap contributors" in html
    assert "Find coordinates" in html


def test_the_picker_loads_the_vendored_leaflet_and_the_map_module(client: Client) -> None:
    _staff(client)
    html = client.get(_edit_url(_event())).content.decode()
    assert "vendor/leaflet/leaflet.js" in html
    assert "vendor/leaflet/leaflet.css" in html
    assert 'type="module"' in html
    assert "events/map.js" in html
    assert "events/map.css" in html
    for host in ("unpkg.com", "cdn.jsdelivr.net", "openstreetmap.org/{z}", "tile.openstreetmap"):
        assert host not in html, host


def test_the_picker_shows_the_current_point(client: Client) -> None:
    _staff(client)
    page = _event(latitude=Decimal("45.464204"), longitude=Decimal("9.189982"), geo_source="map")
    html = client.get(_edit_url(page)).content.decode()
    assert 'value="45.464204"' in html
    assert 'value="9.189982"' in html


def test_without_a_tile_url_the_picker_has_no_tile_layer(client: Client, settings: Any) -> None:
    settings.MAP_TILE_URL = ""
    _staff(client)
    html = client.get(_edit_url(_event())).content.decode()
    assert 'data-tile-url=""' in html


# --- A point placed on the map or typed


@pytest.mark.parametrize("source", ["map", "manual"])
def test_a_placed_or_typed_point_is_saved_as_sent(source: str) -> None:
    page = _event()
    saved = _saved(page, _data(page, latitude="45.464204", longitude="9.189982", geo_source=source))
    assert (saved.latitude, saved.longitude) == (Decimal("45.464204"), Decimal("9.189982"))
    assert saved.geo_source == source


def test_a_typed_point_without_a_source_counts_as_typed() -> None:
    """Without JavaScript nobody sets the source: the inputs are the keyboard alternative."""
    page = _event()
    saved = _saved(page, _data(page, latitude="45.5", longitude="9.2"))
    assert saved.geo_source == EventPage.GeoSource.MANUAL


def test_moving_a_geocoded_point_keeps_its_metadata() -> None:
    page = _event(
        latitude=Decimal("45.464270"),
        longitude=Decimal("9.189510"),
        geo_source="geocoded",
        geo_provider="photon",
        geo_precision="rooftop",
        geo_confidence=0.8,
        geo_at=GEOCODED_AT,
    )
    saved = _saved(page, _data(page, latitude="45.4651", longitude="9.1902", geo_source="map"))
    assert saved.geo_source == EventPage.GeoSource.MAP
    assert (saved.geo_provider, saved.geo_at) == ("photon", GEOCODED_AT)


def test_clearing_the_point_clears_the_source_and_the_metadata() -> None:
    page = _event(
        latitude=Decimal("45.464270"),
        longitude=Decimal("9.189510"),
        geo_source="geocoded",
        geo_provider="photon",
        geo_precision="rooftop",
        geo_confidence=0.8,
        geo_at=GEOCODED_AT,
    )
    saved = _saved(page, _data(page, geo_source="map"))
    assert saved.latitude is None
    assert saved.longitude is None
    assert saved.geo_source == EventPage.GeoSource.NONE
    assert (saved.geo_provider, saved.geo_precision) == ("", "")
    assert saved.geo_confidence is None
    assert saved.geo_at is None


def test_half_a_point_is_refused() -> None:
    page = _event()
    form = _form(page, _staff(), _data(page, latitude="45.5", geo_source="manual"))
    assert not form.is_valid()


# --- A "Find coordinates" candidate


def test_a_cached_candidate_is_stored_with_its_metadata_from_the_cache() -> None:
    _cache_candidate()
    page = _event()
    data = _data(page, latitude="45.464270", longitude="9.189510", geo_source="geocoded")
    saved = _saved(page, data)
    assert (saved.latitude, saved.longitude) == (Decimal("45.464270"), Decimal("9.189510"))
    assert saved.geo_source == EventPage.GeoSource.GEOCODED
    assert saved.geo_provider == "photon"
    assert saved.geo_precision == "rooftop"
    assert saved.geo_confidence == 0.8
    assert saved.geo_at is not None


def test_a_crafted_geocoded_point_is_refused() -> None:
    """No cached lookup has this point: the request cannot claim a provider's result."""
    _cache_candidate()
    page = _event()
    data = _data(page, latitude="45.0", longitude="9.0", geo_source="geocoded")
    form = _form(page, _staff(), data)
    assert not form.is_valid()
    assert "geo_source" in form.errors


def test_a_candidate_for_another_address_is_refused() -> None:
    _cache_candidate()
    page = _event()
    data = _data(
        page, city="Torino", latitude="45.464270", longitude="9.189510", geo_source="geocoded"
    )
    form = _form(page, _staff(), data)
    assert not form.is_valid()
    assert "geo_source" in form.errors


def test_an_unchanged_geocoded_point_is_kept_after_its_cache_entry_expired() -> None:
    page = _event(
        latitude=Decimal("45.464270"),
        longitude=Decimal("9.189510"),
        geo_source="geocoded",
        geo_provider="photon",
        geo_precision="rooftop",
        geo_confidence=0.8,
        geo_at=GEOCODED_AT,
    )
    data = _data(page, latitude="45.464270", longitude="9.189510", geo_source="geocoded")
    saved = _saved(page, data)
    assert saved.geo_source == EventPage.GeoSource.GEOCODED
    assert (saved.geo_provider, saved.geo_at) == ("photon", GEOCODED_AT)


# --- Through the admin edit view: the saved values match what was sent


def test_staff_saves_a_point_through_the_admin_and_the_values_match(client: Client) -> None:
    _staff(client)
    page = _event()
    data = _data(page, latitude="45.464204", longitude="9.189982", geo_source="map")
    response = client.post(_edit_url(page), data)
    assert response.status_code == 302, response.content.decode()[:2000]
    draft = EventPage.objects.get(pk=page.pk).get_latest_revision_as_object()
    assert (draft.latitude, draft.longitude) == (Decimal("45.464204"), Decimal("9.189982"))
    assert draft.geo_source == EventPage.GeoSource.MAP
