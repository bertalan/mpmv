"""EVT.12: the "Find coordinates" endpoint, open only to staff and vendor members, with a
per-user limit. The chain is made of `FakeGeocoder`s: no test reaches the network."""

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.geocoding import services as geo
from apps.core.geocoding.fakes import FakeGeocoder, fake_result
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db

ADDRESS = {"country": "IT", "postcode": "20122", "city": "Milano", "street": "Piazza del Duomo 1"}


@pytest.fixture
def photon(monkeypatch: pytest.MonkeyPatch) -> FakeGeocoder:
    fake = FakeGeocoder("photon", results=[fake_result("photon")])
    monkeypatch.setattr(geo, "configured_geocoders", lambda: [fake])
    return fake


def find(client: Client, data: dict[str, str] | None = None) -> Any:
    return client.post(reverse("geocoding_find"), data if data is not None else ADDRESS)


def test_an_anonymous_visitor_gets_403(client: Client, photon: FakeGeocoder) -> None:
    response = find(client)
    assert response.status_code == 403
    assert photon.calls == []


def test_a_customer_gets_403(client: Client, photon: FakeGeocoder) -> None:
    client.force_login(UserFactory.create())
    response = find(client)
    assert response.status_code == 403
    assert photon.calls == []


def test_staff_get_the_candidates(client: Client, photon: FakeGeocoder) -> None:
    verified_login(client, UserFactory.create(staff=True))
    response = find(client)
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "photon"
    assert body["cached"] is False
    [candidate] = body["candidates"]
    assert candidate["provider"] == "photon"
    assert set(candidate) == {
        "latitude",
        "longitude",
        "label",
        "precision",
        "confidence",
        "provider",
    }


def test_a_vendor_member_gets_the_candidates(client: Client, photon: FakeGeocoder) -> None:
    member = VendorMemberFactory.create(role="staff")
    verified_login(client, member.user)
    assert find(client).status_code == 200
    assert len(photon.calls) == 1


def test_get_is_not_allowed(client: Client, photon: FakeGeocoder) -> None:
    verified_login(client, UserFactory.create(staff=True))
    assert client.get(reverse("geocoding_find"), ADDRESS).status_code == 405


def test_an_empty_address_is_refused(client: Client, photon: FakeGeocoder) -> None:
    verified_login(client, UserFactory.create(staff=True))
    response = find(client, {"country": "IT"})
    assert response.status_code == 400
    assert photon.calls == []


def test_each_user_has_ten_lookups_per_minute(client: Client, photon: FakeGeocoder) -> None:
    verified_login(client, UserFactory.create(staff=True))
    for n in range(10):
        assert find(client, {**ADDRESS, "street": f"Via Roma {n}"}).status_code == 200
    response = find(client, {**ADDRESS, "street": "Via Roma 99"})
    assert response.status_code == 429
    assert 0 < int(response["Retry-After"]) <= 60
    assert len(photon.calls) == 10

    other = Client()
    verified_login(other, UserFactory.create(staff=True))
    assert find(other).status_code == 200  # the limit is per user
