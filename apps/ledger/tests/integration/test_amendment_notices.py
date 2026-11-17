"""ORD.20 part 3b: the notices of applied amendments and cancellations, on the ORD.20 seed (A 2 x
50.00 and B 1 x 30.00 net at 22 %, shipping 10.00 net, gross 170.80) with the Stripe double.

Each notice is an email in the outbox and an `OrderEvent` keeping its exact text with the CMP.24
wording version (durable medium, art. 51(7) Codice del consumo). Every assertion says in plain
words what was expected and what happened (owner, 2026-11-16).
"""

import datetime as dt
import re
from decimal import Decimal
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext

from apps.accounts.tests.factories import UserFactory
from apps.core import services as core_services
from apps.core.changeset import ChangeSet
from apps.core.models import EmailSuppression, OutboxEmail, PlatformOperator
from apps.ledger.models import Refund
from apps.ledger.tests.integration.test_amendment_refunds import (  # noqa: F401 (fixtures)
    CTX,
    _setup,
    apply,
    cancel_now,
    draft,
    key_of,
    owner_of,
    place_seed,
    stripe,
)
from apps.orders import amendment_engine as am
from apps.orders import amendment_notices as notices
from apps.orders import amendments
from apps.orders import services as order_services
from apps.orders.models import OrderEvent, VendorOrder
from apps.orders.tests.control import plainly
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.vendors.models import Vendor

pytestmark = pytest.mark.django_db

D = Decimal

CREDIT = (
    "It is credited within 5 to 10 business days depending on the card issuer, and in any case "
    "within 14 days."
)
INVOICE = "Order changed after payment: if you already issued an invoice or receipt"


def mail(kind: str) -> OutboxEmail:
    found = list(OutboxEmail.objects.filter(template_key=kind))
    assert len(found) == 1, plainly(f"one {kind} email must be queued", f"{len(found)} were")
    return found[0]


def event(sub: VendorOrder, kind: str) -> OrderEvent:
    found = list(OrderEvent.objects.filter(vendor_order=sub, kind=kind))
    assert len(found) == 1, plainly(f"one {kind} notice must be kept", f"{len(found)} were")
    return found[0]


def text(found: OutboxEmail) -> str:
    return core_services.render_email(
        found.template_key, found.language, found.context_data
    ).text_body


def remove_b(sub: VendorOrder, **kw: Any) -> None:
    actor = owner_of(sub)
    apply(draft(sub, actor, am.RemoveLine(key_of(sub, "B"))), actor, **kw)


def test_an_unavailable_item_sends_the_a2_notice_with_the_offer_and_its_deadline(
    stripe: FakeStripeClient,  # noqa: F811
) -> None:
    customer = UserFactory.create()
    sub = place_seed(stripe, customer=customer)
    remove_b(sub, now=timezone.now())

    email = mail(notices.ITEM_UNAVAILABLE)
    body = text(email)
    assert email.recipient == sub.order.email
    for needed in ("36.60", "134.20", CREDIT, "B in stock", "will not ship it before"):
        assert needed in body, plainly(f"the A2 notice must contain {needed!r}", body)
    page = reverse("customer_order", args=[sub.order.public_id])
    assert "Cancel the rest of the order and refund everything: http" in body, body
    assert page in body, plainly("the A2 notice must link the customer's order page", body)
    assert "30 days" not in body
    kept = event(sub, notices.ITEM_UNAVAILABLE)
    assert (kept.audience, kept.language, kept.text) == ("customer", "en", body), plainly(
        "the customer area must keep the exact text of the A2 email", kept.text
    )
    assert kept.wording_version.startswith("sha256:")
    assert len(kept.wording_version) == 71
    assert kept.payload["refund"] == "36.60"


def test_the_vendor_gets_the_change_with_the_invoice_notice(stripe: FakeStripeClient) -> None:  # noqa: F811
    sub = place_seed(stripe)
    remove_b(sub)
    body = text(mail(notices.VENDOR_AMENDMENT_APPLIED))
    assert "version 2" in body, body
    assert "B removed as unavailable" in body, body
    assert INVOICE in body, body
    assert not body.startswith("Important:"), plainly(
        "without a recorded invoice the invoice notice comes last, not marked important", body
    )
    assert event(sub, notices.VENDOR_AMENDMENT_APPLIED).audience == "vendor"


def test_a_recorded_invoice_puts_the_invoice_notice_first(stripe: FakeStripeClient) -> None:  # noqa: F811
    sub = place_seed(stripe)
    VendorOrder.objects.filter(pk=sub.pk).update(invoice_issued_at=timezone.now())
    remove_b(sub)
    body = text(mail(notices.VENDOR_AMENDMENT_APPLIED))
    assert body.startswith("Important: " + INVOICE), plainly(
        "with invoice_issued_at set the invoice notice must open the vendor's email", body
    )
    html = core_services.render_email(
        notices.VENDOR_AMENDMENT_APPLIED, "en", mail(notices.VENDOR_AMENDMENT_APPLIED).context_data
    ).html_body
    assert "<strong>Important:" in html


