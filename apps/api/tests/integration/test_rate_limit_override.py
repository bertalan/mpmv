"""CORE.16 done-when: without configuration a key gets 429 on its 121st call in a minute; a staff
override raises one key's limit without affecting another key; the override change is refused
without step-up and is in the AuditLog.

Precedence of a key's key-wide limit: the override stored on the `ApiClient` (set by staff), then
`API_RATE_LIMIT_CLIENTS`, then `API_RATE_LIMIT`. A key with its own limit (either of the first
two) is capped on scopes with their own limit too: such a call counts on the scope counter and on
the key-wide one.
"""

import datetime as dt
from collections.abc import Callable
from urllib.parse import urlsplit

import pytest
from django.http.response import HttpResponseBase
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.api.engine import InvalidRateLimit
from apps.api.models import ApiClient
from apps.api.services import count_api_call, set_api_client_rate_limit
from apps.api.tests.factories import ApiClientFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

NOON = dt.datetime(2026, 11, 8, 12, 0, tzinfo=dt.UTC)
CTX = AuditContext(actor_id=1, actor_label="staff:ops", source="admin", ip="127.0.0.1")
ACTION = "api.client.rate_limit_changed"


def calls(api_client: ApiClient, count: int, scope: str | None = None) -> list[bool]:
    return [count_api_call(api_client, scope, now=NOON).allowed for _ in range(count)]


# --- Default and override (service)


def test_without_configuration_the_121st_call_in_a_minute_is_refused() -> None:
    api_client = ApiClientFactory.create()
    allowed = calls(api_client, 121)
    assert allowed[:120] == [True] * 120
    assert allowed[120] is False
    refused = count_api_call(api_client, None, now=NOON + dt.timedelta(seconds=10))
    assert (refused.limit, refused.retry_after) == (120, 50)


def test_a_staff_override_raises_one_keys_limit_without_affecting_another(
    settings: SettingsWrapper,
) -> None:
    settings.API_RATE_LIMIT = "2/minute"
    raised, other = ApiClientFactory.create_batch(2)
    set_api_client_rate_limit(raised, "5/minute", ctx=CTX)
    assert calls(raised, 6) == [True] * 5 + [False]
    assert calls(other, 3) == [True, True, False]


def test_the_override_wins_over_the_client_setting(settings: SettingsWrapper) -> None:
    api_client = ApiClientFactory.create(rate_limit="1/minute")
    settings.API_RATE_LIMIT_CLIENTS = {str(api_client.public_id): "10/minute"}
    assert calls(api_client, 2) == [True, False]


def test_clearing_the_override_falls_back_to_the_settings(settings: SettingsWrapper) -> None:
    settings.API_RATE_LIMIT = "2/minute"
    api_client = ApiClientFactory.create(rate_limit="1/minute")
    set_api_client_rate_limit(api_client, "", ctx=CTX)
    api_client.refresh_from_db()
    assert api_client.rate_limit == ""
    assert calls(api_client, 3) == [True, True, False]


def test_a_lowered_key_is_capped_on_a_scope_with_its_own_limit(settings: SettingsWrapper) -> None:
    """The #102 review finding: a capped key must stay capped on a costly scope."""
    settings.API_RATE_LIMIT_SCOPES = {"catalog:write": "100/minute"}
    capped, other = ApiClientFactory.create_batch(2)
    set_api_client_rate_limit(capped, "2/minute", ctx=CTX)
    assert calls(capped, 3, "catalog:write") == [True, True, False]
    assert calls(capped, 1, "catalog:read") == [False]
    assert calls(other, 3, "catalog:write") == [True, True, True]


def test_a_raised_key_keeps_the_scope_limit(settings: SettingsWrapper) -> None:
    settings.API_RATE_LIMIT_SCOPES = {"catalog:write": "1/minute"}
    raised = ApiClientFactory.create(rate_limit="1000/minute")
    assert calls(raised, 2, "catalog:write") == [True, False]
    assert calls(raised, 1, None) == [True]


def test_a_key_capped_by_the_client_setting_is_capped_on_scopes_too(
    settings: SettingsWrapper,
) -> None:
    capped = ApiClientFactory.create()
    settings.API_RATE_LIMIT_SCOPES = {"catalog:write": "100/minute"}
    settings.API_RATE_LIMIT_CLIENTS = {str(capped.public_id): "1/minute"}
    assert calls(capped, 2, "catalog:write") == [True, False]


# --- The override service: audit, dry run, validation


