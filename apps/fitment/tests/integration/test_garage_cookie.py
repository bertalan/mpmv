"""FIT.6: the anonymous garage cookie is technical, written on Save only, capped at 12 months."""

import datetime as dt
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.template.loader import render_to_string
from django.test import Client, RequestFactory
from django.urls import reverse
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.fitment import garage, services
from apps.fitment.tests.factories import FitmentNodeFactory, FitmentSchemeFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def make_model() -> None:
    make = FitmentNodeFactory.create(name="Acme")
    FitmentNodeFactory.create(name="Roadster", parent=make)
    FitmentNodeFactory.create(name="Tourer", parent=make)


def save(client: Client, **fields: str) -> Any:
    data = {"make": "Acme", "model": "Roadster", "year": "", **fields}
    return client.post(reverse("garage_save"), data)


def test_nothing_is_written_before_save(client: Client, make_model: None) -> None:
    response = client.get("/")
    assert garage.COOKIE_NAME not in response.cookies


def test_save_writes_the_cookie_with_no_consent_and_a_lifetime_within_12_months(
    client: Client, make_model: None
) -> None:
    response = save(client)
    cookie = response.cookies[garage.COOKIE_NAME]
    assert 0 < int(cookie["max-age"]) <= 365 * 86400
    assert int(cookie["max-age"]) == 180 * 86400  # the default
    assert cookie["samesite"] == "Lax"
    assert cookie["httponly"]
    assert garage.parse(cookie.value).active_snapshot() == {
        "make": "Acme",
        "model": "Roadster",
        "year": "",
    }
    assert "consent" not in str(response.cookies).lower()


def test_the_cookie_expires_when_its_lifetime_ends(client: Client, make_model: None) -> None:
    """The browser drops the cookie at `expires`: it is never more than 12 months away."""
    cookie = save(client).cookies[garage.COOKIE_NAME]
    expires = dt.datetime.strptime(cookie["expires"], "%a, %d %b %Y %H:%M:%S %Z")
    lifetime = expires - dt.datetime.now(dt.UTC).replace(tzinfo=None)
    assert dt.timedelta(days=179) < lifetime <= dt.timedelta(days=365)


def test_a_vehicle_outside_the_scheme_or_hostile_is_refused(
    client: Client, make_model: None
) -> None:
    for fields in (
        {"make": "Nobody"},
        {"model": "Missing"},
        {"make": "<script>alert(1)</script>"},
        {"model": "x" * 500},
        {"make": ""},
    ):
        response = save(client, **fields)
        assert response.status_code == 400
        assert garage.COOKIE_NAME not in client.cookies


def test_the_year_follows_the_scheme(client: Client, make_model: None) -> None:
    scheme = FitmentSchemeFactory.create()
    assert services.vehicle_from_labels("Acme", "Roadster", "2019").year == ""  # no year used
    scheme.year_enabled = True
    scheme.save()
    assert services.vehicle_from_labels("Acme", "Roadster", "2019").year == "2019"
    with pytest.raises(services.FitmentError):
        services.vehicle_from_labels("Acme", "Roadster", "1800")
    with pytest.raises(services.FitmentError):
        services.vehicle_from_labels("Acme", "Roadster", "20x9")


def test_off_mode_never_writes_the_cookie(
    client: Client, make_model: None, settings: SettingsWrapper
) -> None:
    settings.GARAGE_ANON_STORAGE = "off"
    assert save(client).status_code == 404
    assert garage.COOKIE_NAME not in client.cookies


def test_a_scheme_with_sensitive_items_keeps_no_cookie(client: Client, make_model: None) -> None:
    scheme = FitmentSchemeFactory.create()
    scheme.contains_special_category = True
    scheme.save()
    assert save(client).status_code == 404


def test_remove_vehicle_removes_one_vehicle_at_once(client: Client, make_model: None) -> None:
    save(client)
    save(client, model="Tourer")
    response = client.post(reverse("garage_remove"), {"index": "0"})
    kept = garage.parse(response.cookies[garage.COOKIE_NAME].value)
    assert [v.model for v in kept.vehicles] == ["Tourer"]


def test_removing_the_last_vehicle_deletes_the_cookie(client: Client, make_model: None) -> None:
    save(client)
    response = client.post(reverse("garage_remove"), {"index": "0"})
    assert response.cookies[garage.COOKIE_NAME]["max-age"] == 0


def test_forget_deletes_the_cookie_and_remove_still_works_when_storage_is_off(
    client: Client, make_model: None, settings: SettingsWrapper
) -> None:
    save(client)
    settings.GARAGE_ANON_STORAGE = "off"
    assert (
        client.post(reverse("garage_remove"), {"index": "0"}).cookies[garage.COOKIE_NAME]["max-age"]
        == 0
    )
    assert client.post(reverse("garage_forget")).cookies[garage.COOKIE_NAME]["max-age"] == 0


def test_the_active_vehicle_comes_from_the_cookie_only(
    client: Client, make_model: None, rf: RequestFactory
) -> None:
    save(client)
    request = rf.get("/")
    request.COOKIES = {garage.COOKIE_NAME: client.cookies[garage.COOKIE_NAME].value}
    assert services.active_vehicle(request) == {"make": "Acme", "model": "Roadster", "year": ""}
    request.COOKIES = {garage.COOKIE_NAME: "tampered"}
    assert services.active_vehicle(request) is None


def test_a_lifetime_above_12_months_fails_the_system_check(settings: SettingsWrapper) -> None:
    settings.GARAGE_COOKIE_DAYS = 366
    with pytest.raises(SystemCheckError, match=r"fitment\.E002"):
        call_command("check")


def test_the_controls_show_the_spec_label_and_a_remove_button(make_model: None) -> None:
    html = render_to_string(
        "fitment/_garage_saver.html",
        {
            "garage_vehicles": [garage.Vehicle("Acme", "Roadster", "2019")],
            "garage_candidate": {"make": "Acme", "model": "Tourer", "year": ""},
            "next": "/",
        },
    )
    assert garage.SAVE_LABEL in html
    assert "Remove vehicle" in html
    assert "Forget my garage" in html


def test_the_label_and_save_button_are_absent_when_storage_is_off() -> None:
    html = render_to_string(
        "fitment/_garage_saver.html",
        {
            "garage_candidate": {"make": "Acme", "model": "Tourer", "year": ""},
            "garage_storage_off": True,
        },
    )
    assert garage.SAVE_LABEL not in html
