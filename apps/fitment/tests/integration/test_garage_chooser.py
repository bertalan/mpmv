"""FIT.7 (1/n): the header garage menu and the chooser (spec 17 §17.4.4).

The state changes only by a POST that redirects and announces; no garage state is in a URL; a
profile of another account is a 404; the menu works for the cookie garage and for profiles.
"""

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.accounts.tests.factories import UserFactory
from apps.fitment import garage
from apps.fitment.models import GarageProfile
from apps.fitment.tests.factories import (
    FitmentLevelFactory,
    FitmentNodeFactory,
    GarageProfileFactory,
)

pytestmark = pytest.mark.django_db

CHOOSE = "garage_choose"
APPLY = "garage_apply"


@pytest.fixture
def vehicles() -> None:
    make = FitmentNodeFactory.create(name="Acme")
    FitmentNodeFactory.create(name="Roadster", parent=make)
    FitmentNodeFactory.create(name="Tourer", parent=make)


def _save(client: Client, model: str) -> Any:
    return client.post(
        reverse("garage_save"), {"make": "Acme", "model": model, "year": "", "next": "/"}
    )


def _menu(html: str) -> str:
    match = re.search(r"<details class=\"user-menu garage-menu\".*?</details>", html, re.S)
    assert match, "the header carries no garage menu"
    return match.group(0)


def test_the_menu_invites_a_visitor_without_a_garage(client: Client, vehicles: None) -> None:
    html = _menu(client.get("/").content.decode())
    assert "Add your garage" in html
    assert reverse(CHOOSE) in html
    assert "My garage" not in html  # nothing to manage for a visitor


def test_the_menu_names_the_active_vehicle_and_generic(client: Client, vehicles: None) -> None:
    _save(client, "Roadster")
    assert "Garage: Acme, Roadster" in _menu(client.get("/").content.decode())
    client.post(reverse(APPLY), {"choice": "generic"})
    assert "Garage: generic" in _menu(client.get("/").content.decode())


def test_the_chooser_applies_a_cookie_vehicle_with_a_post_and_a_redirect(
    client: Client, vehicles: None
) -> None:
    _save(client, "Roadster")
    _save(client, "Tourer")
    page = client.get(reverse(CHOOSE), {"next": "/"})
    html = page.content.decode()
    assert 'value="vehicle:0"' in html
    assert 'value="vehicle:1"' in html
    assert 'value="generic"' in html
    assert "noindex" in html
    assert "private" in page["Cache-Control"] or "no-store" in page["Cache-Control"]
    response = client.post(reverse(APPLY), {"choice": "vehicle:0", "next": "/"})
    assert response.status_code == 302
    assert response["Location"] == "/"
    assert garage.parse(response.cookies[garage.COOKIE_NAME].value).active == 0
    assert "?" not in response["Location"]  # no garage state in the URL
    follow = client.get("/")
    assert "is now your active vehicle" in follow.content.decode()  # the polite announcement


def test_generic_keeps_the_vehicles_but_applies_none(client: Client, vehicles: None) -> None:
    _save(client, "Roadster")
    response = client.post(reverse(APPLY), {"choice": "generic"})
    kept = garage.parse(response.cookies[garage.COOKIE_NAME].value)
    assert kept.active is None
    assert len(kept.vehicles) == 1
    assert kept.active_snapshot() is None


def test_a_bad_choice_or_an_unsafe_next_changes_nothing(client: Client, vehicles: None) -> None:
    _save(client, "Roadster")
    for choice in ("vehicle:9", "vehicle:x", "profile:" + "0" * 32, "nonsense", ""):
        assert client.post(reverse(APPLY), {"choice": choice}).status_code == 400
    response = client.post(reverse(APPLY), {"choice": "vehicle:0", "next": "https://evil.example/"})
    assert response["Location"] == "/"
    assert client.get(reverse(APPLY)).status_code == 405  # a link can never change the state


def test_an_empty_chooser_explains_and_off_mode_has_no_menu(
    client: Client, vehicles: None, settings: SettingsWrapper
) -> None:
    assert "data-garage-empty" in client.get(reverse(CHOOSE)).content.decode()
    settings.GARAGE_ANON_STORAGE = "off"
    assert client.get(reverse(CHOOSE)).status_code == 404
    assert "garage-menu" not in client.get("/").content.decode()


def test_a_signed_in_customer_chooses_among_their_profiles(client: Client) -> None:
    for position, code in enumerate(["make", "model"], start=1):
        FitmentLevelFactory.create(position=position, code=code)
    user = UserFactory.create()
    make = FitmentNodeFactory.create(name="Make One")
    one = GarageProfileFactory.create(
        user=user, name="Weekend", is_active=True, node=FitmentNodeFactory.create(parent=make)
    )
    two = GarageProfileFactory.create(
        user=user, name="Old car", node=FitmentNodeFactory.create(parent=make)
    )
    stranger = GarageProfileFactory.create(name="Not mine")
    client.force_login(user)
    assert "Garage: Weekend" in _menu(client.get("/").content.decode())
    assert reverse("garage_account") in _menu(client.get("/").content.decode())
    html = client.get(reverse(CHOOSE)).content.decode()
    assert f"profile:{two.public_id}" in html
    assert "Not mine" not in html
    client.post(reverse(APPLY), {"choice": f"profile:{two.public_id}"})
    one.refresh_from_db()
    two.refresh_from_db()
    assert (one.is_active, two.is_active) == (False, True)
    assert (
        client.post(reverse(APPLY), {"choice": f"profile:{stranger.public_id}"}).status_code == 404
    )
    client.post(reverse(APPLY), {"choice": "generic"})
    assert not GarageProfile.objects.filter(user=user, is_active=True).exists()
    assert GarageProfile.objects.filter(user=user).count() == 2  # profiles are kept


def _state_queries(client: Client) -> int:
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.fitment import garage_state

    request = client.get("/").wsgi_request
    delattr(request, "_garage_state")  # the page above already filled the per-request cache
    with CaptureQueriesContext(connection) as queries:
        garage_state.garage_state(request)
        garage_state.garage_state(request)  # a second read in the same request is free
    return len(queries)


def test_the_header_menu_costs_a_bounded_number_of_queries_for_a_visitor(
    client: Client, vehicles: None
) -> None:
    assert _state_queries(client) <= 1  # no cookie: the scheme row only
    _save(client, "Roadster")
    _save(client, "Tourer")
    assert _state_queries(client) <= 7  # the scheme, then three lookups per vehicle (at most three)


def test_the_header_menu_costs_a_bounded_number_of_queries_when_signed_in(client: Client) -> None:
    user = UserFactory.create()
    for _ in range(5):
        GarageProfileFactory.create(user=user)
    client.force_login(user)
    assert _state_queries(client) <= 3  # scheme and profiles once, whatever their number


def test_the_chooser_lists_options_and_marks_the_active_one(client: Client, vehicles: None) -> None:
    _save(client, "Roadster")
    _save(client, "Tourer")
    html = client.get(reverse(CHOOSE), {"next": "/shop/"}).content.decode()
    assert re.search(r'value="vehicle:1"\s+checked', html)  # the last saved is active
    assert 'value="generic"' in html
    assert 'value="generic" checked' not in html
    assert "Acme, Roadster" in html
    assert "Acme, Tourer" in html
    assert 'name="next" value="/shop/"' in html
    assert 'href="/shop/"' in html  # Cancel returns without a change
