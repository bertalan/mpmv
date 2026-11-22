"""FIT.6: the anonymous garage is staged at sign-in and merged only when the customer adds it."""

from typing import Any
from unittest import mock

import pytest
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory

from apps.accounts.tests.factories import UserFactory
from apps.core.models import AuditLog
from apps.fitment import garage, garage_merge, services
from apps.fitment.middleware import GarageSignOutMiddleware
from apps.fitment.models import FitmentNode, FitmentScheme, GarageProfile
from apps.fitment.tests.factories import (
    FitmentNodeFactory,
    GarageProfileFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def catalogue() -> None:
    make = FitmentNodeFactory.create(name="Acme")
    for name in ("Roadster", "Tourer", "Racer", "Hauler"):
        FitmentNodeFactory.create(name=name, parent=make)


def cookie_of(*models: str, active: int = 0) -> str:
    vehicles = tuple(garage.Vehicle("Acme", m, "") for m in models)
    return garage.dumps(garage.Garage(vehicles, active))


def sign_in(user: Any, value: str | None) -> HttpRequest:
    """The request of a sign-in carrying the cookie; the `user_logged_in` signal runs on it."""
    request = RequestFactory().post("/sign-in/")
    request.session = SessionStore()
    request.user = user
    request.COOKIES = {garage.COOKIE_NAME: value} if value is not None else {}
    user_logged_in.send(sender=type(user), request=request, user=user)
    return request


def merge(request: HttpRequest, user: Any) -> garage_merge.MergeResult:
    return garage_merge.merge_pending(request, user)


def test_sign_in_stages_the_vehicles_and_adds_nothing() -> None:
    user = UserFactory.create()
    client = sign_in(user, cookie_of("Roadster", "Tourer"))
    assert GarageProfile.objects.filter(user=user).count() == 0
    assert len(client.session[garage_merge.SESSION_KEY]["vehicles"]) == 2


def test_add_creates_the_profiles_and_the_cookie_is_cleared() -> None:
    user = UserFactory.create()
    client = sign_in(user, cookie_of("Roadster", "Tourer", active=1))
    result = merge(client, user)
    assert (result.added, result.skipped) == (2, 0)
    names = set(GarageProfile.objects.filter(user=user).values_list("name", flat=True))
    assert names == {"Acme Roadster", "Acme Tourer"}
    response = garage_merge.clear_cookie(HttpResponse())
    assert response.cookies[garage.COOKIE_NAME]["max-age"] == 0


def test_the_cookies_active_vehicle_becomes_active_when_the_account_has_none() -> None:
    user = UserFactory.create()
    client = sign_in(user, cookie_of("Roadster", "Tourer", active=1))
    merge(client, user)
    active = GarageProfile.objects.get(user=user, is_active=True)
    assert active.name == "Acme Tourer"


def test_the_accounts_own_active_profile_stays_active() -> None:
    user = UserFactory.create()
    mine = GarageProfileFactory.create(user=user, is_active=True)
    client = sign_in(user, cookie_of("Roadster"))
    merge(client, user)
    mine.refresh_from_db()
    assert mine.is_active
    assert GarageProfile.objects.filter(user=user, is_active=True).count() == 1


def test_a_vehicle_the_account_already_has_is_not_staged_nor_recreated() -> None:
    user = UserFactory.create()
    existing = FitmentNode.objects.get(name="Roadster")
    GarageProfileFactory.create(user=user, node=existing, year=None)
    client = sign_in(user, cookie_of("Roadster"))
    assert garage_merge.SESSION_KEY not in client.session
    client = sign_in(user, cookie_of("Roadster", "Tourer"))
    result = merge(client, user)
    assert result.added == 1
    assert GarageProfile.objects.filter(user=user).count() == 2


def test_the_profile_limit_is_respected() -> None:
    user = UserFactory.create()
    FitmentScheme.objects.update(max_profiles=2)
    GarageProfileFactory.create(user=user)
    client = sign_in(user, cookie_of("Roadster", "Tourer", "Racer"))
    result = merge(client, user)
    assert (result.added, result.skipped) == (1, 2)
    assert GarageProfile.objects.filter(user=user).count() == 2


def test_not_now_keeps_the_cookie_and_merges_nothing() -> None:
    user = UserFactory.create()
    client = sign_in(user, cookie_of("Roadster"))
    request = client
    assert garage_merge.pending_count(request) == 1
    garage_merge.dismiss_pending(request)
    assert garage_merge.pending_count(request) == 0
    assert GarageProfile.objects.filter(user=user).count() == 0
    # the next sign-in asks again
    client = sign_in(user, cookie_of("Roadster"))
    assert len(client.session[garage_merge.SESSION_KEY]["vehicles"]) == 1


@pytest.mark.parametrize(
    "value",
    [
        "not-base64!",
        "e30=",
        "A" * 3000,
        garage.dumps(garage.Garage((garage.Vehicle("Nobody", "X"),), 0)),
    ],
)
def test_a_hostile_or_unknown_cookie_is_ignored(value: str) -> None:
    user = UserFactory.create()
    client = sign_in(user, value)
    assert garage_merge.SESSION_KEY not in client.session
    assert GarageProfile.objects.filter(user=user).count() == 0


def test_the_special_category_switch_blocks_the_merge() -> None:
    user = UserFactory.create()
    scheme = services.current_scheme()
    scheme.contains_special_category = True
    scheme.save()
    client = sign_in(user, cookie_of("Roadster"))
    assert garage_merge.SESSION_KEY not in client.session
    assert GarageProfile.objects.filter(user=user).count() == 0


def test_the_audit_summary_holds_counts_and_no_vehicle_name() -> None:
    user = UserFactory.create()
    client = sign_in(user, cookie_of("Roadster", "Tourer"))
    merge(client, user)
    entry = AuditLog.objects.get(action=garage_merge.MERGED)
    assert entry.after is not None
    assert entry.after["added"] == 2
    assert "Roadster" not in str(entry.after) + str(entry.target_key)


def test_sign_in_succeeds_when_staging_raises(caplog: pytest.LogCaptureFixture) -> None:
    user = UserFactory.create()
    with mock.patch.object(garage_merge, "_new_vehicles", side_effect=RuntimeError("boom")):
        client = sign_in(user, cookie_of("Roadster"))
    assert garage_merge.SESSION_KEY not in client.session
    assert "Could not stage" in caplog.text


def test_dry_run_writes_nothing_and_keeps_the_session_entry() -> None:
    user = UserFactory.create()
    request = sign_in(user, cookie_of("Roadster", "Tourer"))
    result = garage_merge.merge_pending(request, user, dry_run=True)
    assert (result.added, result.skipped) == (2, 0)
    assert [c.op for c in result.changes.changes].count("create") == 2
    assert GarageProfile.objects.filter(user=user).count() == 0
    assert not AuditLog.objects.filter(action=garage_merge.MERGED).exists()
    assert garage_merge.pending_count(request) == 2
    # the real run then gives the same count
    assert merge(request, user).added == 2


def test_dry_run_respects_the_profile_limit() -> None:
    user = UserFactory.create()
    FitmentScheme.objects.update(max_profiles=1)
    request = sign_in(user, cookie_of("Roadster", "Tourer"))
    assert garage_merge.merge_pending(request, user, dry_run=True).added == 1


def sign_out(user: Any, value: str | None) -> HttpResponse:
    """A sign-out request carrying the cookie, through the signal and the middleware."""
    request = RequestFactory().post("/sign-out/")
    request.COOKIES = {garage.COOKIE_NAME: value} if value is not None else {}

    def view(request: HttpRequest) -> HttpResponse:
        user_logged_out.send(sender=type(user), request=request, user=user)
        return HttpResponse()

    return GarageSignOutMiddleware(view)(request)


def test_sign_out_clears_the_cookie_made_from_the_active_profile() -> None:
    user = UserFactory.create()
    node = FitmentNode.objects.get(name="Roadster")
    GarageProfileFactory.create(user=user, node=node, year=None, is_active=True)
    response = sign_out(user, cookie_of("Roadster"))
    assert response.cookies[garage.COOKIE_NAME]["max-age"] == 0


@pytest.mark.parametrize("models", [("Tourer",), ("Roadster", "Tourer")])
def test_sign_out_keeps_a_cookie_that_is_not_the_active_profile(models: tuple[str, ...]) -> None:
    user = UserFactory.create()
    node = FitmentNode.objects.get(name="Roadster")
    GarageProfileFactory.create(user=user, node=node, year=None, is_active=True)
    assert garage.COOKIE_NAME not in sign_out(user, cookie_of(*models)).cookies


def test_sign_out_keeps_the_cookie_without_an_active_profile_or_cookie() -> None:
    user = UserFactory.create()
    assert garage.COOKIE_NAME not in sign_out(user, cookie_of("Roadster")).cookies
    assert garage.COOKIE_NAME not in sign_out(user, None).cookies


def test_sign_out_succeeds_when_the_check_raises(caplog: pytest.LogCaptureFixture) -> None:
    user = UserFactory.create()
    value = cookie_of("Roadster")
    with mock.patch.object(services, "read_garage", side_effect=RuntimeError("boom")):
        response = sign_out(user, value)
    assert response.status_code == 200
    assert "Could not check" in caplog.text
    assert value not in caplog.text
