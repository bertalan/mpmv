"""The per-vendor non-fiscal confirmation PDF and its email attachment (ORD.9 3/3, ADR-0010)."""

import io
from collections.abc import Iterator
from typing import Any

import pytest
from pypdf import PdfReader

from apps.core import mail
from apps.core import services as core_services
from apps.core.models import OutboxEmail, OutboxEmailEvent
from apps.orders import documents, notifications
from apps.orders.models import Order, PrivacyStatus, VendorOrder
from apps.orders.tests.integration.test_notifications import BANNED, LANGS, two_vendor_order
from apps.orders.tests.integration.test_services import line_input, pay, place, vendor_input
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

LABELS = {
    "en": "Non-fiscal document",
    "it": "Documento non fiscale",
    "de": "Kein Steuerbeleg",
    "fr": "Document non fiscal",
    "es": "Documento no fiscal",
}


@pytest.fixture
def sender() -> Iterator[mail.MemoryEmailSender]:
    backend = core_services.email_sender()
    assert isinstance(backend, mail.MemoryEmailSender)
    backend.clear()
    yield backend
    backend.clear()


def reader(pdf: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(pdf))


def text_of(pdf: bytes) -> str:
    return " ".join(" ".join(page.extract_text().split()) for page in reader(pdf).pages)


def sub_orders(order: Order) -> list[VendorOrder]:
    return list(order.vendor_orders.order_by("id"))


def sub_with(order: Order, title: str) -> VendorOrder:
    return next(sub for sub in sub_orders(order) if sub.lines.filter(title=title).exists())


@pytest.mark.parametrize("lang", LANGS)
def test_the_pdf_is_per_vendor_labelled_non_fiscal_and_never_an_invoice(lang: str) -> None:
    order = two_vendor_order(language=lang)
    a = sub_with(order, "Cup from A")
    text = text_of(documents.render_vendor_confirmation_pdf(a))
    assert LABELS[lang] in text
    assert order.number in text
    assert (a.vendor_snapshot.get("display_name") or a.vendor.display_name) in text
    assert "Cup from A" in text
    assert "Bowl from B" not in text  # the other vendor's lines never appear
    assert not any(word in text.lower() for word in BANNED)


def test_the_pdf_carries_no_buyer_contact_data_or_billing_address() -> None:
    order = two_vendor_order()
    text = text_of(documents.render_vendor_confirmation_pdf(sub_orders(order)[0]))
    assert order.email not in text
    assert "9 Billing Road" not in text


def test_the_pdf_is_built_from_snapshots_not_live_rows() -> None:
    order = two_vendor_order()
    sub = sub_with(order, "Cup from A")
    name = sub.vendor_snapshot["display_name"]
    type(sub.vendor).objects.filter(pk=sub.vendor_id).update(display_name="Renamed later")
    text = text_of(documents.render_vendor_confirmation_pdf(sub))
    assert name in text
    assert "Renamed later" not in text
    total = f"{sub.gross_total:.2f}"
    assert total in text


def test_an_unknown_order_language_renders_in_english() -> None:
    order = place(language="en")
    Order.objects.filter(pk=order.pk).update(language="xx")
    sub = sub_orders(Order.objects.get(pk=order.pk))[0]
    assert LABELS["en"] in text_of(documents.render_vendor_confirmation_pdf(sub))


