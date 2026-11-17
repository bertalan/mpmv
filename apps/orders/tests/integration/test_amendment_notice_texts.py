"""ORD.20 part 3b: the amendment and cancellation notices render in the five languages.

Every kind and branch renders in EN, IT, DE, FR and ES without template leftovers or English
words in the German, French and Spanish texts; each customer notice says what changed, what it
costs and what to do (owner, 2026-11-16); the invoice notice has the CMP.24 wording in IT and EN;
no notice states a 30-day refund term; the CMP.24 wording version follows the text.
"""

import itertools
import re
from typing import Any

import pytest

from apps.core import services as core_services
from apps.core.mail import html_violations
from apps.orders import amendment_notices as notices
from apps.orders.tests.control import plainly
from apps.orders.tests.factories import OrderEventFactory
from apps.orders.tests.integration.test_email_languages import ENGLISH

pytestmark = pytest.mark.django_db

LANGS = ("en", "it", "de", "fr", "es")
FOREIGN = ("de", "fr", "es")

BASE: dict[str, Any] = {
    "order_number": "MP-1001",
    "vendor_name": "Shop X",
    "version": 2,
    "reason": "Out of stock",
    "changes": ["B removed as unavailable"],
    "items": "B",
    "lines": [{"title": "Cup", "variant": "Blue", "quantity": 2}],
    "ship_to": ["1 Via Test", "Roma", "IT"],
    "refund": "36.60",
    "total": "134.20",
    "hold_until": "18/11/2026 11:00",
    "cancel_url": "https://example.test/account/orders/x/",
    "order_url": "https://example.test/account/orders/x/",
}
VARIANTS: dict[str, list[dict[str, Any]]] = {
    notices.AMENDMENT_APPLIED: [
        {"refunded": r, "by_staff": s} for r, s in itertools.product((True, False), repeat=2)
    ],
    notices.ITEM_UNAVAILABLE: [
        {"refunded": True},
        {"refunded": True, "cancel_url": "", "order_url": "", "support_email": "help@example.test"},
        {"refunded": True, "cancel_url": "", "order_url": ""},
    ],
    notices.PACKAGE_CANCELLED: [
        {"cause": c} for c in ("vendor_fault", "customer_request", "platform_error")
    ],
    notices.REST_CANCELLED: [{}],
    notices.VENDOR_AMENDMENT_APPLIED: [
        {"by_staff": s, "refunded": r, "invoice_issued": i}
        for s, r, i in itertools.product((True, False), repeat=3)
    ],
    notices.VENDOR_PACKAGE_CANCELLED: [
        {"by": b, "cause": c, "refund_fee": c == "vendor_fault", "invoice_issued": i}
        for b, c, i in itertools.product(
            ("shop", "staff", "customer"), ("vendor_fault", "customer_request"), (True, False)
        )
    ]
    + [{"by": "staff", "cause": "platform_error", "refund_fee": False, "invoice_issued": False}],
}
CASES = [(kind, i) for kind, variants in VARIANTS.items() for i in range(len(variants))]


def test_every_notice_kind_is_covered() -> None:
    assert set(VARIANTS) == set(notices.KINDS)


@pytest.mark.parametrize(("kind", "index"), CASES)
@pytest.mark.parametrize("lang", LANGS)
def test_every_notice_renders_in_every_language(kind: str, index: int, lang: str) -> None:
    context = {**BASE, **VARIANTS[kind][index]}
    rendered = core_services.render_email(kind, lang, context)
    english = core_services.render_email(kind, "en", context)
    everything = rendered.subject + "\n" + rendered.text_body + "\n" + rendered.html_body
    assert html_violations(rendered.html_body) == []
    assert "{{" not in everything
    assert "{%" not in everything
    assert f'lang="{lang}"' in rendered.html_body
    assert not re.search(r"\b30\b", rendered.text_body), plainly(
        "no notice may state a 30-day refund term", rendered.text_body
    )
    if lang == "en":
        return
    assert rendered.subject != english.subject
    assert rendered.text_body != english.text_body
    if lang in FOREIGN:
        assert ENGLISH.findall(everything) == [], plainly(
            f"the {lang} {kind} notice must not fall back to English", everything
        )


def render(kind: str, lang: str = "en", **extra: Any) -> str:
    return core_services.render_email(kind, lang, {**BASE, **extra}).text_body


def test_the_a2_notice_says_what_changed_what_it_costs_what_to_do_and_by_when() -> None:
    text = render(notices.ITEM_UNAVAILABLE, refunded=True)
    for needed, why in [
        ("no longer has B in stock", "the item that is unavailable"),
        ("36.60 EUR (VAT and any shipping share included)", "the refund with VAT and shipping"),
        ("in any case within 14 days", "the credit-time wording"),
        ("new total of 134.20 EUR", "that the order goes on at the new total"),
        ("cancel the rest of the order free of charge", "the right to cancel the rest"),
        ("will not ship it before 18/11/2026 11:00", "the deadline with date and time"),
        ("Cancel the rest of the order and refund everything: https://", "the action"),
    ]:
        assert needed in text, plainly(f"the A2 notice must state {why}", text)


def test_the_change_notice_says_that_nothing_is_due_when_nothing_is_refunded() -> None:
    text = render(notices.AMENDMENT_APPLIED, refunded=False, by_staff=False)
    assert "You pay nothing more, and there is nothing you need to do." in text
    assert "refunded" not in text


