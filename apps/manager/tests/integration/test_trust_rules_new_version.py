"""TRU.1 part 2, slice 2: a new TrustRules version from the Manager, with a dry-run preview.

Configuration and the platform owner open the form; the preview writes nothing and shows the
vendor counts and the diff against the version in force; the confirmation writes version N+1
through the service (one audit entry, no extra one) behind the `operator_config` session level.
"""

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts import levels
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.manager import trust_edit_views as trust_views
from apps.trust import engine
from apps.trust.models import TrustRules
from apps.trust.services import RULES_CHANGED
from apps.trust.tests.factories import TrustRulesFactory

pytestmark = pytest.mark.django_db

NEW = "manager:platform:trust_rule_new"
LIST = "manager:platform:trust_rules"


def login(role: str, **kwargs: Any) -> Client:
    client = Client()
    verified_login(client, UserFactory.create(platform_role=role), **kwargs)
    return client


def stamp(delta: dt.timedelta) -> str:
    return (timezone.now() + delta).strftime("%Y-%m-%dT%H:%M")


def form(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "orders_tiers": "10, 50, 100",
        "min_order_value": "5.00",
        "withdrawal_window_days": 14,
        "orders_per_buyer_cap": 3,
        "orders_cap_window_days": 365,
        "reviews_stars_from": 5,
        "review_excerpt_length": 200,
        "countries_min_orders": 2,
        "countries_min_buyers": 2,
        "repeat_window_days": 730,
        "repeat_min_gap_days": 7,
        "repeat_shown_from": 3,
        "dispatch_window_days": 90,
        "dispatch_min_orders": 10,
        "dispatch_earn_percent": "92",
        "dispatch_lose_percent": "85",
        "dispute_window_days": 365,
        "dispute_min_orders": 20,
        "dispute_earn_percent": "3",
        "dispute_lose_percent": "4",
        "valid_from": stamp(dt.timedelta(hours=2)),
        "note": "Stricter dispatch",
    }
    data.update(changes)
    return data


@pytest.fixture
def current() -> TrustRules:
    now = timezone.now()
    return TrustRulesFactory.create(
        version=1, valid_from=now - dt.timedelta(days=30), valid_to=None
    )


def audits() -> int:
    return AuditLog.objects.filter(action=RULES_CHANGED).count()


@pytest.mark.parametrize("role", ["config", "owner"])
def test_the_list_offers_new_version_and_the_form_is_prefilled(role: str, current: Any) -> None:
    client = login(role)
    assert reverse(NEW) in client.get(reverse(LIST)).text
    page = client.get(reverse(NEW))
    assert page.status_code == 200
    body = page.text
    assert 'name="orders_tiers"' in body
    assert 'value="10, 50, 100, 500, 1000"' in body
    assert 'value="90"' in body, "the in-force percent, written without an exponent"
    assert "Dispatch on time" in body


@pytest.mark.parametrize("role", ["support", "catalog", "finance"])
def test_the_other_roles_get_404(role: str, current: Any) -> None:
    client = login(role)
    assert client.get(reverse(NEW)).status_code == 404
    assert client.post(reverse(NEW), form()).status_code == 404
    assert TrustRules.objects.count() == 1


