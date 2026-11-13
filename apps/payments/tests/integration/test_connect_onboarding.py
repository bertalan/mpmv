"""VEN.6: Stripe Express onboarding, the account.updated webhook and the D-19 on-request mode."""

import json
import secrets
import time
from collections.abc import Iterator
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.payments import connect, registry, stripe_engine
from apps.payments.models import ConnectedAccount
from apps.payments.stripe_client import StripeAccount
from apps.payments.stripe_provider import StripePaymentProvider
from apps.payments.tests.factories import ConnectedAccountFactory
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

SECRET = secrets.token_urlsafe(24)
REFRESH = "https://shop.example.test/p/"
RETURN = "https://shop.example.test/p/?return=1"


@pytest.fixture
def ctx() -> AuditContext:
    return AuditContext(actor_label="test", source=AuditLog.Source.VENDOR)


@pytest.fixture
def stripe() -> FakeStripeClient:
    return FakeStripeClient()


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create(sales_mode_default=Vendor.SalesMode.BUY)


@pytest.fixture
def owner(vendor: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor, role="owner").user


def start(
    vendor: Vendor, actor: User, stripe: FakeStripeClient, ctx: AuditContext, **kw: Any
) -> Any:
    return connect.start_onboarding(
        vendor, actor=actor, client=stripe, ctx=ctx, refresh_url=REFRESH, return_url=RETURN, **kw
    )


def activate(stripe: FakeStripeClient, ref: str, **fields: Any) -> StripeAccount:
    state = {"details_submitted": True, "charges_enabled": True, "payouts_enabled": True}
    account = StripeAccount(id=ref, **{**state, **fields})
    stripe.accounts[ref] = account
    return account


# --- Account creation and link


def test_the_owner_gets_an_express_account_and_a_link(
    vendor: Vendor, owner: User, stripe: FakeStripeClient, ctx: AuditContext
) -> None:
    result = start(vendor, owner, stripe, ctx)
    account = ConnectedAccount.objects.get(vendor=vendor)
    assert account.account_ref in stripe.accounts
    assert result.url.startswith("https://")
    assert account.account_ref in result.url
    assert (account.status, account.provider) == ("pending", "stripe")
    assert stripe.account_calls[0]["metadata"] == {"vendor": str(vendor.public_id)}
    assert AuditLog.objects.filter(action="payments.connected_account.created").count() == 1


def test_a_second_start_reuses_the_account(
    vendor: Vendor, owner: User, stripe: FakeStripeClient, ctx: AuditContext
) -> None:
    start(vendor, owner, stripe, ctx)
    start(vendor, owner, stripe, ctx)
    assert ConnectedAccount.objects.filter(vendor=vendor).count() == 1
    assert len(stripe.accounts) == 1
    assert len(stripe.links) == 2


def test_a_dry_run_calls_nothing_and_stores_nothing(
    vendor: Vendor, owner: User, stripe: FakeStripeClient, ctx: AuditContext
) -> None:
    result = start(vendor, owner, stripe, ctx, dry_run=True)
    assert result.changes.summary()["create"] == 1
    assert stripe.calls == []
    assert not ConnectedAccount.objects.exists()
    assert not AuditLog.objects.filter(action__startswith="payments.connected_account").exists()


def test_staff_and_strangers_cannot_onboard(
    vendor: Vendor, stripe: FakeStripeClient, ctx: AuditContext
) -> None:
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    stranger = VendorMemberFactory.create(role="owner").user  # owner of another vendor
    for actor in (staff, stranger):
        with pytest.raises(PermissionDenied):
            start(vendor, actor, stripe, ctx)
    assert stripe.calls == []
    assert not ConnectedAccount.objects.exists()


def test_a_suspended_vendor_cannot_onboard(
    vendor: Vendor, owner: User, stripe: FakeStripeClient, ctx: AuditContext
) -> None:
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.SUSPENDED)
    vendor.refresh_from_db()
    with pytest.raises(PermissionDenied):
        start(vendor, owner, stripe, ctx)


# --- On request until connected (D-19) and the automatic switch


def test_starting_the_onboarding_puts_the_vendor_on_request(
    vendor: Vendor, owner: User, stripe: FakeStripeClient, ctx: AuditContext
) -> None:
    start(vendor, owner, stripe, ctx)
    vendor.refresh_from_db()
    assert vendor.sales_mode_default == Vendor.SalesMode.ON_REQUEST
    assert ConnectedAccount.objects.get(vendor=vendor).on_request_forced is True
    assert AuditLog.objects.filter(action="payments.vendor_sales_mode.changed").count() == 1


def test_the_vendor_switches_to_sale_when_the_account_becomes_active(
    vendor: Vendor, owner: User, stripe: FakeStripeClient, ctx: AuditContext
) -> None:
    start(vendor, owner, stripe, ctx)
    account = ConnectedAccount.objects.get(vendor=vendor)
    connect.apply_account_state(account, activate(stripe, account.account_ref), ctx=ctx)
    vendor.refresh_from_db()
    account.refresh_from_db()
    assert account.status == "active"
    assert vendor.sales_mode_default == Vendor.SalesMode.BUY
    assert account.on_request_forced is False
    switches = AuditLog.objects.filter(action="payments.vendor_sales_mode.changed")
    assert switches.count() == 2  # to on request, then back to sale