def test_the_override_change_is_in_the_audit_log() -> None:
    api_client = ApiClientFactory.create(vendor_key=True)
    assert api_client.vendor is not None
    set_api_client_rate_limit(api_client, "600/minute", ctx=CTX)
    entry = AuditLog.objects.get(action=ACTION)
    assert entry.target_key == str(api_client.public_id)
    assert entry.vendor_key == str(api_client.vendor.public_id)
    assert entry.actor_label == "staff:ops"
    assert entry.before is not None
    assert entry.after is not None
    assert (entry.before["rate_limit"], entry.after["rate_limit"]) == ("", "600/minute")


def test_a_dry_run_changes_nothing() -> None:
    api_client = ApiClientFactory.create()
    changeset = set_api_client_rate_limit(api_client, "600/minute", ctx=CTX, dry_run=True)
    assert not changeset.is_empty
    api_client.refresh_from_db()
    assert api_client.rate_limit == ""
    assert not AuditLog.objects.filter(action=ACTION).exists()


def test_setting_the_same_override_again_changes_nothing() -> None:
    api_client = ApiClientFactory.create(rate_limit="600/minute")
    assert set_api_client_rate_limit(api_client, "600/minute", ctx=CTX).is_empty
    assert not AuditLog.objects.filter(action=ACTION).exists()


@pytest.mark.parametrize("rate", ["lots", "0/minute", "10/fortnight"])
def test_an_invalid_override_is_refused(rate: str) -> None:
    api_client = ApiClientFactory.create()
    with pytest.raises(InvalidRateLimit):
        set_api_client_rate_limit(api_client, rate, ctx=CTX)
    api_client.refresh_from_db()
    assert api_client.rate_limit == ""


# --- Admin: staff set the override behind a fresh second factor


def rate_limit_url(api_client: ApiClient) -> str:
    return reverse("admin:api_apiclient_rate_limit", args=[api_client.pk])


def asks_for_step_up(response: HttpResponseBase) -> bool:
    if response.status_code != 302:
        return False
    return urlsplit(response["Location"]).path == reverse("mfa:verify")


@pytest.fixture
def staff_client(
    client: Client, superuser: User, mfa_login: Callable[[Client, User], Client]
) -> Client:
    return mfa_login(client, superuser)


def test_staff_set_the_override_from_the_admin(staff_client: Client) -> None:
    api_client = ApiClientFactory.create()
    page = staff_client.get(rate_limit_url(api_client))
    assert page.status_code == 200
    response = staff_client.post(rate_limit_url(api_client), {"rate_limit": "600/minute"})
    assert response.status_code == 302
    api_client.refresh_from_db()
    assert api_client.rate_limit == "600/minute"
    entry = AuditLog.objects.get(action=ACTION)
    assert entry.actor_label == "staff:staff@example.com"


def test_the_detail_page_shows_the_effective_limit_and_links_the_change_page(
    staff_client: Client,
) -> None:
    api_client = ApiClientFactory.create(rate_limit="600/minute")
    url = reverse("admin:api_apiclient_change", args=[api_client.pk])
    body = staff_client.get(url).content.decode()
    assert "600/minute" in body
    assert rate_limit_url(api_client) in body


def test_an_invalid_rate_is_shown_back_and_changes_nothing(staff_client: Client) -> None:
    api_client = ApiClientFactory.create()
    response = staff_client.post(rate_limit_url(api_client), {"rate_limit": "lots"})
    assert response.status_code == 200
    assert "is not a rate" in response.content.decode()
    api_client.refresh_from_db()
    assert api_client.rate_limit == ""


def test_the_override_change_is_refused_without_step_up(
    staff_client: Client, settings: SettingsWrapper
) -> None:
    settings.MFA_STEP_UP_MAX_AGE = -1  # no verification counts as fresh
    api_client = ApiClientFactory.create()
    assert asks_for_step_up(staff_client.get(rate_limit_url(api_client)))
    response = staff_client.post(rate_limit_url(api_client), {"rate_limit": "600/minute"})
    assert asks_for_step_up(response)
    api_client.refresh_from_db()
    assert api_client.rate_limit == ""
    assert not AuditLog.objects.filter(action=ACTION).exists()


def test_staff_without_the_change_permission_cannot_set_the_override(
    client: Client, mfa_login: Callable[[Client, User], Client]
) -> None:
    viewer = UserFactory.create(staff=True, permissions=["api.view_apiclient"])
    mfa_login(client, viewer)
    api_client = ApiClientFactory.create()
    response = client.post(rate_limit_url(api_client), {"rate_limit": "600/minute"})
    assert response.status_code == 403
    api_client.refresh_from_db()
    assert api_client.rate_limit == ""