def test_the_preview_writes_nothing_and_shows_counts_and_diff(
    current: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = timezone.now()
    monkeypatch.setattr(
        trust_views,
        "build_impact_inputs",
        lambda: [engine.VendorTrustInputs(vendor_ref=f"v{n}", as_of=now) for n in range(3)],
    )
    response = login("config").post(reverse(NEW), form())
    assert response.status_code == 200
    body = response.text
    assert TrustRules.objects.count() == 1
    assert audits() == 0
    assert "Vendors checked" in body
    assert "Version 2" in body
    for text in ("Earned at", "92%", "90%"):
        assert text in body, "the changed threshold"
    assert "Window of the per-buyer cap" not in body, "an unchanged threshold is not in the diff"
    assert 'name="confirm"' in body
    assert 'name="dispatch_earn_percent" value="92"' in body


def test_confirm_creates_the_next_version_with_audited_by_the_service(current: Any) -> None:
    client = login("owner")
    response = client.post(reverse(NEW), {**form(), "confirm": "1"})
    assert response.status_code == 302
    assert response["Location"] == reverse(LIST)
    new = TrustRules.objects.get(version=2)
    assert new.note == "Stricter dispatch"
    assert new.params["dispatch_earn_percent"] == "92"
    assert new.params["orders_tiers"] == [10, 50, 100]
    current.refresh_from_db()
    assert current.valid_to == new.valid_from
    # The service writes one entry per change (the version it closes, the new one) and the view
    # adds none of its own.
    entries = AuditLog.objects.filter(target_model="trust.trustrules")
    assert {e.action for e in entries} == {RULES_CHANGED}
    assert entries.count() == 2
    assert audits() == 2


def test_invalid_params_are_refused_with_field_errors(current: Any) -> None:
    client = login("config")
    for bad in (
        form(orders_tiers="50, 10"),
        form(dispatch_earn_percent="140"),
        form(orders_tiers="a, b"),
        form(withdrawal_window_days=""),
    ):
        for extra in ({}, {"confirm": "1"}):
            response = client.post(reverse(NEW), {**bad, **extra})
            assert response.status_code == 200
            assert "field--invalid" in response.text
    assert TrustRules.objects.count() == 1
    assert audits() == 0


def test_a_start_in_the_past_is_refused(current: Any) -> None:
    client = login("config")
    for extra in ({}, {"confirm": "1"}):
        data = {**form(valid_from=stamp(-dt.timedelta(days=2))), **extra}
        response = client.post(reverse(NEW), data)
        assert response.status_code == 200
        assert "cannot start in the past" in response.text
    assert TrustRules.objects.count() == 1


def test_the_post_needs_the_session_level(current: Any) -> None:
    client = login("config", level=levels.NO_WINDOW)
    assert client.get(reverse(NEW)).status_code == 200
    response = client.post(reverse(NEW), {**form(), "confirm": "1"})
    assert response.status_code != 404
    assert TrustRules.objects.count() == 1
    assert audits() == 0
    handler: Any = trust_views.TrustRuleNewView.post
    assert handler.session_action == "operator_config"


def test_the_preview_queries_do_not_grow_with_the_vendors(
    current: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The impact is computed in memory from the builder's inputs: no query per vendor."""
    now = timezone.now()
    size = [1]
    monkeypatch.setattr(
        trust_views,
        "build_impact_inputs",
        lambda: [engine.VendorTrustInputs(vendor_ref=f"v{n}", as_of=now) for n in range(size[0])],
    )
    client = login("config")
    client.post(reverse(NEW), form())  # warm caches
    with CaptureQueriesContext(connection) as few:
        client.post(reverse(NEW), form())
    size[0] = 50
    with CaptureQueriesContext(connection) as many:
        response = client.post(reverse(NEW), form())
    assert "50" in response.text
    assert len(many) == len(few)


def rome_stamp(moment: dt.datetime) -> str:
    return moment.astimezone(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%dT%H:%M")


def test_the_start_is_read_in_the_business_time_zone(current: Any) -> None:
    """00:30 in Rome on day D is 23:30 UTC on D-1 (winter, UTC+1)."""
    day = dt.date(timezone.now().year + 1, 1, 15)
    client = login("config")
    response = client.post(
        reverse(NEW), {**form(valid_from=f"{day:%Y-%m-%d}T00:30"), "confirm": "1"}
    )
    assert response.status_code == 302
    expected = dt.datetime.combine(day - dt.timedelta(days=1), dt.time(23, 30), tzinfo=dt.UTC)
    assert TrustRules.objects.get(version=2).valid_from == expected


def test_the_form_shows_the_start_back_in_rome_and_keeps_the_tolerance(current: Any) -> None:
    client = login("config")
    page = client.get(reverse(NEW))
    default = timezone.now() + dt.timedelta(minutes=5)
    assert f'value="{rome_stamp(default)}"' in page.text
    # The current minute, cut to :00, is under a minute old: the service's tolerance accepts it.
    ok = client.post(reverse(NEW), form(valid_from=rome_stamp(timezone.now())))
    assert "cannot start in the past" not in ok.text
    late = rome_stamp(timezone.now() - dt.timedelta(minutes=5))
    refused = client.post(reverse(NEW), form(valid_from=late))
    assert "cannot start in the past" in refused.text
