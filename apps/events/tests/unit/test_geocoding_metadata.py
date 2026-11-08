"""EVT.1: the page stores a geocoded point with its metadata (spec 15 "Location, coordinates and
geocoding"). The candidate comes from the core `Geocoder` protocol (EVT.12); no network here."""

import datetime as dt
from decimal import Decimal

from apps.core.geocoding import ROOFTOP
from apps.core.geocoding.fakes import fake_result
from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory

AT = dt.datetime(2027, 1, 5, 10, 0, tzinfo=dt.UTC)


def test_a_chosen_candidate_sets_the_point_and_its_metadata() -> None:
    page = EventPageFactory.build(city="Milano", country="IT")
    result = fake_result("photon", latitude="45.464204", longitude="9.189982", confidence=0.9)
    page.set_geocoded_point(result, at=AT)

    assert (page.latitude, page.longitude) == (Decimal("45.464204"), Decimal("9.189982"))
    assert page.geo_source == EventPage.GeoSource.GEOCODED
    assert page.geo_provider == "photon"
    assert page.geo_precision == ROOFTOP
    assert page.geo_confidence == 0.9
    assert page.geo_at == AT


def test_moving_the_pin_keeps_the_geocoding_metadata() -> None:
    page = EventPageFactory.build()
    page.set_geocoded_point(fake_result("nominatim", confidence=None), at=AT)
    page.set_point(Decimal("45.5"), Decimal("9.2"), source=EventPage.GeoSource.MAP)

    assert (page.latitude, page.longitude) == (Decimal("45.5"), Decimal("9.2"))
    assert page.geo_source == EventPage.GeoSource.MAP
    assert page.geo_provider == "nominatim"
    assert page.geo_at == AT


def test_clearing_the_point_clears_the_metadata() -> None:
    page = EventPageFactory.build()
    page.set_geocoded_point(fake_result("photon"), at=AT)
    page.clear_point()

    assert page.latitude is None
    assert page.longitude is None
    assert page.geo_source == ""
    assert page.geo_provider == ""
    assert page.geo_precision == ""
    assert page.geo_confidence is None
    assert page.geo_at is None
