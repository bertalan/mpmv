"""Transactional order emails (ORD.9), queued through the ARC.16 outbox.

Each `notify_*` function puts one or more emails in the outbox, in the caller's transaction:
a rolled-back order or payment sends nothing, and the outbox row and its send job commit
together. Each is idempotent per event (an outbox idempotency key), so a replayed webhook or a
retried job never double-sends. They only read the order: callers (the order services,
`place_order`, the payment webhook) decide when the event happened.

Privacy (D-30, spec 07): nothing is sent for an order that is no longer `active`. The customer
gets the order total and its own lines; a vendor gets only its own sub-order and the shipping
address it must ship to, never the buyer's email, billing address or other vendors' lines. No
payment reference, commission or tax identifier of the buyer enters any context. The platform
never sends invoices or receipts: the per-vendor confirmation is a non-fiscal document.

Emails render in the order's language snapshot, English when it is not configured.
"""

import logging
from decimal import Decimal
from typing import Any

from django.conf import settings

from apps.core.services import enqueue_email as _enqueue_email

from .models import Order, OrderLine, PrivacyStatus, VendorOrder

logger = logging.getLogger(__name__)


def enqueue_email(**kwargs: Any) -> None:
    """Queue one email; a refused one (malformed recipient, SEC.5) is logged and skipped so it
    never rolls back the order or payment transition that called us. Nothing was written."""
    try:
        _enqueue_email(**kwargs)
    except ValueError as exc:
        logger.warning(
            "order email %s not queued: %s", kwargs.get("template_key"), exc.__class__.__name__
        )


ORDER_RECEIVED = "orders.order_received"
PAYMENT_CONFIRMED = "orders.payment_confirmed"
ORDER_CONFIRMATION = "orders.order_confirmation"
VENDOR_NEW_ORDER = "orders.vendor_new_order"
VENDOR_ORDER_SHIPPED = "orders.vendor_order_shipped"

VENDOR_ORDER_DELIVERED = "orders.vendor_order_delivered"
ORDER_CANCELLED = "orders.order_cancelled"
VENDOR_ORDER_CANCELLED = "orders.vendor_order_cancelled"
VENDOR_ORDER_CANCELLED_NOTICE = "orders.vendor_order_cancelled_notice"

#: The shipping address lines a vendor needs to ship; nothing else of the buyer's data.
_SHIP_TO_KEYS = ("name", "line1", "line2", "postal_code", "city", "region")


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _sendable(order: Order) -> bool:
    return order.privacy_status == PrivacyStatus.ACTIVE and bool(order.email)


def _line(line: OrderLine) -> dict[str, Any]:
    return {
        "title": line.title,
        "variant": line.variant_label,
        "quantity": line.quantity,
        "total": _money(line.net_total + line.tax_amount),
    }


def _lines(sub: VendorOrder) -> list[dict[str, Any]]:
    return [_line(line) for line in sub.lines.order_by("id")]


def _ship_to(order: Order) -> list[str]:
    address = order.shipping_address or {}
    parts = [str(address.get(key, "")).strip() for key in _SHIP_TO_KEYS]
    return [part for part in parts if part] + [order.shipping_country]


def _vendor_name(sub: VendorOrder) -> str:
    return str(sub.vendor_snapshot.get("display_name") or sub.vendor.display_name)


def notify_order_placed(order: Order) -> None:
    """Customer: order received."""
    if not _sendable(order):
        return
    enqueue_email(
        recipient=order.email,
        template_key=ORDER_RECEIVED,
        language=order.language,
        idempotency_key=f"order:{order.public_id}:received",
        context={
            "order_number": order.number,
            "customer_name": order.customer_name,
            "total": _money(order.grand_total),
        },
    )


