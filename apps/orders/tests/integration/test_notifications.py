"""Transactional order emails through the outbox (ORD.9)."""

import pytest
from django.db import transaction

from apps.core import services as core_services
from apps.core.mail import html_violations
from apps.core.models import OutboxEmail
from apps.orders import notifications, services
from apps.orders.models import Order, VendorOrder
from apps.orders.tests.integration.test_services import CTX, line_input, pay, place, vendor_input
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

LANGS = ("en", "it", "de", "fr", "es")
BANNED = ("invoice", "receipt", "fattura", "ricevuta", "rechnung", "facture", "recibo", "factura")


def two_vendor_order(**overrides: object) -> Order:
    a = vendor_input(VendorFactory.create(), lines=[line_input(title="Cup from A")])
    b = vendor_input(VendorFactory.create(), lines=[line_input(title="Bowl from B", sku="B-1")])
    overrides.setdefault("billing_address", {"line1": "9 Billing Road", "city": "Milano"})
    return place(vendor_orders=[a, b], **overrides)


def kinds() -> list[str]:
    return sorted(OutboxEmail.objects.values_list("template_key", flat=True))


def render(mail: OutboxEmail):  # type: ignore[no-untyped-def]
    return core_services.render_email(mail.template_key, mail.language, mail.context_data)


def test_order_received_goes_once_to_the_customer_in_their_language() -> None:
    order = place(language="it")
    notifications.notify_order_placed(order)
    notifications.notify_order_placed(order)  # same event again: no second email
    mail = OutboxEmail.objects.get()
    assert (mail.template_key, mail.recipient, mail.language) == (
        "orders.order_received",
        order.email,
        "it",
    )
    assert order.number in render(mail).subject


def test_unknown_language_falls_back_to_english() -> None:
    order = place(language="en")
    Order.objects.filter(pk=order.pk).update(language="xx")
    order.refresh_from_db()
    notifications.notify_order_placed(order)
    assert OutboxEmail.objects.get().language == "en"


def test_payment_sends_one_confirmation_per_vendor_and_one_vendor_mail_each() -> None:
    order = two_vendor_order()
    pay(order)
    notifications.notify_order_paid(order)  # replay: idempotent
    assert kinds() == [
        "orders.order_confirmation",
        "orders.order_confirmation",
        "orders.payment_confirmed",
        "orders.vendor_new_order",
        "orders.vendor_new_order",
    ]
    vendor_mails = OutboxEmail.objects.filter(template_key="orders.vendor_new_order")
    assert {m.recipient for m in vendor_mails} == {
        v.vendor.contact_email for v in order.vendor_orders.all()
    }


def test_a_vendor_sees_only_its_own_order_and_never_the_buyer_email() -> None:
    order = two_vendor_order()
    pay(order)
    for mail in OutboxEmail.objects.filter(template_key="orders.vendor_new_order"):
        body = render(mail)
        text = body.subject + body.text_body + body.html_body
        assert order.email not in text
        assert ("Cup from A" in text) != ("Bowl from B" in text)
        assert str(order.billing_address["line1"]) not in text


def test_confirmation_is_per_vendor_non_fiscal_and_never_an_invoice() -> None:
    order = two_vendor_order()
    pay(order)
    mails = OutboxEmail.objects.filter(template_key="orders.order_confirmation")
    assert mails.count() == 2
    labels = {
        "en": "Non-fiscal document",
        "it": "Documento non fiscale",
        "de": "Kein Steuerbeleg",
        "fr": "Document non fiscal",
        "es": "Documento no fiscal",
    }
    for mail in mails:
        for lang, label in labels.items():
            r = core_services.render_email(mail.template_key, lang, mail.context_data)
            assert label in r.text_body
            assert label in r.html_body
            assert order.number in r.text_body
            assert html_violations(r.html_body) == []
            lowered = (r.subject + r.text_body + r.html_body).lower()
            assert not any(word in lowered for word in BANNED)
        vendor = VendorOrder.objects.get(public_id=mail.context_data["vendor_order_ref"]).vendor
        assert vendor.display_name in render(mail).text_body


@pytest.mark.parametrize("lang", LANGS)
def test_every_kind_renders_in_every_language(lang: str) -> None:
    order = two_vendor_order(language=lang)
    notifications.notify_order_placed(order)
    pay(order)
    sub = order.vendor_orders.first()
    assert sub is not None
    services.transition_vendor_order(sub, "accepted", ctx=CTX)
    services.transition_vendor_order(
        sub, "shipped", ctx=CTX, tracking_number="TRK123", carrier="DHL"
    )
    assert "orders.vendor_order_shipped" in kinds()
    for mail in OutboxEmail.objects.all():
        r = render(mail)
        assert r.subject
        assert r.text_body.strip()
        assert html_violations(r.html_body) == []
        assert "{{" not in r.text_body + r.html_body


def test_shipping_notifies_the_customer_once_with_tracking() -> None:
    order = place(language="it")
    pay(order)
    sub = order.vendor_orders.get()
    services.transition_vendor_order(sub, "accepted", ctx=CTX)
    services.transition_vendor_order(sub, "shipped", ctx=CTX, tracking_number="TRK9", carrier="X")
    services.transition_vendor_order(sub, "delivered", ctx=CTX)
    mail = OutboxEmail.objects.get(template_key="orders.vendor_order_shipped")
    assert (mail.recipient, mail.language) == (order.email, "it")
    assert "TRK9" in render(mail).text_body


def test_a_rolled_back_payment_sends_nothing() -> None:
    order = place()
    services.transition_order(order, "awaiting_payment", ctx=CTX)

    def checkout_then_fail() -> None:
        with transaction.atomic():
            services.transition_order(order, "paid", ctx=CTX)
            raise RuntimeError("later step failed")

    with pytest.raises(RuntimeError):
        checkout_then_fail()
    assert OutboxEmail.objects.count() == 0


def test_a_dry_run_sends_nothing() -> None:
    order = place()
    services.transition_order(order, "awaiting_payment", ctx=CTX)
    services.transition_order(order, "paid", ctx=CTX, dry_run=True)
    assert OutboxEmail.objects.count() == 0


def test_a_restricted_order_gets_no_email() -> None:
    order = place()
    order.privacy_status = "restricted_tax_only"  # in memory: the scrub service has its own tests
    notifications.notify_order_placed(order)
    assert OutboxEmail.objects.count() == 0


def test_no_payment_data_or_internal_fields_in_any_context() -> None:
    order = two_vendor_order()
    pay(order)
    for mail in OutboxEmail.objects.all():
        blob = mail.context.lower()
        for forbidden in ("payment_intent", "transfer_group", "vat_number", "commission", "pi_"):
            assert forbidden not in blob


def test_italian_and_english_wording_differ_per_the_catalog() -> None:
    order = place(language="it")
    notifications.notify_order_placed(order)
    mail = OutboxEmail.objects.get()
    assert "Abbiamo ricevuto" in render(mail).text_body
    english = core_services.render_email(mail.template_key, "en", mail.context_data)
    assert "We received your order" in english.text_body
