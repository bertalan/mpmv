"""ORD.20 part 3b in the Manager and the customer area: cancellation notices by cause, the staff
counts of cancellations per shop and cause, the invoice record and notice, and the notices kept
in the customer's order page.

Every assertion says in plain words what was expected and what happened (owner, 2026-11-16).
"""

from collections.abc import Callable
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.core import services as core_services
from apps.core.models import AuditLog, OutboxEmail
from apps.ledger.models import Refund
from apps.ledger.tests.integration.test_amendment_refunds import (  # noqa: F401 (fixtures)
    _setup,
    place_seed,
    stripe,
)
from apps.manager.tests.integration.test_amendment_views import (
    PLATFORM,
    SHOP,
    applied_removal,
    member,
    shop_login,
    staff_login,
)
from apps.manager.tests.integration.test_package_cancellation import (
    ASKED,
    SHOP_CTX,
    STAFF_CTX,
    session_grant,
)
from apps.orders import amendment_notices as notices
from apps.orders import amendments
from apps.orders.models import OrderEvent, VendorOrder
from apps.orders.tests.control import plainly
from apps.payments.tests.fake_stripe import FakeStripeClient

pytestmark = pytest.mark.django_db

CAPTURE = Callable[..., Any]


@pytest.fixture
def seeded(stripe: FakeStripeClient) -> VendorOrder:  # noqa: F811
    return place_seed(stripe, customer=UserFactory.create())


def text_of(kind: str) -> tuple[str, str]:
    found = OutboxEmail.objects.get(template_key=kind)
    rendered = core_services.render_email(found.template_key, found.language, found.context_data)
    return rendered.subject, rendered.text_body


def staff() -> Any:
    return UserFactory.create(platform_role="support")


def staff_cancel(sub: VendorOrder, cause: str) -> None:
    support = staff()
    amendments.cancel_package(
        sub,
        actor=support,
        reason="The seller cannot ship",
        cause=cause,
        staff_note="Seller phoned",
        ctx=STAFF_CTX,
        passkey_session=session_grant(support, sub, cause),
    )


@pytest.mark.parametrize(
    ("cause", "customer_says", "vendor_says"),
    [
        ("vendor_fault", "cannot fulfil your order", "Cause: the shop cannot fulfil the order."),
        ("customer_request", "As you asked", "Cause: the customer asked to cancel."),
        (
            "platform_error",
            "Due to a technical issue on our platform",
            "technical error attributable to the platform",
        ),
    ],
)
def test_a_staff_cancellation_notifies_the_customer_by_cause_and_the_vendor(
    seeded: VendorOrder, cause: str, customer_says: str, vendor_says: str
) -> None:
    staff_cancel(seeded, cause)
    _, body = text_of(notices.PACKAGE_CANCELLED)
    assert "170.80" in body, body
    assert customer_says in body, plainly(
        f"the customer must read the {cause} wording and the full refund", body
    )
    assert "within 14 days" in body or "14-day limit" in body
    _, vendor = text_of(notices.VENDOR_PACKAGE_CANCELLED)
    assert "Platform support cancelled your order" in vendor, vendor
    assert vendor_says in vendor, vendor
    fee = "The refund fee is charged to your shop."
    refund = Refund.objects.get(vendor_order=seeded, cause="cancel_package")
    charged = (
        refund.refund_fee > 0 and not refund.fee_exclusion_reason and not refund.platform_error
    )
    assert (fee in vendor) is charged, plainly(
        "the vendor's fee line must match the ledger's refund (fee and exclusion)",
        f"fee {refund.refund_fee}, exclusion {refund.fee_exclusion_reason!r}, "
        f"platform error {refund.platform_error}: {vendor}",
    )
    assert charged is (cause == "vendor_fault")
    vendor_copy = OrderEvent.objects.get(vendor_order=seeded, audience="vendor")
    assert vendor_copy.payload["refund_fee"] is charged
    assert "Do not ship these items:" in vendor
    assert "A x 2" in vendor
    kept = OrderEvent.objects.filter(vendor_order=seeded).values_list("audience", flat=True)
    assert sorted(kept) == ["customer", "vendor"]
    assert "Seller phoned" not in body + vendor, "the staff note is internal"


