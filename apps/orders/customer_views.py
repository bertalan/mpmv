"""The customer area "My orders" (ORD.7): list and detail of the signed-in customer's orders.

The queryset always starts from `services.customer_orders(request.user)`: no view reads a user
id from the input, and an order that is not the caller's is a 404, the same answer as for an id
that does not exist. A vendor part shows its own status timeline and tracking (ORD.6 data); the
commission, the vendor's support notes, the carrier's phone and the payment reference are never
passed to the template. The order reference is not an invoice: the platform issues none.

ORD.20 part 3: a package changed after payment shows its current version (lines and totals)
and a timeline of its versions (what changed, the money refunded, the date), without internal
notes. After an item removed as unavailable, the customer may cancel the rest of the package
(a full refund) or keep it, by POST, while the offer is open.
"""

import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render, resolve_url
from django.urls import reverse
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import User
from apps.core.audit import audit_context
from apps.core.money import CURRENCY
from apps.core.public_forms import not_anonymous

from . import amendment_engine as am
from . import amendments, engine, services, versions
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


def _change_text(change: am.AmendmentChange, before: am.VersionSnapshot | None) -> str:
    """One applied change in the customer's words (no internal note, no address)."""
    key = str(getattr(change, "line_key", ""))
    line = before.line(key) if before is not None and key else None
    title = line.title if line is not None else gettext("an item")
    match change:
        case am.RemoveLine(unavailable=True):
            return gettext("%(title)s removed as unavailable") % {"title": title}
        case am.RemoveLine():
            return gettext("%(title)s removed") % {"title": title}
        case am.ChangeQuantity(quantity=quantity):
            return gettext("%(title)s: quantity changed to %(quantity)s") % {
                "title": title,
                "quantity": quantity,
            }
        case am.ChangePrice():
            return gettext("%(title)s: lower price") % {"title": title}
        case am.ChangeCarrier(offer=offer):
            return gettext("Shipping method changed to %(method)s") % {
                "method": offer.label or offer.key
            }
        case am.ChangeAddress():
            return gettext("Delivery address corrected as you asked")
        case _:
            return gettext("Order changed")


def _versions(sub: VendorOrder) -> list[dict[str, Any]]:
    return [
        {
            "number": version.number,
            "at": version.created_at,
            "changes": [_change_text(c, version.before) for c in version.changes]
            if version.changes
            else [gettext("Order placed and paid")],
            "refund": _money(version.refund) if version.refund > 0 else "",
            "total": _money(version.gross_total),
        }
        for version in amendments.applied_versions(sub)
    ]


def _offer(sub: VendorOrder, order: Order) -> dict[str, Any] | None:
    """The open cancel-the-rest offer of a package, with its actions and the hold's end."""
    if sub.cancel_rest_offered_at is None or sub.state not in am.AMENDABLE_VENDOR_ORDER_STATES:
        return None
    return {
        "hold_until": sub.ship_hold_until,
        "cancel_url": reverse("customer_order_cancel_rest", args=[order.public_id, sub.public_id]),
        "keep_url": reverse("customer_order_keep_rest", args=[order.public_id, sub.public_id]),
    }


def _version_lines(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "title": line.title,
            "variant": line.variant_label,
            "quantity": line.quantity,
            "total": _money(line.gross),
        }
        for line in am.VersionSnapshot.from_json(snapshot).lines
    ]


def _part(sub: VendorOrder, order: Order, templates: dict[str, str]) -> dict[str, Any]:
    steps = engine.vendor_timeline(
        sub.state,
        placed_at=order.created_at,
        paid_at=order.paid_at,
        shipped_at=sub.shipped_at,
        delivered_at=sub.delivered_at,
    )
    shown_tracking = bool(sub.tracking_number)
    version = versions.current_version(sub) if sub.current_version_id is not None else None
    amended = version is not None and version.number > 1
    if amended:
        assert version is not None  # noqa: S101  # amended implies a version
        snapshot = am.VersionSnapshot.from_json(version.snapshot)
        lines = _version_lines(version.snapshot)
        shipping = _money(snapshot.shipping.net + snapshot.shipping.tax)
        total = _money(version.gross_total)
    else:
        lines = [_line(line) for line in sub.lines.all()]
        shipping = _money(sub.shipping_net + sub.shipping_tax_amount)
        total = _money(sub.gross_total)
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
        "lines": lines,
        "shipping": shipping,
        "total": total,
        "versions": _versions(sub) if amended else [],
        "offer": _offer(sub, order),
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


def _own_package(request: HttpRequest, public_id: uuid.UUID, package: uuid.UUID) -> VendorOrder:
    user: User = request.user  # type: ignore[assignment]
    order = services.customer_order(user, public_id)
    if order is None:
        raise Http404
    sub = order.vendor_orders.filter(public_id=package).first()
    if sub is None:
        raise Http404
    return sub


@not_anonymous(
    reason="signed in: the customer of the order answers for their own package",
    purpose="Cancel the rest of a package after an item was unavailable",
    task="ORD.20",
)
@require_POST
@never_cache
@_signed_in
def cancel_rest(request: HttpRequest, public_id: uuid.UUID, package: uuid.UUID) -> HttpResponse:
    """The customer cancels the rest of a package after an item was unavailable: a full refund
    of what is left (ORD.20)."""
    sub = _own_package(request, public_id, package)
    try:
        amendments.cancel_rest_of_package(sub, actor=request.user, ctx=audit_context(request))  # type: ignore[arg-type]
    except amendments.AmendmentRefused as error:
        messages.error(request, str(error))
    else:
        messages.success(
            request,
            gettext(
                "The rest of the package is cancelled. You get back everything you paid for "
                "it, credited within 5 to 10 business days depending on the card issuer, and "
                "in any case within 14 days."
            ),
        )
    return HttpResponseRedirect(reverse("customer_order", args=[public_id]))


@not_anonymous(
    reason="signed in: the customer of the order answers for their own package",
    purpose="Keep the rest of a package after an item was unavailable",
    task="ORD.20",
)
@require_POST
@never_cache
@_signed_in
def keep_rest(request: HttpRequest, public_id: uuid.UUID, package: uuid.UUID) -> HttpResponse:
    """The customer keeps the rest of the package: the seller may ship it now (ORD.20)."""
    sub = _own_package(request, public_id, package)
    try:
        amendments.keep_rest_of_package(sub, actor=request.user, ctx=audit_context(request))  # type: ignore[arg-type]
    except amendments.AmendmentRefused as error:
        messages.error(request, str(error))
    else:
        messages.success(request, gettext("Thank you. The seller can now ship the rest."))
    return HttpResponseRedirect(reverse("customer_order", args=[public_id]))
