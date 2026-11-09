"""EVT.13: a point confirmed in the map picker is checked against the cached lookup (spec 15
"Confirmation"). The form never takes geocoding metadata from the request: it finds the
candidate the "Find coordinates" endpoint returned in the cache, by address and point."""

import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.geocoding import AddressQuery, engine
from apps.core.geocoding.services import cached_candidate
from apps.core.tests.factories import GeocodeCacheEntryFactory

pytestmark = pytest.mark.django_db

QUERY = AddressQuery(country="IT", postcode="20121", city="Milano", street="Via Esempio 1")
CANDIDATES = [
    {
        "latitude": "45.4642041",
        "longitude": "9.1899819",
        "label": "Via Esempio 1, 20121 Milano",
        "precision": "rooftop",
        "confidence": 0.9,
        "provider": "photon",
    },
    {
        "latitude": "45.470000",
        "longitude": "9.180000",
        "label": "Milano",
        "precision": "locality",
        "confidence": None,
        "provider": "photon",
    },
]


def _cache(query: AddressQuery = QUERY, **kwargs: object) -> None:
    GeocodeCacheEntryFactory.create(
        key=engine.cache_key(query),
        normalised_address=engine.normalised_address(query),
        provider="photon",
        candidates=CANDIDATES,
        **kwargs,
    )


def test_the_candidate_at_the_confirmed_point_is_found() -> None:
    _cache()
    result = cached_candidate(QUERY, Decimal("45.464204"), Decimal("9.189982"))
    assert result is not None
    assert result.provider == "photon"
    assert result.precision == "rooftop"
    assert result.confidence == 0.9
    # Stored at the page's precision (6 decimal places).
    assert (result.latitude, result.longitude) == (Decimal("45.464204"), Decimal("9.189982"))


def test_a_lookup_in_another_language_counts() -> None:
    _cache(language="de")
    assert cached_candidate(QUERY, Decimal("45.47"), Decimal("9.18")) is not None


def test_a_point_that_no_candidate_has_is_not_found() -> None:
    _cache()
    assert cached_candidate(QUERY, Decimal("45.464205"), Decimal("9.189982")) is None


def test_a_candidate_of_another_address_is_not_found() -> None:
    _cache(AddressQuery(country="IT", city="Torino"))
    assert cached_candidate(QUERY, Decimal("45.464204"), Decimal("9.189982")) is None


def test_an_expired_lookup_is_not_used() -> None:
    _cache(expires_at=timezone.now() - dt.timedelta(seconds=1))
    assert cached_candidate(QUERY, Decimal("45.464204"), Decimal("9.189982")) is None