def test_a_lower_price_sends_the_change_notice_with_the_refund(stripe: FakeStripeClient) -> None:  # noqa: F811
    sub = place_seed(stripe)
    actor = owner_of(sub)
    apply(draft(sub, actor, am.ChangePrice(key_of(sub, "A"), D("45.00"))), actor)
    body = text(mail(notices.AMENDMENT_APPLIED))
    for needed in ("A: lower price", "We have refunded 12.20 EUR", CREDIT, "158.60", "Reason:"):
        assert needed in body, plainly(f"the change notice must contain {needed!r}", body)
    assert not OutboxEmail.objects.filter(template_key=notices.ITEM_UNAVAILABLE).exists()


def test_the_notice_is_in_the_customer_s_language(stripe: FakeStripeClient) -> None:  # noqa: F811
    sub = place_seed(stripe, language="it")
    remove_b(sub)
    kept = event(sub, notices.ITEM_UNAVAILABLE)
    assert kept.language == "it"
    assert "rimborsarti" in kept.text, plainly(
        "an Italian order must get the A2 notice in Italian", kept.text
    )
    english = event(sub, notices.VENDOR_AMENDMENT_APPLIED)
    assert english.language == "en", "a vendor without a mail language reads the platform language"


def test_a_dry_run_sends_and_keeps_nothing(stripe: FakeStripeClient) -> None:  # noqa: F811
    sub = place_seed(stripe)
    actor = owner_of(sub)
    amendments.apply_amendment(
        draft(sub, actor, am.RemoveLine(key_of(sub, "B"))), actor=actor, ctx=CTX, dry_run=True
    )
    assert not OrderEvent.objects.exists()
    assert not OutboxEmail.objects.filter(template_key__in=notices.KINDS).exists()


def test_cancelling_the_rest_notifies_both_sides(stripe: FakeStripeClient) -> None:  # noqa: F811
    customer = UserFactory.create()
    sub = place_seed(stripe, customer=customer)
    remove_b(sub)
    cancel_now(sub, actor=customer, ctx=CTX)
    body = text(mail(notices.REST_CANCELLED))
    for needed in ("134.20", CREDIT, "A x 2"):
        assert needed in body, plainly(f"the cancel-the-rest notice must contain {needed!r}", body)
    vendor = text(mail(notices.VENDOR_PACKAGE_CANCELLED))
    assert "the customer cancelled the rest" in vendor, vendor
    assert "The refund fee is charged to your shop." in vendor, plainly(
        "a cancel of the rest keeps the vendor's refund fee and says so", vendor
    )
    rest = Refund.objects.get(vendor_order=sub, cause="cancel_rest")
    charged = rest.refund_fee > 0 and not rest.fee_exclusion_reason and not rest.platform_error
    assert charged, plainly("the notice's fee line must match the ledger's refund", str(rest))
    kinds = [c.kind for c in amendments.package_cancellations(sub)]
    assert kinds == [amendments.KIND_REST_CANCELLED]


def test_cancelling_the_rest_stays_allowed_while_the_shop_s_orders_are_frozen(
    stripe: FakeStripeClient,  # noqa: F811
    settings: Any,
) -> None:
    """Customer protection (ORD.20): a termination freeze stops the shop, never the customer's
    free cancel of the rest after an unavailable item."""
    customer = UserFactory.create()
    sub = place_seed(stripe, customer=customer)
    remove_b(sub)
    settings.ORDER_FREEZE_HOOKS = ["apps.orders.tests.integration.test_amendments.always_frozen"]
    cancel_now(sub, actor=customer, ctx=CTX)
    sub.refresh_from_db()
    assert sub.state == "cancelled", plainly(
        "the customer must be able to cancel the rest while the shop's orders are frozen",
        f"the package is {sub.state}",
    )


def test_the_offer_deadline_is_in_the_business_time_zone(stripe: FakeStripeClient) -> None:  # noqa: F811
    sub = place_seed(stripe, customer=UserFactory.create())
    remove_b(sub, now=dt.datetime(2026, 11, 17, 10, 0, tzinfo=dt.UTC))
    body = text(mail(notices.ITEM_UNAVAILABLE))
    assert "Nov. 18, 2026, 11 a.m." in body, plainly(
        "the hold ends 24 hours later, shown in Rome time (11:00)", body
    )


