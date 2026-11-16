"""ORD.20 part 3 (staff side ORD.22): cancelling a whole package with a full refund.

Owner decision 2026-11-16: the cause decides the refund fee. The vendor's fault charges it to the
shop; a customer's request (`customer_requested_cancellation`) or a platform error (the L-10 flag)
does not, set by the service only. Lane-a decisions on #577: the stock follows the cause (the
vendor's fault restocks nothing, the others restock), staff refunds are platform-initiated, and
the cancellation shows in the customer's timeline. Also the money path: the ledger sums to zero,
`due_by` is the event plus 14 days, the refund is submitted only after the commit, the dry run
writes nothing, the passkey is always asked, staff need a note, an open draft is superseded and a
second cancellation is refused. Only Support and the platform owner act from the platform side.
"""

import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404
from django.test import Client

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.catalog.models import Product
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.ledger.models import LedgerEntry, Refund, RefundChannel
from apps.ledger.tests.integration.test_amendment_refunds import (  # noqa: F401 (fixtures)
    _setup,
    place_seed,
    stripe,
)
from apps.manager.tests.integration.test_amendment_views import (
    PLATFORM,
    SHOP,
    line_key,
    member,
    shop_login,
    staff_login,
)
from apps.orders import amendment_engine as am
from apps.orders import amendments
from apps.orders.models import AmendmentState, VendorOrder
from apps.orders.tests.control import plainly
from apps.orders.tests.factories import paid_vendor_order
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

D = Decimal
STAFF_CTX = AuditContext(actor_label="staff:test", source=AuditLog.Source.ADMIN)
SHOP_CTX = AuditContext(actor_label="vendor:test", source=AuditLog.Source.VENDOR)
ASKED = {"channel": "email", "date": "2026-11-16", "text": "Please cancel my order"}
CAPTURE = Callable[..., Any]


@pytest.fixture
def seeded(stripe: FakeStripeClient) -> VendorOrder:  # noqa: F811
    return place_seed(stripe, customer=UserFactory.create())


def grant_for(client: Client, user: User, sub: VendorOrder, cause: str) -> None:
    """What the prompt's "Do it once" ceremony mints in this browser session."""
    ask = amendments.cancellation_ask(sub, user, cause, ctx=STAFF_CTX)
    key = client.session.session_key
    assert key
    levels.create_grant(key, user, ask.action, ask.digest, ttl=120)


def session_grant(user: User, sub: VendorOrder, cause: str) -> str:
    ask = amendments.cancellation_ask(sub, user, cause, ctx=STAFF_CTX)
    session = "s" * 32
    levels.create_grant(session, user, ask.action, ask.digest, ttl=120)
    return session


def stock(sub: VendorOrder) -> dict[str, int]:
    return {
        line.title: Product.objects.get(pk=line.product_id or 0).stock_qty
        for line in sub.lines.all()
    }


def balanced(sub: VendorOrder) -> Decimal:
    entries = LedgerEntry.objects.filter(vendor_order=sub).exclude(kind="refund_fee")
    return sum((e.amount for e in entries), D("0"))


# --- staff: the cause decides the fee and the stock -----------------------------------------------

CAUSES = [
    ("vendor_fault", True, "", False, False),
    ("customer_request", False, "customer_requested_cancellation", False, True),
    ("platform_error", False, "", True, True),
]


