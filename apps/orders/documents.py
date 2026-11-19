"""The per-vendor non-fiscal order confirmation PDF (ORD.9 3/3, ADR-0010).

`render_vendor_confirmation_pdf(vendor_order)` is a pure function of the order snapshots: the
vendor's name when ordered, its own lines, its shipping and its total. It is rendered when the
confirmation email is sent (in the queue worker, never in a request) and stored nowhere. The
platform never issues invoices or receipts, so the document says "non-fiscal" in the order's
language and carries the order reference, never an invoice number.

WeasyPrint runs only in `pdf_render` (no Django), in a child process with a hard timeout and no
application secrets; see there for the locked-down fetcher (`data:` URIs and static files only, no
network, no redirects). The template is autoescaped and loads no resource at all. WeasyPrint is
never imported in the worker itself, because the build stage and the web process do not need its
system libraries.
"""

import logging
from typing import Any

from django.template.loader import render_to_string
from django.utils import translation

from apps.core import mail
from apps.core.pdf import render_html
from apps.core.services import AttachmentUnavailable, email_language

from .models import PrivacyStatus, VendorOrder
from .notifications import CONFIRMATION_PDF_KIND, _lines, _money, _vendor_name

logger = logging.getLogger(__name__)

ATTACHMENT_KIND = CONFIRMATION_PDF_KIND
TEMPLATE = "documents/orders/vendor_confirmation.html"


class ConfirmationUnavailable(AttachmentUnavailable):
    """No document for this order: it is not `active` any more (D-30) or does not exist."""


def _context(sub: VendorOrder, language: str) -> dict[str, Any]:
    order = sub.order
    return {
        "email_language": language,
        "order_number": order.number,
        "vendor_name": _vendor_name(sub),
        "lines": _lines(sub),
        "shipping": _money(sub.shipping_net + sub.shipping_tax_amount),
        "total": _money(sub.gross_total),
    }


def render_vendor_confirmation_pdf(vendor_order: VendorOrder) -> bytes:
    """The non-fiscal confirmation of one vendor's part of an order, in the order's language
    (English when it is not configured). Raises `ConfirmationUnavailable` when the order is not
    `active` (nothing is produced for a restricted or anonymised order)."""
    order = vendor_order.order
    if order.privacy_status != PrivacyStatus.ACTIVE:
        raise ConfirmationUnavailable("order is not active")
    language = email_language(order.language)
    with translation.override(language):
        html = render_to_string(TEMPLATE, _context(vendor_order, language))
    return render_html(html)


def vendor_confirmation_attachment(ref: str, recipient: str) -> mail.Attachment:
    """The email attachment for the vendor order with public id `ref`, for an email to
    `recipient` (the outbox builds it when the email is sent). The document is the buyer's, so it
    is built only when `recipient` is the order's own address: a forged reference in an outbox
    context never sends one buyer's document to someone else. Raises `AttachmentUnavailable`
    when it cannot or may not be produced, or is over the time or size cap; the email then goes
    out without it."""
    sub = (
        VendorOrder.objects.select_related("order", "vendor").filter(public_id=ref).first()
        if _is_uuid(ref)
        else None
    )
    if sub is None:
        raise ConfirmationUnavailable("vendor order not found")
    if mail.normalise_address(sub.order.email) != mail.normalise_address(recipient):
        raise AttachmentUnavailable("attachment does not belong to the recipient")
    pdf = render_vendor_confirmation_pdf(sub)
    return mail.Attachment(
        filename=f"order-{sub.order.number}-{str(sub.public_id)[:8]}.pdf",
        content_type="application/pdf",
        content=pdf,
    )


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True