def test_a_kept_notice_is_append_only_except_the_anonymisation_scrub(
    stripe: FakeStripeClient,  # noqa: F811
) -> None:
    sub = place_seed(stripe)
    remove_b(sub)
    kept = event(sub, notices.ITEM_UNAVAILABLE)
    with pytest.raises(PermissionError):
        kept.save()
    for statement in (
        "UPDATE orders_orderevent SET text = 'edited' WHERE id = %s",
        "DELETE FROM orders_orderevent WHERE id = %s",
    ):
        with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(statement, [kept.pk])
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('mpmv.order_scrub', 'anonymise', true)")
        changes = ChangeSet()
        order_services._scrub_order_events(sub.order, changes)
    kept.refresh_from_db()
    assert (kept.text, kept.subject, kept.payload) == ("ANONYMISED", "ANONYMISED", {}), plainly(
        "anonymisation must replace the kept text, which may quote the vendor's reason",
        kept.text,
    )
    assert kept.wording_version.startswith("sha256:"), "the wording version stays"


# --- a durable copy only of what went out (lane-b review of #624) ------------------------------


def audiences(sub: VendorOrder) -> list[str]:
    return sorted(OrderEvent.objects.filter(vendor_order=sub).values_list("audience", flat=True))


def test_a_vendor_without_an_email_address_gets_no_kept_copy(
    stripe: FakeStripeClient,  # noqa: F811
) -> None:
    sub = place_seed(stripe)
    Vendor.objects.filter(pk=sub.vendor_id).update(contact_email="")
    remove_b(sub)
    assert audiences(sub) == ["customer"], plainly(
        "a notice that could not be sent must leave no durable copy", str(audiences(sub))
    )


def test_a_suppressed_customer_address_gets_no_kept_copy(
    stripe: FakeStripeClient,  # noqa: F811
) -> None:
    sub = place_seed(stripe)
    EmailSuppression.objects.create(address=sub.order.email.lower(), reason="hard_bounce")
    remove_b(sub)
    assert audiences(sub) == ["vendor"], plainly(
        "the customer area must not list a message that was suppressed", str(audiences(sub))
    )
    assert OutboxEmail.objects.get(template_key=notices.ITEM_UNAVAILABLE).status == "suppressed"


def test_a_refused_recipient_gets_no_kept_copy_and_the_change_still_applies(
    stripe: FakeStripeClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The outbox refuses a recipient that is not one plain address (SEC.5, a `ValueError`);
    the order's email cannot be edited (D-29), so the refusal is simulated for the customer."""
    sub = place_seed(stripe)
    real = core_services.enqueue_email

    def refuse_customer(**kwargs: Any) -> Any:
        if kwargs["recipient"] == sub.order.email:
            raise ValueError("invalid email recipient")
        return real(**kwargs)

    monkeypatch.setattr(core_services, "enqueue_email", refuse_customer)
    remove_b(sub)
    sub.refresh_from_db()
    assert getattr(sub.current_version, "number", None) == 2
    assert audiences(sub) == ["vendor"], plainly(
        "a recipient the outbox refuses must leave no durable copy", str(audiences(sub))
    )


def test_a_hold_shorter_than_the_a2_minimum_is_refused(
    stripe: FakeStripeClient,  # noqa: F811
) -> None:
    """Counsel (2026-11-17): the A2 date is never less than the minimum window after the notice."""
    sub = place_seed(stripe)
    PlatformOperator.objects.update(amendment_cancel_hold_hours=amendments.A2_MIN_WINDOW_HOURS - 1)
    # The refusal follows the active language (I18N.15): compare with the translated text.
    refusal = gettext(
        "The customer must have at least %(hours)s hours to read the notice and cancel the "
        "rest before the seller may ship, but the shipping hold is set to %(set)s hours: "
        "raise the hold in the amendment settings."
    ) % {"hours": 24, "set": amendments.A2_MIN_WINDOW_HOURS - 1}
    assert amendments.A2_MIN_WINDOW_HOURS == 24, "counsel's A2 minimum is 24 hours"
    with pytest.raises(amendments.AmendmentRefused, match=re.escape(refusal)):
        remove_b(sub)
    sub.refresh_from_db()
    assert getattr(sub.current_version, "number", None) == 1, plainly(
        "a refused hold must leave the order as it was", f"version {sub.current_version}"
    )
    assert not OrderEvent.objects.filter(vendor_order=sub).exists()


def test_the_guest_a2_names_the_support_address(stripe: FakeStripeClient) -> None:  # noqa: F811
    sub = place_seed(stripe)
    PlatformOperator.objects.update(support_email="help@example.test")
    remove_b(sub)
    kept = event(sub, notices.ITEM_UNAVAILABLE)
    assert "contact support at help@example.test" in kept.text, kept.text
    assert kept.payload["wording_approval"]["approved_on"] == "2026-11-17"