@pytest.mark.parametrize(("cause", "fee", "exclusion", "platform_error", "restock"), CAUSES)
def test_support_cancels_and_the_cause_decides_fee_and_stock(
    client: Client,
    seeded: VendorOrder,
    django_capture_on_commit_callbacks: CAPTURE,
    cause: str,
    fee: bool,
    exclusion: str,
    platform_error: bool,
    restock: bool,
) -> None:
    support = staff_login(client)
    url = f"{PLATFORM}{seeded.public_id}/cancel/"
    data = {
        "cancel-reason": "Cannot ship",
        "cancel-staff_note": "Seller phoned",
        "cancel-cause": cause,
    }
    prompt = client.post(url, data)
    assert prompt.status_code == 403
    assert 'name="cancel-cause"' in prompt.text, plainly(
        "the prompt must replay the cause", "it does not"
    )
    before = stock(seeded)
    grant_for(client, support, seeded, cause)
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(url, data).status_code == 302
    seeded.refresh_from_db()
    assert seeded.state == "cancelled", plainly("the package must be cancelled", seeded.state)
    refund = Refund.objects.filter(vendor_order=seeded).latest("id")
    assert refund.refunded_gross == D("170.80"), plainly(
        "a full cancellation refunds 170.80", f"it refunded {refund.refunded_gross}"
    )
    assert refund.channel == RefundChannel.PLATFORM_INITIATED, plainly(
        "a staff cancellation is a platform-initiated refund", f"it is {refund.channel}"
    )
    assert (refund.refund_fee > 0) is fee, plainly(
        f"a cancellation for {cause} must "
        + ("charge the refund fee to the shop" if fee else "charge no refund fee"),
        f"a fee of {refund.refund_fee} was written",
    )
    assert refund.fee_exclusion_reason == exclusion
    assert refund.platform_error is platform_error
    excluded = AuditLog.objects.filter(action="ledger.refund_fee_excluded").exists()
    assert excluded is not fee, plainly(
        "a dropped fee must be audited as ledger.refund_fee_excluded", f"audited: {excluded}"
    )
    after = stock(seeded)
    moved = after != before
    assert moved is restock, plainly(
        f"a cancellation for {cause} must " + ("restock" if restock else "restock nothing"),
        f"stock went from {before} to {after}",
    )
    entry = AuditLog.objects.filter(action="orders.amendment_package_cancelled").latest("id")
    assert (entry.after or {})["cancel_cause"] == cause
    assert (entry.after or {})["restocked"] is restock


def test_the_money_path_of_a_cancellation(
    seeded: VendorOrder, django_capture_on_commit_callbacks: CAPTURE
) -> None:
    support = UserFactory.create(platform_role="support")
    session = session_grant(support, seeded, "vendor_fault")
    with django_capture_on_commit_callbacks() as callbacks:
        amendments.cancel_package(
            seeded,
            actor=support,
            reason="Cannot ship",
            staff_note="note",
            ctx=STAFF_CTX,
            passkey_session=session,
        )
        refund = Refund.objects.get(vendor_order=seeded)
        assert refund.submission_pending, plainly(
            "the refund must wait for the commit before reaching the provider", "it did not"
        )
        assert refund.due_by is not None
        assert refund.due_by - refund.created_at < dt.timedelta(days=14, minutes=1)
        assert refund.due_by - refund.created_at > dt.timedelta(days=13, hours=23)
    assert callbacks, plainly("the submission must be registered on commit", "nothing was")
    for callback in callbacks:
        callback()
    refund.refresh_from_db()
    assert not refund.submission_pending, plainly(
        "after the commit the refund must be submitted", "it is still pending"
    )
    assert refund.commission_reversal > 0
    assert refund.payment_fee_reversal >= 0
    assert balanced(seeded) == 0, plainly(
        "the package's ledger must sum to zero after a full cancellation",
        f"it sums {balanced(seeded)}",
    )


def test_a_dry_run_writes_nothing(seeded: VendorOrder) -> None:
    support = UserFactory.create(platform_role="support")
    changes = amendments.cancel_package(
        seeded, actor=support, reason="Cannot ship", staff_note="note", ctx=STAFF_CTX, dry_run=True
    )
    seeded.refresh_from_db()
    assert seeded.state != "cancelled"
    assert not Refund.objects.filter(vendor_order=seeded).exists()
    assert changes.changes, plainly("the dry run must return the ChangeSet", "it is empty")