def test_hostile_names_are_inert_and_shown_as_text(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[str] = []
    real = documents.StaticOnlyFetcher.fetch

    def spy(self: Any, url: str, headers: Any = None) -> Any:
        fetched.append(url)
        return real(self, url, headers)

    monkeypatch.setattr(documents.StaticOnlyFetcher, "fetch", spy)
    hostile = "<img src=x onerror=alert(1)> url(http://169.254.169.254/x) </style><p>"
    vendor = VendorFactory.create(display_name=hostile)
    order = place(
        vendor_orders=[
            vendor_input(
                vendor,
                lines=[
                    line_input(title='"><link rel=attachment href=file:///etc/hostname>'),
                    line_input(title="</title><b>x</b>", sku="H-2", variant_label=hostile),
                ],
            )
        ]
    )
    text = text_of(documents.render_vendor_confirmation_pdf(sub_orders(order)[0]))
    assert "<img src=x onerror=alert(1)>" in text
    assert "</style>" in text
    assert "<link rel=attachment" in text
    assert "</title><b>x</b>" in text
    assert fetched == []  # nothing was even asked of the fetcher
    assert not reader(documents.render_vendor_confirmation_pdf(sub_orders(order)[0])).attachments


def test_the_pdf_is_tagged_with_language_and_title() -> None:
    order = two_vendor_order(language="de")
    pdf = documents.render_vendor_confirmation_pdf(sub_orders(order)[0])
    rd = reader(pdf)
    root = rd.trailer["/Root"]
    assert root["/MarkInfo"]["/Marked"] is True or bool(root["/MarkInfo"]["/Marked"])
    assert "/StructTreeRoot" in root
    assert str(root["/Lang"]).lower().startswith("de")
    title = str(rd.metadata.title)
    assert LABELS["de"] in title
    assert order.number in title


@pytest.mark.parametrize("state", [PrivacyStatus.RESTRICTED_TAX_ONLY, PrivacyStatus.ANONYMIZED])
def test_nothing_is_produced_for_an_order_that_is_not_active(state: str) -> None:
    order = two_vendor_order()
    sub = sub_orders(order)[0]
    Order.objects.filter(pk=order.pk).update(privacy_status=state)
    sub = VendorOrder.objects.get(pk=sub.pk)
    with pytest.raises(documents.ConfirmationUnavailable):
        documents.render_vendor_confirmation_pdf(sub)


# --- Delivery: attached to the confirmation email through the outbox -----------------------


def confirmations() -> list[OutboxEmail]:
    return list(OutboxEmail.objects.filter(template_key="orders.order_confirmation").order_by("id"))


def test_each_confirmation_email_carries_its_own_vendors_pdf(
    sender: mail.MemoryEmailSender,
) -> None:
    order = two_vendor_order(language="fr")
    pay(order)
    notifications.notify_order_paid(order)  # replay: no second email, no second attachment
    mails = confirmations()
    assert len(mails) == 2
    for email in mails:
        core_services.deliver_email(email.public_id)
    sent = list(sender.sent)  # only the two confirmations were delivered
    assert len(sent) == 2
    seen = set()
    for message in sent:
        (attachment,) = message.attachments
        assert attachment.content_type == "application/pdf"
        assert attachment.filename.endswith(".pdf")
        assert not any(word in attachment.filename.lower() for word in BANNED)
        assert attachment.content.startswith(b"%PDF")
        text = text_of(attachment.content)
        assert LABELS["fr"] in text
        title = "Cup from A" if "Cup from A" in message.text_body else "Bowl from B"
        other = "Bowl from B" if title == "Cup from A" else "Cup from A"
        assert title in text
        assert other not in text
        seen.add(title)
    assert seen == {"Cup from A", "Bowl from B"}


def test_the_attachment_marker_is_a_reference_not_the_document() -> None:
    order = two_vendor_order()
    pay(order)
    for email in confirmations():
        marker = email.context_data["attachment"]
        assert marker["kind"] == documents.ATTACHMENT_KIND
        assert marker["ref"] == email.context_data["vendor_order_ref"]
    # no PDF bytes are stored anywhere: only the reference is in the outbox row
    assert "%PDF" not in "".join(OutboxEmail.objects.values_list("context", flat=True))


def omitted(email: OutboxEmail) -> str:
    events = OutboxEmailEvent.objects.filter(email=email).order_by("id")
    return " ".join(event.detail for event in events)


def test_a_failed_render_sends_the_email_without_the_pdf(
    sender: mail.MemoryEmailSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(sub: VendorOrder) -> bytes:
        raise RuntimeError("renderer crashed")

    monkeypatch.setattr(documents, "render_vendor_confirmation_pdf", boom)
    pay(two_vendor_order())
    email = confirmations()[0]
    core_services.deliver_email(email.public_id)
    email.refresh_from_db()
    assert email.status == OutboxEmail.Status.SENT
    assert sender.sent[-1].attachments == ()
    assert "attachment omitted" in omitted(email)


def test_a_pdf_over_the_size_cap_is_left_out(
    sender: mail.MemoryEmailSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(documents, "MAX_PDF_BYTES", 100)
    pay(two_vendor_order())
    email = confirmations()[0]
    core_services.deliver_email(email.public_id)
    assert sender.sent[-1].attachments == ()
    assert "attachment omitted" in omitted(email)


def test_a_render_over_the_time_cap_is_left_out(
    sender: mail.MemoryEmailSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(documents, "MAX_RENDER_SECONDS", 0.0)
    pay(two_vendor_order())
    email = confirmations()[0]
    core_services.deliver_email(email.public_id)
    assert sender.sent[-1].attachments == ()
    assert "attachment omitted" in omitted(email)


def test_an_order_restricted_before_sending_gets_the_email_without_the_pdf(
    sender: mail.MemoryEmailSender,
) -> None:
    order = two_vendor_order()
    pay(order)
    Order.objects.filter(pk=order.pk).update(privacy_status=PrivacyStatus.RESTRICTED_TAX_ONLY)
    email = confirmations()[0]
    core_services.deliver_email(email.public_id)
    assert sender.sent[-1].attachments == ()


def test_an_unregistered_attachment_kind_is_left_out(sender: mail.MemoryEmailSender) -> None:
    core_services.enqueue_email(
        recipient="someone@example.com",
        template_key="orders.order_confirmation",
        language="en",
        context={
            "order_number": "X",
            "vendor_name": "V",
            "lines": [],
            "shipping": "0.00",
            "total": "0.00",
            "attachment": {"kind": "nobody.registered_this", "ref": "1"},
        },
    )
    email = OutboxEmail.objects.get()
    core_services.deliver_email(email.public_id)
    assert sender.sent[-1].attachments == ()
    assert "attachment omitted" in omitted(email)