def test_losing_the_account_puts_the_vendor_back_on_request(
    vendor: Vendor, owner: User, stripe: FakeStripeClient, ctx: AuditContext
) -> None:
    start(vendor, owner, stripe, ctx)
    account = ConnectedAccount.objects.get(vendor=vendor)
    connect.apply_account_state(account, activate(stripe, account.account_ref), ctx=ctx)
    state = StripeAccount(id=account.account_ref, details_submitted=True, charges_enabled=True)
    connect.apply_account_state(account, state, ctx=ctx)
    vendor.refresh_from_db()
    assert account.status == "restricted"
    assert vendor.sales_mode_default == Vendor.SalesMode.ON_REQUEST


def test_a_vendor_that_chose_on_request_is_not_switched_to_sale(ctx: AuditContext) -> None:
    vendor = VendorFactory.create(sales_mode_default=Vendor.SalesMode.ON_REQUEST)
    account = ConnectedAccountFactory.create(vendor=vendor)
    state = StripeAccount(
        id=account.account_ref, details_submitted=True, charges_enabled=True, payouts_enabled=True
    )
    connect.apply_account_state(account, state, ctx=ctx)
    vendor.refresh_from_db()
    assert vendor.sales_mode_default == Vendor.SalesMode.ON_REQUEST


# --- Refresh on return and the webhook


def test_refresh_mirrors_the_owners_own_account(
    vendor: Vendor, owner: User, stripe: FakeStripeClient, ctx: AuditContext
) -> None:
    start(vendor, owner, stripe, ctx)
    ref = ConnectedAccount.objects.get(vendor=vendor).account_ref
    activate(stripe, ref)
    connect.refresh_account(vendor, actor=owner, client=stripe, ctx=ctx)
    assert ConnectedAccount.objects.get(vendor=vendor).is_active


@pytest.fixture
def webhook(settings: Any) -> Iterator[None]:
    settings.PAYMENT_PROVIDER = "stripe"
    registry.unregister_provider("stripe")
    registry.register_provider(
        lambda: StripePaymentProvider(FakeStripeClient(), webhook_secret=SECRET)
    )
    yield
    registry.unregister_provider("stripe")
    from apps.payments.stripe_provider import provider_from_settings

    registry.register_provider(provider_from_settings)


def post_event(
    client: Client, event_id: str, obj: dict[str, Any], kind: str = "account.updated"
) -> Any:
    body = json.dumps({"id": event_id, "type": kind, "data": {"object": obj}}).encode()
    header = stripe_engine.sign_payload(body, SECRET, int(time.time()))
    return client.post(
        reverse("payments_webhook"),
        data=body,
        content_type="application/json",
        headers={"Stripe-Signature": header},
    )


def test_account_updated_updates_the_status_and_requirements(client: Client, webhook: None) -> None:
    vendor = VendorFactory.create(sales_mode_default=Vendor.SalesMode.ON_REQUEST)
    account = ConnectedAccountFactory.create(vendor=vendor, on_request_forced=True)
    response = post_event(
        client,
        "evt_a1",
        {
            "id": account.account_ref,
            "details_submitted": True,
            "charges_enabled": False,
            "payouts_enabled": False,
            "requirements": {"currently_due": ["external_account"]},
        },
    )
    assert response.status_code == 200
    account.refresh_from_db()
    assert (account.status, account.requirements_due) == ("restricted", ["external_account"])
    entry = AuditLog.objects.get(action="payments.connected_account.updated")
    assert entry.actor_label == "webhook:payments"


def test_account_updated_active_switches_the_vendor_to_sale(client: Client, webhook: None) -> None:
    vendor = VendorFactory.create(sales_mode_default=Vendor.SalesMode.ON_REQUEST)
    account = ConnectedAccountFactory.create(vendor=vendor, on_request_forced=True)
    obj = {
        "id": account.account_ref,
        "details_submitted": True,
        "charges_enabled": True,
        "payouts_enabled": True,
    }
    assert post_event(client, "evt_a2", obj).status_code == 200
    assert post_event(client, "evt_a2", obj).status_code == 200  # a redelivery
    vendor.refresh_from_db()
    assert vendor.sales_mode_default == Vendor.SalesMode.BUY
    assert AuditLog.objects.filter(action="payments.vendor_sales_mode.changed").count() == 1


def test_an_unknown_account_changes_nothing(client: Client, webhook: None) -> None:
    other = ConnectedAccountFactory.create()
    obj = {
        "id": "acct_unknown",
        "details_submitted": True,
        "charges_enabled": True,
        "payouts_enabled": True,
    }
    assert post_event(client, "evt_a3", obj).status_code == 200
    other.refresh_from_db()
    assert other.status == "pending"


def test_a_payload_without_an_account_id_is_malformed(client: Client, webhook: None) -> None:
    assert post_event(client, "evt_a4", {"charges_enabled": True}).status_code == 400


# --- Demo payout accounts (ARC.10 seed, owner decision 2026-11-13)


def test_a_demo_account_is_ready_audited_idempotent_and_dry_runnable(ctx: AuditContext) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    preview = connect.record_demo_account(vendor, number=7, ctx=ctx, dry_run=True)
    assert not preview.is_empty
    assert not ConnectedAccount.objects.filter(vendor=vendor).exists()

    connect.record_demo_account(vendor, number=7, ctx=ctx)

    account = ConnectedAccount.objects.get(vendor=vendor)
    assert (account.account_ref, account.status) == ("acct_demo_7", stripe_engine.ACTIVE)
    assert AuditLog.objects.filter(action="payments.connected_account.created").count() == 1
    assert connect.record_demo_account(vendor, number=8, ctx=ctx).is_empty