def test_service_refusals(seeded: VendorOrder) -> None:
    support = UserFactory.create(platform_role="support")
    with pytest.raises(amendments.PasskeyRequired):
        amendments.cancel_package(seeded, actor=support, reason="x", staff_note="n", ctx=STAFF_CTX)
    with pytest.raises(amendments.AmendmentRefused, match="note"):
        amendments.cancel_package(seeded, actor=support, reason="x", ctx=STAFF_CTX)
    with pytest.raises(amendments.AmendmentRefused, match="Choose why"):
        amendments.cancel_package(
            seeded, actor=support, reason="x", staff_note="n", cause="whim", ctx=STAFF_CTX
        )
    session = session_grant(support, seeded, "customer_request")
    with pytest.raises(amendments.PasskeyRequired):
        amendments.cancel_package(
            seeded,
            actor=support,
            reason="x",
            staff_note="n",
            cause="vendor_fault",
            ctx=STAFF_CTX,
            passkey_session=session,
        )
    seeded.refresh_from_db()
    assert seeded.state != "cancelled", plainly(
        "a grant for one cause must not cancel for another", "it did"
    )


def test_an_open_window_does_not_cover_a_cancellation(client: Client, seeded: VendorOrder) -> None:
    support = UserFactory.create(platform_role="support")
    verified_login(client, support)  # a passkey window open, with `elevate`
    url = f"{PLATFORM}{seeded.public_id}/cancel/"
    data = {
        "cancel-reason": "Cannot ship",
        "cancel-staff_note": "n",
        "cancel-cause": "vendor_fault",
    }
    assert client.post(url, data).status_code == 403, plainly(
        "an open passkey window must not cover a cancellation", "it did"
    )
    seeded.refresh_from_db()
    assert seeded.state != "cancelled"


def test_an_open_draft_is_superseded_and_a_second_cancel_refused(seeded: VendorOrder) -> None:
    owner = member(seeded)
    draft, _ = amendments.draft_amendment(
        seeded, [am.RemoveLine(line_key(seeded, "B"))], actor=owner, reason="x", ctx=SHOP_CTX
    )
    assert draft is not None
    support = UserFactory.create(platform_role="support")
    session = session_grant(support, seeded, "vendor_fault")
    amendments.cancel_package(
        seeded,
        actor=support,
        reason="Cannot ship",
        staff_note="n",
        ctx=STAFF_CTX,
        passkey_session=session,
    )
    draft.refresh_from_db()
    assert draft.state == AmendmentState.SUPERSEDED, plainly(
        "the refund must supersede the open draft", f"it is {draft.state}"
    )
    with pytest.raises(amendments.AmendmentRefused, match="cancelled"):
        amendments.cancel_package(
            seeded, actor=support, reason="again", staff_note="n", ctx=STAFF_CTX
        )


@pytest.mark.parametrize("role", ["finance", "catalog", "config"])
def test_other_platform_roles_cannot_amend_or_cancel(seeded: VendorOrder, role: str) -> None:
    staff = UserFactory.create(platform_role=role)
    with pytest.raises((PermissionDenied, Http404)):
        amendments.cancel_package(seeded, actor=staff, reason="x", staff_note="n", ctx=STAFF_CTX)
    with pytest.raises((PermissionDenied, Http404)):
        amendments.draft_amendment(
            seeded,
            [am.ChangeCarrier(am.ShippingOffer("standard", "", D("0")))],
            actor=staff,
            reason="x",
            staff_note="n",
            ctx=STAFF_CTX,
        )
    seeded.refresh_from_db()
    assert seeded.state != "cancelled", plainly(f"the {role} role must not cancel", "it did")


def test_the_customer_sees_the_cancellation_in_the_timeline(
    client: Client, seeded: VendorOrder
) -> None:
    support = UserFactory.create(platform_role="support")
    amendments.cancel_package(
        seeded,
        actor=support,
        reason="Cannot ship",
        staff_note="n",
        ctx=STAFF_CTX,
        passkey_session=session_grant(support, seeded, "vendor_fault"),
    )
    customer = seeded.order.customer
    assert customer is not None
    client.force_login(customer)
    page = client.get(f"/account/orders/{seeded.order.public_id}/").text
    assert "Package cancelled" in page, plainly("the timeline must show the cancellation", "no")
    assert "170.80 EUR refunded" in page