def test_the_audit_entry_names_the_kind_and_old_entries_stay_readable(
    seeded: VendorOrder,
) -> None:
    staff_cancel(seeded, "vendor_fault")
    entry = AuditLog.objects.get(
        action="orders.amendment_package_cancelled", target_model="orders.vendororder"
    )
    assert (entry.after or {}).get("kind") == amendments.KIND_PACKAGE_CANCELLED
    # Entries written before part 3b carry no `kind`.
    assert amendments.cancellation_kind({"cause": "cancel_package"}) == "package_cancelled"
    assert amendments.cancellation_kind({"refund": "x"}) == "rest_cancelled"
    found = amendments.package_cancellations(seeded)
    assert [(c.kind, c.by_customer) for c in found] == [("package_cancelled", False)]


def test_staff_see_the_shops_self_declared_customer_request_cancellations(
    client: Client,
    stripe: FakeStripeClient,  # noqa: F811
) -> None:
    claimed = place_seed(stripe, customer=UserFactory.create())
    owner = member(claimed)
    session = session_grant(owner, claimed, "customer_request")
    amendments.cancel_package(
        claimed,
        actor=owner,
        reason="The customer changed their mind",
        cause="customer_request",
        customer_request=ASKED,
        ctx=SHOP_CTX,
        passkey_session=session,
    )
    faulty = place_seed(stripe, customer=UserFactory.create())
    staff_cancel(faulty, "vendor_fault")

    rows = {r.vendor_public_id: r for r in amendments.cancellation_counts()}
    first = rows[str(claimed.vendor.public_id)]
    assert (first.shop_customer_request, first.total) == (1, 1), plainly(
        "the shop's own 'customer asked' cancellation must be counted as such", str(first)
    )
    second = rows[str(faulty.vendor.public_id)]
    assert (second.staff, second.shop_customer_request) == (1, 0)
    assert amendments.cancellation_counts()[0].vendor_public_id == str(claimed.vendor.public_id)

    staff_login(client)
    page = client.get(PLATFORM)
    assert page.status_code == 200
    assert "data-cancellation-counts" in page.text
    assert "data-self-declared" in page.text, plainly(
        "the staff page must show the counts and mark the self-declared ones", "it does not"
    )
    assert claimed.vendor.display_name in page.text


def test_vendors_never_see_the_counts(client: Client, seeded: VendorOrder) -> None:
    shop_login(client, member(seeded))
    assert client.get(PLATFORM).status_code in {403, 404}


def test_the_shop_records_its_invoice_and_the_notice_becomes_prominent(
    client: Client, seeded: VendorOrder
) -> None:
    owner = member(seeded)
    shop_login(client, owner)
    page = f"{SHOP}{seeded.public_id}/"
    assert "I have issued the invoice or receipt" in client.get(page).text
    assert client.post(f"{page}invoice-issued/").status_code == 302
    seeded.refresh_from_db()
    assert seeded.invoice_issued_at is not None
    assert AuditLog.objects.filter(action="orders.invoice_issued_recorded").count() == 1
    again = client.post(f"{page}invoice-issued/")
    assert again.status_code == 409, plainly(
        "recording the invoice twice must be refused", f"it answered {again.status_code}"
    )
    amend = client.get(f"{page}amend/")
    assert "data-invoice-notice" in amend.text, plainly(
        "the amendment screen must show the invoice notice once the invoice is recorded", "no"
    )


def test_staff_cannot_record_the_shop_s_invoice(seeded: VendorOrder) -> None:
    with pytest.raises(PermissionDenied):
        amendments.record_invoice_issued(seeded, actor=staff(), ctx=STAFF_CTX)


def test_the_order_page_shows_the_invoice_notice_after_a_change(
    client: Client, seeded: VendorOrder
) -> None:
    applied_removal(seeded)
    shop_login(client, member(seeded))
    page = client.get(f"{SHOP}{seeded.public_id}/").text
    assert "Order changed after payment: if you already issued an invoice" in page


def test_the_customer_reads_the_kept_notices_on_the_order_page(
    client: Client, seeded: VendorOrder
) -> None:
    applied_removal(seeded)
    customer = seeded.order.customer
    assert customer is not None
    client.force_login(customer)
    page = client.get(f"/account/orders/{seeded.order.public_id}/")
    assert page.status_code == 200
    assert "data-notices" in page.text, plainly("the customer area must list the notices", "no")
    assert "partial refund for an unavailable item" in page.text


def test_a_vendor_cancellation_is_dated(seeded: VendorOrder) -> None:
    staff_cancel(seeded, "platform_error")
    event = OrderEvent.objects.get(vendor_order=seeded, audience="customer")
    assert event.created_at <= timezone.now()
    assert event.payload["cause"] == "platform_error"