@pytest.mark.parametrize(
    ("lang", "wording"),
    [
        (
            "en",
            "Order changed after payment: if you already issued an invoice or receipt for the "
            "previous version, remember to issue a credit or debit note to bring the document in "
            "line with the new amounts.",
        ),
        (
            "it",
            "Ordine modificato dopo il pagamento: se hai già emesso fattura o scontrino per la "
            "versione precedente, ricorda di emettere nota di variazione/rettifica per allineare "
            "il documento ai nuovi importi.",
        ),
    ],
)
def test_the_invoice_notice_has_the_cmp24_wording(lang: str, wording: str) -> None:
    for issued in (True, False):
        text = render(
            notices.VENDOR_AMENDMENT_APPLIED,
            lang,
            by_staff=False,
            refunded=True,
            invoice_issued=issued,
        )
        assert wording in text, plainly(f"the {lang} vendor notice must carry the wording", text)
        assert text.startswith(wording) is False
        assert (text.split("\n", 1)[0].endswith(wording)) is issued, plainly(
            "the notice opens the email exactly when the invoice is recorded", text
        )


def test_the_staff_change_notice_names_platform_support() -> None:
    subject = core_services.render_email(
        notices.VENDOR_AMENDMENT_APPLIED,
        "en",
        {**BASE, "by_staff": True, "refunded": False, "invoice_issued": False},
    ).subject
    assert subject == "Shop X: platform support changed order MP-1001"


def test_the_wording_version_follows_the_language_and_the_branch() -> None:
    version = notices.wording_version
    a2 = version(notices.ITEM_UNAVAILABLE, "en", (("refunded", True),))
    assert a2.startswith("sha256:")
    assert a2 == version(notices.ITEM_UNAVAILABLE, "en", (("refunded", True),))
    assert a2 != version(notices.ITEM_UNAVAILABLE, "it", (("refunded", True),))
    assert a2 != version(notices.ITEM_UNAVAILABLE, "en", (("linked", False), ("refunded", True)))
    fault = version(notices.PACKAGE_CANCELLED, "en", (("cause", "vendor_fault"),))
    assert fault != version(notices.PACKAGE_CANCELLED, "en", (("cause", "platform_error"),))


def test_a_kept_notice_refuses_edits_and_deletes() -> None:
    event = OrderEventFactory.create()
    with pytest.raises(PermissionError):
        event.save()
    with pytest.raises(PermissionError):
        event.delete()


INFORMAL = {
    "de": re.compile(r"\b(du|dein|deine|deinem|deinen|dir|dich|versende)\b", re.IGNORECASE),
    "fr": re.compile(r"\b(tu|ta|ton|tes|toi|expédie)\b", re.IGNORECASE),
    "es": re.compile(r"\b(tu|tú|tus|envía|envíes)\b", re.IGNORECASE),
}
VENDOR_KINDS = (
    notices.VENDOR_AMENDMENT_APPLIED,
    notices.VENDOR_PACKAGE_CANCELLED,
    "orders.vendor_new_order",
    "orders.vendor_order_cancelled_notice",
)


@pytest.mark.parametrize("lang", FOREIGN)
@pytest.mark.parametrize("kind", VENDOR_KINDS)
def test_vendor_emails_use_the_formal_register(kind: str, lang: str) -> None:
    """Vendor-facing texts are formal (Sie, vous, usted); only customer emails are informal."""
    variants = VARIANTS.get(kind) or [{}]
    for variant in variants:
        rendered = core_services.render_email(kind, lang, {**BASE, **variant})
        found = INFORMAL[lang].findall(rendered.subject + "\n" + rendered.text_body)
        assert found == [], plainly(f"the {lang} {kind} email must be formal", str(found))


# --- counsel's reference texts (2026-11-17) ------------------------------------------------------


def test_the_guest_a2_names_the_support_address_and_no_reply() -> None:
    text = render(
        notices.ITEM_UNAVAILABLE,
        refunded=True,
        cancel_url="",
        order_url="",
        support_email="help@example.test",
    )
    assert "since you checked out as a guest, simply contact support at help@example.test" in text
    assert "will not ship it before 18/11/2026 11:00" in text
    assert "reply" not in text.lower(), plainly(
        "the outbox sets no reply-to, so the guest text must not ask for a reply", text
    )


def test_the_platform_error_texts_are_counsel_s() -> None:
    subject = core_services.render_email(
        notices.PACKAGE_CANCELLED, "en", {**BASE, "cause": "platform_error"}
    ).subject
    assert subject == "Cancellation of order MP-1001 due to a technical error and full refund"
    text = render(notices.PACKAGE_CANCELLED, cause="platform_error")
    assert "Due to a technical issue on our platform" in text
    assert "statutory 14-day limit" in text
    vendor = render(
        notices.VENDOR_PACKAGE_CANCELLED,
        by="staff",
        cause="platform_error",
        refund_fee=False,
        invoice_issued=False,
    )
    assert "no refund fee is charged to you" in vendor


@pytest.mark.parametrize(
    ("lang", "start"),
    [
        ("de", "Bestellung nach Zahlungseingang geändert: Falls Sie"),
        ("fr", "Commande modifiée après le paiement : si vous avez déjà émis"),
        ("es", "Pedido modificado tras el pago: si ya ha emitido"),
    ],
)
def test_the_invoice_notice_is_counsel_s_formal_text(lang: str, start: str) -> None:
    text = render(
        notices.VENDOR_AMENDMENT_APPLIED, lang, by_staff=False, refunded=False, invoice_issued=True
    )
    assert start in text, plainly(f"the {lang} invoice notice must be counsel's", text)


def test_the_wording_provenance_is_recorded() -> None:
    assert notices.WORDING_APPROVAL["approved_on"] == "2026-11-17"
    assert notices.WORDING_APPROVAL["translator_review_pending"] == ["de", "fr", "es"]
