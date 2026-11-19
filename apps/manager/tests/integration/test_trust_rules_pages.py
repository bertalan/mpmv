"""TRU.1 part 2, slice 1: the trust rules versions in the Manager, read only.

Configuration and the platform owner open the list and the detail page; Support, Catalog and
Finance get 404, and so does a vendor member. The pages issue a bounded number of queries.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.manager import roles
from apps.trust import engine
from apps.trust.tests.factories import TrustRulesFactory
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db

LIST = "manager:platform:trust_rules"
DETAIL = "manager:platform:trust_rule"
NOW = dt.datetime.now(dt.UTC)


def login(role: str) -> Client:
    user = UserFactory.create(platform_role=role)
    client = Client()
    verified_login(client, user)
    return client


@pytest.fixture
def versions() -> None:
    """Version 1 closed, version 2 in force with changed thresholds and a note."""
    TrustRulesFactory.create(
        version=1, valid_from=NOW - dt.timedelta(days=60), valid_to=NOW - dt.timedelta(days=10)
    )
    params = engine.rules_to_params(engine.TrustRulesData(version=2))
    params.update(orders_tiers=[5, 20], dispatch_earn_percent="95", withdrawal_window_days=21)
    TrustRulesFactory.create(
        version=2,
        params=params,
        valid_from=NOW - dt.timedelta(days=10),
        valid_to=None,
        note="Stricter dispatch",
    )


@pytest.mark.parametrize("role", ["config", "owner"])
def test_config_and_owner_open_the_list(role: str, versions: None) -> None:
    page = login(role).get(reverse(LIST))
    assert page.status_code == 200
    body = page.text
    assert "Stricter dispatch" in body
    assert body.index("Version 2") < body.index("Version 1"), "newest version first"
    assert body.count("In force") == 1, "exactly one version is marked in force"
    assert "Open" in body


@pytest.mark.parametrize("role", ["config", "owner"])
def test_the_detail_shows_readable_labels_and_values(role: str, versions: None) -> None:
    page = login(role).get(reverse(DETAIL, args=[2]))
    assert page.status_code == 200
    body = page.text
    for text in ("Order count tiers", "5, 20", "95%", "21 days", "Dispatch on time"):
        assert text in body, f"the detail page must show {text!r}"
    assert "orders_tiers" not in body.replace('data-param="orders_tiers"', "")
    assert "In force" in body
    closed = login(role).get(reverse(DETAIL, args=[1]))
    assert "Not in force" in closed.text


def test_a_missing_version_is_404(versions: None) -> None:
    assert login("config").get(reverse(DETAIL, args=[99])).status_code == 404


@pytest.mark.parametrize("role", ["support", "catalog", "finance"])
def test_the_other_roles_get_404(role: str, versions: None) -> None:
    client = login(role)
    assert client.get(reverse(LIST)).status_code == 404
    assert client.get(reverse(DETAIL, args=[1])).status_code == 404


def test_a_vendor_member_is_refused(versions: None) -> None:
    member = VendorMemberFactory.create().user
    client = Client()
    verified_login(client, member)
    assert client.get(reverse(LIST)).status_code in {403, 404}


def test_anonymous_is_refused(versions: None) -> None:
    assert Client().get(reverse(LIST)).status_code in {302, 403, 404}


def test_the_matrix_row() -> None:
    row = roles.MATRIX["platform.trust_rules"]
    assert row == {roles.CONFIG: roles.E, roles.OWNER: roles.E}


def test_the_platform_nav_links_the_page(versions: None) -> None:
    page = login("config").get(reverse("manager:platform:dashboard"))
    assert reverse(LIST) in page.text


def test_the_list_query_count_does_not_grow_with_versions() -> None:
    client = login("config")
    TrustRulesFactory.create_batch(2)
    client.get(reverse(LIST))  # warm caches
    with CaptureQueriesContext(connection) as few:
        client.get(reverse(LIST))
    TrustRulesFactory.create_batch(6)
    with CaptureQueriesContext(connection) as many:
        client.get(reverse(LIST))
    assert len(many) == len(few)


def test_the_detail_shows_default_for_an_omitted_parameter() -> None:
    TrustRulesFactory.create(
        version=1, params={}, valid_to=None, valid_from=NOW - dt.timedelta(days=1)
    )
    body = login("owner").get(reverse(DETAIL, args=[1])).text
    assert "10, 50, 100, 500, 1000" in body
    assert f"{Decimal('5.00')}" in body
