"""Delivered and cancelled order emails (ORD.9, part 2), queued by the state transitions."""

import pytest

from apps.core import services as core_services
from apps.core.mail import html_violations
from apps.core.models import OutboxEmail
from apps.orders import notifications, services
from apps.orders.models import Order
from apps.orders.tests.factories import unchecked
from apps.orders.tests.integration.test_notifications import render, two_vendor_order
from apps.orders.tests.integration.test_services import CTX, pay, place

pytestmark = pytest.mark.django_db

LANGS = ("en", "it", "de", "fr", "es")
NEW_KINDS = (
    "orders.vendor_order_delivered",
    "orders.order_cancelled",
    "orders.vendor_order_cancelled",
    "orders.vendor_order_cancelled_notice",
)


def ship(sub, tracking: str = "TRK1") -> None:  # type: ignore[no-untyped-def]
    services.transition_vendor_order(sub, "accepted", ctx=CTX)
    services.transition_vendor_order(sub, "shipped", ctx=CTX, tracking_number=tracking)


def mails(kind: str) -> list[OutboxEmail]:
    return list(OutboxEmail.objects.filter(template_key=kind))


def test_delivery_tells_the_customer_once_in_their_language() -> None:
    order = place(language="it")
    pay(order)
    sub = order.vendor_orders.get()
    ship(sub)
    services.transition_vendor_order(sub, "delivered", ctx=CTX)
    notifications.notify_vendor_order_delivered(sub)  # replay: idempotent
    (mail,) = mails("orders.vendor_order_delivered")
    assert (mail.recipient, mail.language) == (order.email, "it")
    assert order.number in render(mail).subject


def test_delivery_to_completed_does_not_resend() -> None:
    order = place()
    pay(order)
    sub = order.vendor_orders.get()
    ship(sub)
    services.transition_vendor_order(sub, "delivered", ctx=CTX)
    services.transition_vendor_order(sub, "completed", ctx=CTX)
    assert len(mails("orders.vendor_order_delivered")) == 1


def test_cancelling_an_unpaid_order_tells_the_customer_once() -> None:
    order = place(language="en")
    services.transition_order(order, "awaiting_payment", ctx=CTX)
    services.transition_order(order, "cancelled", ctx=CTX)
    notifications.notify_order_cancelled(order)
    (mail,) = mails("orders.order_cancelled")
    assert (mail.recipient, mail.language) == (order.email, "en")
    assert OutboxEmail.objects.count() == 1


def test_a_cancelled_vendor_order_tells_customer_and_vendor() -> None:
    order = two_vendor_order(language="it")
    pay(order)
    sub = order.vendor_orders.order_by("id").first()
    assert sub is not None
    services.transition_vendor_order(sub, "cancelled", ctx=CTX)
    (customer,) = mails("orders.vendor_order_cancelled")
    (vendor,) = mails("orders.vendor_order_cancelled_notice")
    assert customer.recipient == order.email
    assert vendor.recipient == sub.vendor.contact_email
    text = render(vendor)
    assert order.email not in text.subject + text.text_body + text.html_body
    assert "Bowl from B" not in text.text_body  # the other vendor's lines


def test_dry_run_and_rollback_send_nothing_new() -> None:
    order = place()
    pay(order)
    before = OutboxEmail.objects.count()
    sub = order.vendor_orders.get()
    services.transition_vendor_order(sub, "cancelled", ctx=CTX, dry_run=True)
    assert OutboxEmail.objects.count() == before


@pytest.mark.parametrize("lang", LANGS)
def test_every_new_kind_renders_in_every_language(lang: str) -> None:
    order = two_vendor_order(language=lang)
    pay(order)
    first, second = order.vendor_orders.order_by("id")
    services.transition_vendor_order(first, "cancelled", ctx=CTX)
    ship(second)
    services.transition_vendor_order(second, "delivered", ctx=CTX)
    other = place(language=lang)
    services.transition_order(other, "awaiting_payment", ctx=CTX)
    services.transition_order(other, "cancelled", ctx=CTX)
    found = {m.template_key for m in OutboxEmail.objects.all()}
    assert set(NEW_KINDS) <= found
    for mail in OutboxEmail.objects.filter(template_key__in=NEW_KINDS):
        r = core_services.render_email(mail.template_key, lang, mail.context_data)
        assert r.subject
        assert r.text_body.strip()
        assert html_violations(r.html_body) == []
        assert "{{" not in r.text_body + r.html_body
        lowered = (r.subject + r.text_body + r.html_body).lower()
        assert "invoice" not in lowered
        assert "receipt" not in lowered


def test_italian_wording_for_delivery() -> None:
    order = place(language="it")
    pay(order)
    sub = order.vendor_orders.get()
    ship(sub)
    services.transition_vendor_order(sub, "delivered", ctx=CTX)
    (mail,) = mails("orders.vendor_order_delivered")
    assert "consegnat" in render(mail).text_body.lower()


def test_a_restricted_order_gets_no_lifecycle_email() -> None:
    order = place()
    order.privacy_status = "restricted_tax_only"
    notifications.notify_order_cancelled(order)
    assert OutboxEmail.objects.count() == 0


def test_a_malformed_address_never_blocks_cancellation() -> None:
    order = place()
    with unchecked():
        Order.objects.filter(pk=order.pk).update(email="not an address")
    order.refresh_from_db()
    services.transition_order(order, "awaiting_payment", ctx=CTX)
    services.transition_order(order, "cancelled", ctx=CTX)
    order.refresh_from_db()
    assert order.state == "cancelled"
    assert OutboxEmail.objects.count() == 0
