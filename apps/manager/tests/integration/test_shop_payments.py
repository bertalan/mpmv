"""VEN.6: the Payments page of the shop space (owner only), with the provider client faked."""

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.payments.models import ConnectedAccount
from apps.payments.tests.factories import ConnectedAccountFactory
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorMemberFactory

from .conftest import SHOP

pytestmark = pytest.mark.django_db

PAGE = f"{SHOP}payments/"


@pytest.fixture(autouse=True)
def stripe(monkeypatch: pytest.MonkeyPatch) -> FakeStripeClient:
    fake = FakeStripeClient()
    monkeypatch.setattr("apps.manager.views.client_from_settings", lambda: fake)
    return fake


def test_the_owner_connects_and_is_sent_to_the_provider(
    client: Client, owner: User, vendor: Vendor, stripe: FakeStripeClient
) -> None:
    verified_login(client, owner)
    assert "On request" in client.get(PAGE).text or "Not connected" in client.get(PAGE).text
    response = client.post(PAGE)
    assert response.status_code == 302
    assert response["Location"] == stripe.links[0]
    account = ConnectedAccount.objects.get(vendor=vendor)
    assert account.account_ref in response["Location"]


def test_the_return_from_the_provider_refreshes_the_own_account(
    client: Client, owner: User, vendor: Vendor, stripe: FakeStripeClient
) -> None:
    verified_login(client, owner)
    client.post(PAGE)
    account = ConnectedAccount.objects.get(vendor=vendor)
    from apps.payments.stripe_client import StripeAccount

    stripe.accounts[account.account_ref] = StripeAccount(
        id=account.account_ref, details_submitted=True, charges_enabled=True, payouts_enabled=True
    )
    assert client.get(f"{PAGE}?return=1").status_code == 302
    account.refresh_from_db()
    assert account.is_active
    vendor.refresh_from_db()
    assert vendor.sales_mode_default == Vendor.SalesMode.BUY


def test_staff_get_a_404_and_start_nothing(
    client: Client, vendor: Vendor, stripe: FakeStripeClient
) -> None:
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    verified_login(client, staff)
    assert client.get(PAGE).status_code == 404
    assert client.post(PAGE).status_code == 404
    assert stripe.calls == []


def test_a_vendor_never_sees_or_uses_another_vendors_account(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor, stripe: FakeStripeClient
) -> None:
    theirs = ConnectedAccountFactory.create(vendor=other_vendor)
    verified_login(client, owner)
    assert theirs.account_ref not in client.get(PAGE).text
    client.post(PAGE)
    assert ConnectedAccount.objects.get(vendor=other_vendor).pk == theirs.pk
    assert ConnectedAccount.objects.get(vendor=vendor).pk != theirs.pk
    assert theirs.account_ref not in stripe.accounts


def test_anonymous_visitors_are_refused(client: Client) -> None:
    assert client.get(PAGE).status_code in (302, 404)


def test_a_non_member_is_refused(client: Client) -> None:
    verified_login(client, UserFactory.create())
    assert client.get(PAGE).status_code in (302, 403, 404)
