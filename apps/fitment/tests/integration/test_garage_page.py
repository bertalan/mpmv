"""FIT.5 (2/3): the "My garage" page of the customer area (spec 17 §17.4.1, §17.5.6).

Every action is a POST with CSRF and no URL parameter holds state; a profile of another account
is a 404, the same answer as for an unknown id; an anonymous visitor is sent to sign in.
"""

import re
import secrets
import uuid
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import translation

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.models import AuditLog
from apps.fitment import garage_profiles
from apps.fitment.models import FitmentScheme, GarageProfile
from apps.fitment.tests.factories import (
    FitmentLevelFactory,
    FitmentNodeFactory,
    GarageProfileFactory,
)

type _TestResponse = Any

pytestmark = pytest.mark.django_db

PAGE = "garage_account"
PROFILE_ACTIONS = [
    "garage_profile_activate",
    "garage_profile_rename",
    "garage_profile_duplicate",
    "garage_profile_delete",
]


@pytest.fixture(autouse=True)
def levels() -> None:
    for position, code in enumerate(["make", "model", "variant"], start=1):
        FitmentLevelFactory.create(position=position, code=code)


@pytest.fixture
def user() -> User:
    return UserFactory.create(password=secrets.token_urlsafe(16))


@pytest.fixture
def client(user: User) -> Client:
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    return client


def _token(client: Client) -> str:
    page = client.get(reverse(PAGE)).content.decode()
    return re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]


def post(client: Client, name: str, /, *args: object, **data: str) -> _TestResponse:
    return client.post(reverse(name, args=args), {"csrfmiddlewaretoken": _token(client), **data})


def _node(name: str = "Model A"):  # type: ignore[no-untyped-def]
    make = FitmentNodeFactory.create(name="Make One")
    return FitmentNodeFactory.create(name=name, parent=make)


def test_the_page_lists_the_profiles_and_marks_the_active_one(client: Client, user: User) -> None:
    GarageProfileFactory.create(user=user, name="Weekend bike", node=_node(), is_active=True)
    GarageProfileFactory.create(user=user, name="Old car", node=_node("Model B"))
    GarageProfileFactory.create(name="Someone else's")
    response = client.get(reverse(PAGE))
    body = response.content.decode()
    assert response.status_code == 200
    assert "Weekend bike" in body
    assert "Old car" in body
    assert "Someone else" not in body
    assert body.count("data-garage-active") == 1
    assert "Make One" in body  # the node path, not only the name
    assert "no-store" in response["Cache-Control"] or "private" in response["Cache-Control"]
    assert 'name="robots" content="noindex' in body


def test_the_empty_state_says_how_to_start(client: Client) -> None:
    body = client.get(reverse(PAGE)).content.decode()
    assert "data-garage-empty" in body
    assert "data-garage-profile" not in body
    assert reverse("garage_reset") not in body


def test_the_page_has_no_url_parameter_and_no_state_in_links(client: Client, user: User) -> None:
    GarageProfileFactory.create(user=user, node=_node())
    body = client.get(reverse(PAGE)).content.decode()
    targets = re.findall(r'(?:href|action)="(/account/garage[^"]*)"', body)
    assert targets
    assert not any("?" in target or "=" in target for target in targets)


def test_activate_switches_the_active_profile(client: Client, user: User) -> None:
    first = GarageProfileFactory.create(user=user, node=_node(), is_active=True)
    second = GarageProfileFactory.create(user=user, node=_node("Model B"))
    response = post(client, "garage_profile_activate", second.public_id)
    assert response.status_code == 302
    assert response["Location"] == reverse(PAGE)
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.is_active, second.is_active) == (False, True)
    assert AuditLog.objects.filter(action=garage_profiles.PROFILE_ACTIVATED).exists()


def test_reset_clears_the_active_flag_and_keeps_the_profiles(client: Client, user: User) -> None:
    GarageProfileFactory.create(user=user, node=_node(), is_active=True)
    GarageProfileFactory.create(user=user, node=_node("Model B"))
    post(client, "garage_reset")
    assert GarageProfile.objects.filter(user=user).count() == 2
    assert not GarageProfile.objects.filter(user=user, is_active=True).exists()


def test_reset_leaves_another_customers_garage_alone(client: Client) -> None:
    other = GarageProfileFactory.create(node=_node(), is_active=True)
    post(client, "garage_reset")
    other.refresh_from_db()
    assert other.is_active is True


def test_rename_changes_the_name(client: Client, user: User) -> None:
    profile = GarageProfileFactory.create(user=user, node=_node())
    post(client, "garage_profile_rename", profile.public_id, name="  My   Scooter ")
    profile.refresh_from_db()
    assert profile.name == "My Scooter"


@pytest.mark.parametrize("bad", ["", "   ", "x" * 200])
def test_rename_refuses_a_bad_name_and_says_why(client: Client, user: User, bad: str) -> None:
    profile = GarageProfileFactory.create(user=user, name="Keep me", node=_node())
    response = post(client, "garage_profile_rename", profile.public_id, name=bad)
    assert response.status_code == 302
    profile.refresh_from_db()
    assert profile.name == "Keep me"
    assert "alert" in client.get(reverse(PAGE)).content.decode()


