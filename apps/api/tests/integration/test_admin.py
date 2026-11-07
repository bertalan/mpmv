"""CORE.13: the staff admin lists API clients by vendor and revokes them through the service.

Read-only: keys are issued by `issue_api_client` (the full key is shown once), and the admin
never shows the key hash. Revocation is the audited `revoke_api_client`.
"""

from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.api.models import ApiClient
from apps.api.tests.factories import ApiClientFactory
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:api_apiclient_changelist"


@pytest.fixture
def staff_client(
    client: Client, superuser: User, mfa_login: Callable[[Client, User], Client]
) -> Client:
    return mfa_login(client, superuser)


def test_the_changelist_shows_each_clients_vendor(staff_client: Client) -> None:
    vendor_key = ApiClientFactory.create(name="Vendor feed", vendor_key=True)
    ApiClientFactory.create(name="Platform pipeline")
    assert vendor_key.vendor is not None

    body = staff_client.get(reverse(CHANGELIST)).content.decode()

    assert "Vendor feed" in body
    assert "Platform pipeline" in body
    assert vendor_key.vendor.display_name in body


def test_the_changelist_filters_platform_and_vendor_keys(staff_client: Client) -> None:
    ApiClientFactory.create(name="Vendor feed", vendor_key=True)
    ApiClientFactory.create(name="Platform pipeline")

    platform = staff_client.get(reverse(CHANGELIST), {"vendor__isnull": "True"}).content.decode()
    assert "Platform pipeline" in platform
    assert "Vendor feed" not in platform


def test_the_detail_page_never_shows_the_key_hash(staff_client: Client) -> None:
    client = ApiClientFactory.create(vendor_key=True)
    url = reverse("admin:api_apiclient_change", args=[client.pk])
    response = staff_client.get(url)
    assert response.status_code == 200
    assert client.key_hash not in response.content.decode()


def test_the_admin_cannot_add_or_edit_clients(staff_client: Client) -> None:
    client = ApiClientFactory.create(name="Feed")
    assert staff_client.get(reverse("admin:api_apiclient_add")).status_code == 403
    url = reverse("admin:api_apiclient_change", args=[client.pk])
    staff_client.post(url, {"name": "Renamed"})
    client.refresh_from_db()
    assert client.name == "Feed"


def test_the_revoke_action_revokes_through_the_audited_service(staff_client: Client) -> None:
    client = ApiClientFactory.create(vendor_key=True)
    assert client.vendor is not None
    staff_client.post(reverse(CHANGELIST), {"action": "revoke", "_selected_action": [client.pk]})

    client.refresh_from_db()
    assert not client.is_active
    entry = AuditLog.objects.get(action="api.client.revoked")
    assert entry.vendor_key == str(client.vendor.public_id)
    assert entry.actor_label == "staff:staff@example.com"


def test_view_only_staff_cannot_revoke(client: Client) -> None:
    target = ApiClientFactory.create()
    viewer = UserFactory.create(staff=True, permissions=["api.view_apiclient"])
    verified_login(client, viewer)

    client.post(reverse(CHANGELIST), {"action": "revoke", "_selected_action": [target.pk]})

    assert ApiClient.objects.get(pk=target.pk).is_active
