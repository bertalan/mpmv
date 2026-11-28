"""ING.14 2/3: the Manager page where the shop owner picks an AI provider and sets its key."""

import json
import secrets

import pytest
from django.test import Client

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.ingest.models import VendorAIProvider
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorMemberFactory

from .conftest import PICKER, SHOP

pytestmark = pytest.mark.django_db
URL = f"{SHOP}ai-provider/"


def _open(client: Client, user: User, vendor: Vendor, **kw: object) -> None:
    verified_login(client, user, **kw)  # type: ignore[arg-type]
    assert client.post(PICKER, {"vendor": str(vendor.public_id)}).status_code == 302


def test_the_owner_sets_a_key_and_it_is_never_shown_again(
    client: Client, owner: User, vendor: Vendor
) -> None:
    _open(client, owner, vendor)
    key = secrets.token_urlsafe(24)
    data = {"provider": "claude", "model": "m-1", "api_key": key}
    assert client.post(URL, data).status_code == 302
    assert VendorAIProvider.objects.get(vendor=vendor).api_key == key
    page = client.get(URL)
    assert page.status_code == 200
    assert key.encode() not in page.content
    assert key not in str(page.context["form"].initial)
    assert b'type="password"' in page.content
    audit = json.dumps(list(AuditLog.objects.values_list("before", "after")), default=str)
    assert key not in audit


def test_an_invalid_post_does_not_echo_the_key(client: Client, owner: User, vendor: Vendor) -> None:
    _open(client, owner, vendor)
    key = secrets.token_urlsafe(24)
    response = client.post(URL, {"provider": "nope", "model": "m-1", "api_key": key})
    assert response.status_code == 200
    assert key.encode() not in response.content
    assert not VendorAIProvider.objects.exists()


def test_without_a_passkey_window_the_post_is_refused(
    client: Client, owner: User, vendor: Vendor
) -> None:
    _open(client, owner, vendor, level=levels.NO_WINDOW)
    data = {"provider": "claude", "model": "m-1", "api_key": secrets.token_urlsafe(24)}
    assert client.post(URL, data).status_code == 403
    assert not VendorAIProvider.objects.exists()


def test_a_shop_staff_member_gets_404(client: Client, vendor: Vendor) -> None:
    member = VendorMemberFactory.create(vendor=vendor, role="staff").user
    _open(client, member, vendor)
    assert client.get(URL).status_code == 404
    assert client.post(URL, {"provider": "claude", "model": "m-1"}).status_code == 404


def test_the_page_of_one_shop_never_shows_another_shops_choice(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor
) -> None:
    VendorAIProvider.objects.create(vendor=other_vendor, provider="openai", model="other-model")
    _open(client, owner, vendor)
    assert b"other-model" not in client.get(URL).content