def test_duplicate_adds_a_copy_that_is_not_active(client: Client, user: User) -> None:
    profile = GarageProfileFactory.create(user=user, name="Car", node=_node(), is_active=True)
    post(client, "garage_profile_duplicate", profile.public_id)
    copy = GarageProfile.objects.get(user=user, name="Car (copy)")
    assert copy.is_active is False
    assert copy.node_id == profile.node_id


def test_the_limit_refuses_a_duplicate_with_a_message(client: Client, user: User) -> None:
    FitmentScheme.objects.update(max_profiles=1)
    profile = GarageProfileFactory.create(user=user, node=_node())
    post(client, "garage_profile_duplicate", profile.public_id)
    assert GarageProfile.objects.filter(user=user).count() == 1
    body = client.get(reverse(PAGE)).content.decode()
    assert "at most 1 profile" in body  # the flash message
    assert "data-garage-full" in body  # the standing note on the page


def test_delete_asks_for_confirmation_first(client: Client, user: User) -> None:
    profile = GarageProfileFactory.create(user=user, name="Doomed", node=_node())
    page = client.get(reverse("garage_profile_delete", args=[profile.public_id]))
    assert page.status_code == 200
    assert "Doomed" in page.content.decode()
    assert GarageProfile.objects.filter(pk=profile.pk).exists()  # a GET deletes nothing
    refused = post(client, "garage_profile_delete", profile.public_id)  # no confirmation field
    assert refused.status_code == 200
    assert GarageProfile.objects.filter(pk=profile.pk).exists()
    done = post(client, "garage_profile_delete", profile.public_id, confirm="yes")
    assert done.status_code == 302
    assert not GarageProfile.objects.filter(pk=profile.pk).exists()


def test_a_retired_node_is_marked_with_its_action(client: Client, user: User) -> None:
    node = _node("Retired Model")
    GarageProfileFactory.create(user=user, name="Gone", node=node)
    node.status = "retired"
    node.save()
    body = client.get(reverse(PAGE)).content.decode()
    assert "data-garage-retired" in body
    assert "no longer in the catalogue" in body


@pytest.mark.parametrize("name", PROFILE_ACTIONS)
def test_another_customers_profile_is_a_404_like_an_unknown_one(client: Client, name: str) -> None:
    stranger = GarageProfileFactory.create(name="Stranger", node=_node(), is_active=True)
    for public_id in (stranger.public_id, uuid.uuid4()):
        response = client.post(
            reverse(name, args=[public_id]),
            {"csrfmiddlewaretoken": _token(client), "name": "x", "confirm": "yes"},
        )
        assert response.status_code == 404
    stranger.refresh_from_db()
    assert stranger.name == "Stranger"
    assert stranger.is_active is True


def test_the_confirmation_page_of_a_stranger_is_a_404(client: Client) -> None:
    stranger = GarageProfileFactory.create(node=_node())
    assert (
        client.get(reverse("garage_profile_delete", args=[stranger.public_id])).status_code == 404
    )


@pytest.mark.parametrize("name", ["garage_account", "garage_reset", *PROFILE_ACTIONS])
def test_an_anonymous_visitor_is_sent_to_sign_in(name: str) -> None:
    args = [uuid.uuid4()] if name in PROFILE_ACTIONS else []
    for method in ("get", "post"):
        response = getattr(Client(), method)(reverse(name, args=args))
        assert response.status_code == 302
        assert reverse("login") in response["Location"]


@pytest.mark.parametrize("name", ["garage_reset", *PROFILE_ACTIONS[:3]])
def test_actions_refuse_get_and_a_post_without_the_csrf_token(
    client: Client, user: User, name: str
) -> None:
    profile = GarageProfileFactory.create(user=user, node=_node(), is_active=True)
    args = [profile.public_id] if name in PROFILE_ACTIONS else []
    assert client.get(reverse(name, args=args)).status_code == 405
    assert client.post(reverse(name, args=args)).status_code == 403  # CSRF failure
    profile.refresh_from_db()
    assert profile.is_active is True


def test_the_confirmed_delete_needs_the_csrf_token(client: Client, user: User) -> None:
    profile = GarageProfileFactory.create(user=user, node=_node())
    response = client.post(
        reverse("garage_profile_delete", args=[profile.public_id]), {"confirm": "yes"}
    )
    assert response.status_code == 403
    assert GarageProfile.objects.filter(pk=profile.pk).exists()


def test_the_special_category_switch_makes_the_account_garage_unavailable(user: User) -> None:
    FitmentScheme.objects.update(contains_special_category=True)
    plain = Client()
    plain.force_login(user)
    assert plain.get(reverse(PAGE)).status_code == 404
    assert plain.post(reverse("garage_reset")).status_code == 404


@pytest.mark.parametrize("language", ["en", "it", "de", "fr", "es"])
def test_the_page_renders_in_every_language(client: Client, user: User, language: str) -> None:
    GarageProfileFactory.create(user=user, node=_node(), is_active=True)
    with translation.override(language):
        client.cookies["django_language"] = language
        assert client.get(reverse(PAGE)).status_code == 200
