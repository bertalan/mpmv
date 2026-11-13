"""The customer area "My orders" (ORD.7): list and detail of the signed-in customer's orders.

The queryset always starts from `services.customer_orders(request.user)`: no view reads a user
id from the input, and an order that is not the caller's is a 404, the same answer as for an id
that does not exist. A vendor part shows its own status timeline and tracking (ORD.6 data); the
commission, the vendor's support notes, the carrier's phone and the payment reference are never
passed to the template. The order reference is not an invoice: the platform issues none.
"""

import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render, resolve_url
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.accounts.models import User
from apps.core.money import CURRENCY

from . import engine, services
from .models import Order, OrderLine, VendorOrder

PAGE_SIZE = 20

type View = Callable[..., HttpResponse]

# What the customer reads for each state. Plain words, no internal state names.
ORDER_STATUS = {
    "quote_flow": _("Quote requested"),
    "awaiting_payment": _("Waiting for payment"),
    "paid": _("Paid"),
    "in_fulfillment": _("Being prepared"),
    "fulfilled": _("Delivered"),
    "closed": _("Completed"),
    "cancelled": _("Cancelled"),
    "partially_refunded": _("Partly refunded"),
    "refunded": _("Refunded"),
}
PART_STATUS = {
    "pending": _("Waiting for the seller"),
    "accepted": _("Being prepared"),
    "shipped": _("Shipped"),
    "delivered": _("Delivered"),
    "completed": _("Completed"),
    "cancelled": _("Cancelled"),
    "refunded": _("Refunded"),
}
STEP_LABEL = {
    "placed": _("Order placed"),
    "paid": _("Payment received"),
    "accepted": _("Accepted by the seller"),
    "shipped": _("Shipped"),
    "delivered": _("Delivered"),
    "cancelled": _("Cancelled"),
    "refunded": _("Refunded"),
}
STEP_STATE_LABEL = {
    "done": _("done"),
    "current": _("current step"),
    "upcoming": _("not yet"),
}


def _signed_in(view: View) -> View:
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), resolve_url(settings.MFA_LOGIN_URL))
        return view(request, *args, **kwargs)

    wrapped.__name__ = view.__name__
    wrapped.__doc__ = view.__doc__
    return wrapped


def _money(value: Decimal) -> str:
    return f"{value:.2f} {CURRENCY}"


def _line(line: OrderLine) -> dict[str, Any]:
    return {
        "title": line.title,
        "variant": line.variant_label,
        "quantity": line.quantity,
        "total": _money(line.net_total + line.tax_amount),
    }


def _part(sub: VendorOrder, order: Order, templates: dict[str, str]) -> dict[str, Any]:
    steps = engine.vendor_timeline(
        sub.state,
        placed_at=order.created_at,
        paid_at=order.paid_at,
        shipped_at=sub.shipped_at,
        delivered_at=sub.delivered_at,
    )
    shown_tracking = bool(sub.tracking_number)
    return {
        "vendor_name": sub.vendor_snapshot.get("display_name") or sub.vendor.display_name,
        "status": PART_STATUS.get(sub.state, sub.state),
        "steps": [
            {
                "label": STEP_LABEL[step.key],
                "status": step.status,
                "status_label": STEP_STATE_LABEL[step.status],
                "at": step.at,
            }
            for step in steps
        ],
        "lines": [_line(line) for line in sub.lines.all()],
        "shipping": _money(sub.shipping_net + sub.shipping_tax_amount),
        "total": _money(sub.gross_total),
        "tracking": {
            "carrier": sub.carrier,
            "number": sub.tracking_number,
            "url": engine.tracking_link(templates.get(sub.carrier, ""), sub.tracking_number),
        }
        if shown_tracking
        else None,
    }


@require_GET
@never_cache
@_signed_in
def order_list(request: HttpRequest) -> HttpResponse:
    user: User = request.user  # type: ignore[assignment]
    page = Paginator(services.customer_orders(user), PAGE_SIZE).get_page(request.GET.get("page"))
    rows = [
        {
            "public_id": order.public_id,
            "number": order.number,
            "placed": order.created_at,
            "status": ORDER_STATUS.get(order.state, order.state),
            "total": _money(order.grand_total),
        }
        for order in page
    ]
    context = {
        "rows": rows,
        "orders_page": page,
        "previous_url": f"?page={page.previous_page_number()}" if page.has_previous() else "",
        "next_url": f"?page={page.next_page_number()}" if page.has_next() else "",
    }
    return render(request, "orders/customer_orders.html", context)


@require_GET
@never_cache
@_signed_in
def order_detail(request: HttpRequest, public_id: uuid.UUID) -> HttpResponse:
    user: User = request.user  # type: ignore[assignment]
    order = services.customer_order(user, public_id)
    if order is None:
        raise Http404
    subs = list(order.vendor_orders.all())
    templates = services.carrier_tracking_templates(sub.carrier for sub in subs)
    context = {
        "number": order.number,
        "placed": order.created_at,
        "status": ORDER_STATUS.get(order.state, order.state),
        "total": _money(order.grand_total),
        "parts": [_part(sub, order, templates) for sub in subs],
    }
    return render(request, "orders/customer_order.html", context)