def test_the_cancellation_exclusion_cannot_be_set_by_hand(seeded: VendorOrder) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        Refund.objects.create(
            vendor=seeded.vendor,
            vendor_order=seeded,
            channel=RefundChannel.THROUGH_PLATFORM,
            cause="",
            idempotency_key="refund:hand-made",
            refunded_gross=D("10.00"),
            fee_exclusion_reason="customer_requested_cancellation",
        )


# --- the shop ------------------------------------------------------------------------------------


def shop_cancel_url(sub: VendorOrder) -> str:
    return f"{SHOP}{sub.public_id}/amend/cancel/"


def test_the_owner_cancels_for_its_own_fault_and_pays_the_fee(
    client: Client, seeded: VendorOrder, django_capture_on_commit_callbacks: CAPTURE
) -> None:
    owner = member(seeded)
    shop_login(client, owner)
    data = {"cancel-cause": "vendor_fault", "cancel-reason": "B is out of stock"}
    prompt = client.post(shop_cancel_url(seeded), data)
    assert prompt.status_code == 403
    assert "vendor_refund" in prompt.text
    grant_for(client, owner, seeded, "vendor_fault")
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(shop_cancel_url(seeded), data).status_code == 302
    refund = Refund.objects.get(vendor_order=seeded)
    assert refund.channel == RefundChannel.THROUGH_PLATFORM
    assert refund.refund_fee > 0, plainly("the shop's own fault charges the fee", "it did not")


def test_a_member_cancels_on_the_customer_s_request_without_fee(
    client: Client, seeded: VendorOrder, django_capture_on_commit_callbacks: CAPTURE
) -> None:
    clerk = member(seeded, role="staff")
    shop_login(client, clerk)
    missing = client.post(
        shop_cancel_url(seeded), {"cancel-cause": "customer_request", "cancel-reason": "Asked"}
    )
    assert missing.status_code == 400, plainly(
        "the customer's request must be recorded", f"it answered {missing.status_code}"
    )
    data = {
        "cancel-cause": "customer_request",
        "cancel-reason": "You asked to cancel",
        "cancel-request_channel": "email",
        "cancel-request_date": "2026-11-16",
        "cancel-request_text": "Please cancel my order",
    }
    grant_for(client, clerk, seeded, "customer_request")
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(shop_cancel_url(seeded), data).status_code == 302
    refund = Refund.objects.get(vendor_order=seeded)
    assert refund.refund_fee == 0
    assert refund.fee_exclusion_reason == "customer_requested_cancellation"
    entry = AuditLog.objects.filter(action="orders.amendment_package_cancelled").latest("id")
    assert (entry.after or {})["customer_request"]["channel"] == "email"
    assert "Please cancel" not in str(entry.after), plainly(
        "the audit keeps a digest of the customer's words, never the text", "the text is there"
    )


def test_the_shop_cannot_record_a_platform_error(seeded: VendorOrder) -> None:
    owner = member(seeded)
    with pytest.raises(amendments.AmendmentRefused, match="platform error"):
        amendments.cancel_package(
            seeded, actor=owner, reason="x", cause="platform_error", ctx=SHOP_CTX
        )
    with pytest.raises(amendments.AmendmentRefused, match="needs the request"):
        amendments.cancel_package(
            seeded, actor=owner, reason="x", cause="customer_request", ctx=SHOP_CTX
        )
    with pytest.raises(amendments.AmendmentRefused, match="needs the request"):
        amendments.cancel_package(
            seeded,
            actor=owner,
            reason="x",
            cause="customer_request",
            customer_request={**ASKED, "text": ""},
            ctx=SHOP_CTX,
        )


def test_another_shop_cannot_cancel(client: Client, seeded: VendorOrder) -> None:
    other = paid_vendor_order(VendorFactory.create())
    shop_login(client, member(other))
    response = client.post(
        shop_cancel_url(seeded), {"cancel-cause": "vendor_fault", "cancel-reason": "x"}
    )
    assert response.status_code == 404
    seeded.refresh_from_db()
    assert seeded.state != "cancelled"