def notify_order_paid(order: Order) -> None:
    """Customer: payment confirmed, plus one non-fiscal confirmation per vendor. Each vendor:
    new order (only after payment)."""
    if not _sendable(order):
        return
    language = order.language
    base = {"order_number": order.number, "customer_name": order.customer_name}
    enqueue_email(
        recipient=order.email,
        template_key=PAYMENT_CONFIRMED,
        language=language,
        idempotency_key=f"order:{order.public_id}:paid",
        context={**base, "total": _money(order.grand_total)},
    )
    for sub in order.vendor_orders.select_related("vendor").order_by("id"):
        enqueue_email(
            recipient=order.email,
            template_key=ORDER_CONFIRMATION,
            language=language,
            idempotency_key=f"vendor-order:{sub.public_id}:confirmation",
            context={
                **base,
                "vendor_order_ref": str(sub.public_id),
                "vendor_name": _vendor_name(sub),
                "lines": _lines(sub),
                "shipping": _money(sub.shipping_net + sub.shipping_tax_amount),
                "total": _money(sub.gross_total),
            },
        )
        if sub.vendor.contact_email:
            enqueue_email(
                recipient=sub.vendor.contact_email,
                template_key=VENDOR_NEW_ORDER,
                language=settings.LANGUAGE_CODE,  # vendors have no language of their own yet
                idempotency_key=f"vendor-order:{sub.public_id}:new",
                context={
                    "order_number": order.number,
                    "vendor_name": _vendor_name(sub),
                    "lines": _lines(sub),
                    "ship_to": _ship_to(order),
                    "total": _money(sub.gross_total),
                },
            )


def _vendor_event(sub: VendorOrder, kind: str, event: str) -> None:
    """Customer mail about one sub-order, once per sub-order and event."""
    order = sub.order
    if not _sendable(order):
        return
    enqueue_email(
        recipient=order.email,
        template_key=kind,
        language=order.language,
        idempotency_key=f"vendor-order:{sub.public_id}:{event}",
        context={
            "order_number": order.number,
            "customer_name": order.customer_name,
            "vendor_name": _vendor_name(sub),
        },
    )


def notify_vendor_order_delivered(sub: VendorOrder) -> None:
    """Customer: this vendor's part of the order was delivered."""
    _vendor_event(sub, VENDOR_ORDER_DELIVERED, "delivered")


def notify_order_cancelled(order: Order) -> None:
    """Customer: the order was cancelled (before payment, so nothing was charged)."""
    if not _sendable(order):
        return
    enqueue_email(
        recipient=order.email,
        template_key=ORDER_CANCELLED,
        language=order.language,
        idempotency_key=f"order:{order.public_id}:cancelled",
        context={"order_number": order.number, "customer_name": order.customer_name},
    )


def notify_vendor_order_cancelled(sub: VendorOrder) -> None:
    """Customer and vendor: one vendor's part of a paid order was cancelled. The vendor gets
    only its own lines, never the buyer's data."""
    order = sub.order
    _vendor_event(sub, VENDOR_ORDER_CANCELLED, "cancelled")
    if _sendable(order) and sub.vendor.contact_email:
        enqueue_email(
            recipient=sub.vendor.contact_email,
            template_key=VENDOR_ORDER_CANCELLED_NOTICE,
            language=settings.LANGUAGE_CODE,
            idempotency_key=f"vendor-order:{sub.public_id}:cancelled-notice",
            context={
                "order_number": order.number,
                "vendor_name": _vendor_name(sub),
                "lines": _lines(sub),
            },
        )


def notify_vendor_order_shipped(sub: VendorOrder) -> None:
    """Customer: this vendor's part of the order has shipped (with tracking)."""
    order = sub.order
    if not _sendable(order):
        return
    enqueue_email(
        recipient=order.email,
        template_key=VENDOR_ORDER_SHIPPED,
        language=order.language,
        idempotency_key=f"vendor-order:{sub.public_id}:shipped",
        context={
            "order_number": order.number,
            "customer_name": order.customer_name,
            "vendor_name": _vendor_name(sub),
            "carrier": sub.carrier,
            "tracking_number": sub.tracking_number,
        },
    )
