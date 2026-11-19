"""The customer area "My orders" (ORD.7): list and detail of the signed-in customer's orders.

The queryset always starts from `services.customer_orders(request.user)`: no view reads a user
id from the input, and an order that is not the caller's is a 404, the same answer as for an id
that does not exist. A vendor part shows its own status timeline and tracking (ORD.6 data); the
commission, the vendor's support notes, the carrier's phone and the payment reference are never
passed to the template. The order reference is not an invoice: the platform issues none.

ORD.20 part 3: a package changed after payment shows its current version (lines and totals)
and a timeline of its versions (what changed, the money refunded, the date), without internal
notes. After an item removed as unavailable, the customer may cancel the rest of the package
(a full refund) or keep it, by POST, while the offer is open. Part 3b: the notices sent about the
package (changes, cancellations) are listed with the exact text of their emails.
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
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import User
from apps.core.audit import audit_context
from apps.core.money import CURRENCY
from apps.core.public_forms import not_anonymous

from . import amendment_engine as am
from . import amendment_notices, amendments, engine, reorder, services, versions
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
REORDER_SKIPPED = {
    engine.REORDER_RETIRED: _("not sold any more"),
    engine.REORDER_OUT_OF_STOCK: _("out of stock"),
    engine.REORDER_NOT_DELIVERABLE: _("cannot be delivered to your address"),
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
    return amendment_notices.change_text(change, before)


def _versions(sub: VendorOrder) -> list[dict[str, Any]]:
    """The versions of a changed package, then its cancellations (an entry without a version)."""
    found = [
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
    for cancelled in amendments.package_cancellations(sub):
        found.append(
            {
                "number": None,
                "at": cancelled.at,
                "changes": [
                    gettext("Rest of the package cancelled at your request")
                    if cancelled.by_customer
                    else gettext("Package cancelled")
                ],
                "refund": _money(cancelled.refunded) if cancelled.refunded > 0 else "",
                "total": "",
            }
        )
    return found


def _offer(sub: VendorOrder, order: Order) -> dict[str, Any] | None:
    """The open cancel-the-rest offer of a package, with its actions and the hold's end."""
    if sub.cancel_rest_offered_at is None or sub.state not in am.AMENDABLE_VENDOR_ORDER_STATES:
        return None
    held = sub.ship_hold_until is not None and sub.ship_hold_until > timezone.now()
    return {
        # Only a future date: once the hold is over the seller may ship, and the offer stays
        # open until it does (counsel clause).
        "hold_until": sub.ship_hold_until if held else None,
        "can_keep": held,
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
    history = _versions(sub) if sub.current_version_id is not None else []
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
        "versions": history if (amended or len(history) > 1) else [],
        "offer": _offer(sub, order),
        # The notices sent about this package, with their exact text (durable medium, ORD.20).
        "notices": [
            {"at": notice.created_at, "subject": notice.subject, "text": notice.text}
            for notice in amendment_notices.customer_notices(sub)
        ],
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
        "public_id": order.public_id,
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


def _reorder_name(title: str, variant: str) -> str:
    return f"{title} ({variant})" if variant else title


@not_anonymous(
    reason="signed in: the customer puts their own past order back in their cart",
    purpose="Order again: refill the cart from a past order",
    task="ORD.31",
)
@require_POST
@never_cache
@_signed_in
def order_again(request: HttpRequest, public_id: uuid.UUID) -> HttpResponse:
    """Put the still-available items of a past order back in the cart at today's prices (ORD.31).
    Nothing is placed or paid: the customer goes on to the normal checkout."""
    user: User = request.user  # type: ignore[assignment]
    result = reorder.order_again(user, public_id, ctx=audit_context(request))
    if result is None:
        raise Http404
    if result.added:
        messages.success(
            request,
            gettext("Put back in your cart, at today's prices: %(items)s.")
            % {"items": ", ".join(_reorder_name(i.title, i.variant_label) for i in result.added)},
        )
    for item in result.added:
        if item.capped:
            messages.warning(
                request,
                gettext(
                    "%(item)s: only %(quantity)d available, so your cart holds %(quantity)d "
                    "instead of %(requested)d."
                )
                % {
                    "item": _reorder_name(item.title, item.variant_label),
                    "quantity": item.quantity,
                    "requested": item.requested,
                },
            )
    for skipped in result.skipped:
        messages.warning(
            request,
            gettext("Not added: %(item)s, %(reason)s.")
            % {
                "item": _reorder_name(skipped.title, skipped.variant_label),
                "reason": REORDER_SKIPPED[skipped.reason],
            },
        )
    if not result.added and not result.skipped:
        messages.info(request, gettext("There was nothing to add to your cart."))
    return HttpResponseRedirect(reverse("orders:cart"))
