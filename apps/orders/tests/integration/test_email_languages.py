"""ORD.16: every order email renders in the five languages, and vendors get a mail language.

German, French and Spanish must not fall back to English text; a vendor with a mail language
receives its notices in it, a vendor with none in the platform language.
"""

import re
from typing import Any

import pytest
from django.conf import settings

from apps.core import services as core_services
from apps.core.mail import html_violations
from apps.core.models import OutboxEmail
from apps.orders import notifications, services
from apps.orders.tests.integration.test_notifications import render, two_vendor_order
from apps.orders.tests.integration.test_services import CTX, pay
from apps.vendors.models import Vendor

pytestmark = pytest.mark.django_db

LANGS = ("en", "it", "de", "fr", "es")
FOREIGN = ("de", "fr", "es")

LINES = [{"title": "Cup", "variant": "Blue", "quantity": 2, "total": "12.00"}]
CUSTOMER = {"order_number": "MP-1001", "customer_name": "Alex", "vendor_name": "Shop X"}
CONTEXTS: dict[str, dict[str, Any]] = {
    "orders.order_received": {**CUSTOMER, "total": "12.00"},
    "orders.payment_confirmed": {**CUSTOMER, "total": "12.00"},
    "orders.order_confirmation": {
        **CUSTOMER,
        "vendor_order_ref": "ref-1",
        "lines": LINES,
        "shipping": "4.00",
        "total": "16.00",
    },
    "orders.vendor_new_order": {
        "order_number": "MP-1001",
        "vendor_name": "Shop X",
        "lines": LINES,
        "ship_to": ["Alex", "1 Main Street", "Berlin", "DE"],
        "total": "12.00",
    },
    "orders.vendor_order_shipped": {**CUSTOMER, "carrier": "DHL", "tracking_number": "TRK1"},
    "orders.vendor_order_delivered": CUSTOMER,
    "orders.order_cancelled": CUSTOMER,
    "orders.vendor_order_cancelled": CUSTOMER,
    "orders.vendor_order_cancelled_notice": {
        "order_number": "MP-1001",
        "vendor_name": "Shop X",
        "lines": [{"title": "Cup", "variant": "Blue", "quantity": 2}],
    },
}

# Words of the English source texts: none may appear in a German, French or Spanish email.
ENGLISH = re.compile(
    r"\b(Hello|Order|Shipping|Ship to|Carrier|Tracking|cancelled|delivered|shipped|received"
    r"|sold by|Payment|shipment|part of|Non-fiscal|Nothing was charged|paid order|your)\b"
)


def test_the_test_covers_every_order_email_kind() -> None:
    declared = {
        value
        for name, value in vars(notifications).items()
        if name.isupper() and isinstance(value, str) and value.startswith("orders.")
    }
    assert declared == set(CONTEXTS)


@pytest.mark.parametrize("kind", sorted(CONTEXTS))
@pytest.mark.parametrize("lang", LANGS)
def test_every_kind_renders_in_every_language(kind: str, lang: str) -> None:
    rendered = core_services.render_email(kind, lang, CONTEXTS[kind])
    english = core_services.render_email(kind, "en", CONTEXTS[kind])
    assert html_violations(rendered.html_body) == []
    assert "{{" not in rendered.text_body + rendered.html_body
    assert f'lang="{lang}"' in rendered.html_body
    if lang == "en":
        return
    assert rendered.subject != english.subject
    assert rendered.text_body != english.text_body
    if lang in FOREIGN:
        everything = rendered.subject + "\n" + rendered.text_body + "\n" + rendered.html_body
        assert ENGLISH.findall(everything) == []


def test_german_french_and_spanish_bodies_use_their_own_words() -> None:
    expected = {
        "de": "bestellung",
        "fr": "commande",
        "es": "pedido",
    }
    for lang, word in expected.items():
        context = CONTEXTS["orders.order_received"]
        rendered = core_services.render_email("orders.order_received", lang, context)
        assert word in rendered.subject.lower()


def _vendor_mails() -> list[OutboxEmail]:
    return list(
        OutboxEmail.objects.filter(
            template_key__in=("orders.vendor_new_order", "orders.vendor_order_cancelled_notice")
        )
    )


def test_a_vendor_with_a_mail_language_gets_its_mails_in_it() -> None:
    order = two_vendor_order(language="it")
    first, second = order.vendor_orders.order_by("id")
    Vendor.objects.filter(pk=first.vendor_id).update(mail_language="de")
    pay(order)
    services.transition_vendor_order(first, "cancelled", ctx=CTX)
    own = [m for m in _vendor_mails() if m.recipient == first.vendor.contact_email]
    assert {m.template_key for m in own} == {
        "orders.vendor_new_order",
        "orders.vendor_order_cancelled_notice",
    }
    assert {m.language for m in own} == {"de"}
    other = [m for m in _vendor_mails() if m.recipient == second.vendor.contact_email]
    assert {m.language for m in other} == {settings.LANGUAGE_CODE}  # no mail language: platform
    text = render(own[0]).text_body
    assert ENGLISH.findall(text) == []


def test_a_vendor_language_that_is_not_configured_falls_back_to_the_platform_language() -> None:
    order = two_vendor_order()
    (sub, _other) = order.vendor_orders.order_by("id")
    Vendor.objects.filter(pk=sub.vendor_id).update(mail_language="xx")
    pay(order)
    mail = OutboxEmail.objects.get(
        template_key="orders.vendor_new_order", recipient=sub.vendor.contact_email
    )
    assert mail.language == settings.LANGUAGE_CODE


def test_the_customer_language_never_changes_the_vendor_language() -> None:
    order = two_vendor_order(language="fr")
    sub = order.vendor_orders.order_by("id").first()
    assert sub is not None
    Vendor.objects.filter(pk=sub.vendor_id).update(mail_language="es")
    pay(order)
    vendor_mail = OutboxEmail.objects.get(
        template_key="orders.vendor_new_order", recipient=sub.vendor.contact_email
    )
    customer_mail = OutboxEmail.objects.get(template_key="orders.payment_confirmed")
    assert (vendor_mail.language, customer_mail.language) == ("es", "fr")
