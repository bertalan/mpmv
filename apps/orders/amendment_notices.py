"""The notices of order amendments and package cancellations (ORD.20 part 3b, ORD.22).

Each notice is an email queued through the ARC.16 outbox (as the ORD.9 emails in
`notifications`) **and** an `OrderEvent` keeping the exact text of that email, in the
recipient's language, with the CMP.24 version of its wording (art. 51(7) Codice del consumo: a
durable medium; spec 07, Durable medium). The customer's copy also names the customer terms
version in force when it was sent. Everything is written in the caller's transaction: an
amendment rolled back sends and records nothing.

The customer is told of every applied minor change (what changed, the money refunded or that
nothing is due, the new total), of an item removed as unavailable with the cancel-the-rest offer
and its deadline (template A2 of the counsel-approved texts), and of a package cancelled in full
by the shop or platform staff, by cause (A1 for the shop's fault) or by the customer. The vendor
is told of every applied change and cancellation, with the invoice notice (CMP.24 wording,
first and marked important when it recorded `invoice_issued_at`), and of every change platform
staff make to its order (ORD.22).

The customer's name is never in these texts (the greeting is plain "Hello,"): the copy outlives
the order's active phase, and the anonymisation scrub only has to replace the text as a whole.
No internal note (the staff note, the customer's own words) enters a notice.
"""

import datetime as dt
import functools
import hashlib
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.urls import reverse
from django.utils import formats, timezone, translation
from django.utils.translation import gettext

from apps.cms import services as cms_services
from apps.core import services as core_services

from . import amendment_engine as am
from . import notifications, versions
from .models import (
    Order,
    OrderAmendment,
    OrderEvent,
    OrderEventAudience,
    PrivacyStatus,
    VendorOrder,
    VendorOrderVersion,
)
from .tz import business_tz

AMENDMENT_APPLIED = "orders.amendment_applied"
ITEM_UNAVAILABLE = "orders.item_unavailable"
PACKAGE_CANCELLED = "orders.package_cancelled"
REST_CANCELLED = "orders.rest_cancelled"
VENDOR_AMENDMENT_APPLIED = "orders.vendor_amendment_applied"
VENDOR_PACKAGE_CANCELLED = "orders.vendor_package_cancelled"
KINDS = (
    AMENDMENT_APPLIED,
    ITEM_UNAVAILABLE,
    PACKAGE_CANCELLED,
    REST_CANCELLED,
    VENDOR_AMENDMENT_APPLIED,
    VENDOR_PACKAGE_CANCELLED,
)

# Who cancelled a package, for the vendor's notice.
BY_SHOP = "shop"
BY_STAFF = "staff"
BY_CUSTOMER = "customer"

#: The fixed placeholders the wording version renders with (CMP.24): a changed text in any
#: language changes the version, an order's data never does.
_PLACEHOLDERS: dict[str, Any] = {
    "order_number": "[order]",
    "vendor_name": "[seller]",
    "version": "[n]",
    "reason": "[reason]",
    "changes": ["[change]"],
    "items": "[items]",
    "lines": [{"title": "[item]", "variant": "[variant]", "quantity": "[q]"}],
    "ship_to": ["[address]"],
    "refund": "[refund]",
    "total": "[total]",
    "hold_until": "[date and time]",
    "cancel_url": "[link]",
    "order_url": "[link]",
}

#: A variant flag that is false empties these placeholders (the branch without them).
_BLANKED_UNLESS: dict[str, tuple[str, ...]] = {
    "linked": ("cancel_url", "order_url"),
    "address_changed": ("ship_to",),
}


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


@functools.cache
def wording_version(template_key: str, language: str, variant: tuple[tuple[str, Any], ...]) -> str:
    """The CMP.24 version of one notice's wording in one language: the SHA-256 of the template
    rendered with fixed placeholders and the variant's flags (the cause, who acted, whether money
    moved...), so it covers exactly the branch that was sent."""
    values = {**_PLACEHOLDERS, **dict(variant)}
    for flag, keys in _BLANKED_UNLESS.items():
        if not values.get(flag, True):
            values.update(dict.fromkeys(keys, ""))
    rendered = core_services.render_email(template_key, language, values)
    digest = hashlib.sha256((rendered.subject + "\n" + rendered.text_body).encode()).hexdigest()
    return f"sha256:{digest}"


def _terms(language: str) -> dict[str, Any]:
    """The customer terms version in force now, in the customer's language (CMP.5 article "Order
    changes after payment and before shipping")."""
    found = cms_services.customer_terms_in_force(language)
    if found is None:
        return {}
    return {
        "version_id": found.version_id,
        "number": found.number,
        "language": found.language,
        "sha256": found.sha256,
    }


def _record(
    sub: VendorOrder,
    *,
    audience: str,
    recipient: str,
    template_key: str,
    language: str,
    context: Mapping[str, Any],
    variant: Mapping[str, Any],
    event_key: str,
    payload: Mapping[str, Any],
) -> OrderEvent:
    """Render the notice, keep its exact text as an `OrderEvent`, and queue the same email."""
    language = core_services.email_language(language)
    values = {**context, **variant}
    rendered = core_services.render_email(template_key, language, values)
    event = OrderEvent.objects.create(
        order=sub.order,
        vendor_order=sub,
        kind=template_key,
        audience=audience,
        language=language,
        subject=rendered.subject[:300],
        text=rendered.text_body,
        wording_version=wording_version(template_key, language, tuple(sorted(variant.items()))),
        terms=_terms(language) if audience == OrderEventAudience.CUSTOMER else {},
        payload=dict(payload),
    )
    if recipient:
        notifications.enqueue_email(
            recipient=recipient,
            template_key=template_key,
            language=language,
            idempotency_key=f"{event_key}:{audience}",
            context=values,
        )
    return event


def _active(order: Order) -> bool:
    return order.privacy_status == PrivacyStatus.ACTIVE


def _vendor_language(sub: VendorOrder) -> str:
    return str(sub.vendor.mail_language or settings.LANGUAGE_CODE)


def _vendor_name(sub: VendorOrder) -> str:
    return str(sub.vendor_snapshot.get("display_name") or sub.vendor.display_name)


def _when(value: dt.datetime | None, language: str) -> str:
    """A date and time in the business time zone (D-31), in the recipient's language."""
    if value is None:
        return ""
    with translation.override(language):
        return formats.date_format(timezone.localtime(value, business_tz()), "DATETIME_FORMAT")


def _absolute(path: str) -> str:
    return str(settings.SITE_BASE_URL).rstrip("/") + path


def _order_url(order: Order) -> str:
    """The customer's order page, where the actions are; a guest has none yet (its signed link
    comes with ORD.20 part 3b-2)."""
    if order.customer_id is None:
        return ""
    return _absolute(reverse("customer_order", args=[order.public_id]))


def change_text(
    change: am.AmendmentChange, before: am.VersionSnapshot | None, *, vendor: bool = False
) -> str:
    """One applied change in plain words, in the active language; never an internal note or an
    address. `vendor` words an address correction for the shop instead of the customer."""
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
        case am.ChangeAddress() if vendor:
            return gettext("Delivery address corrected at the customer's request")
        case am.ChangeAddress():
            return gettext("Delivery address corrected as you asked")
        case _:
            return gettext("Order changed")


def _changes(
    changes: Sequence[am.AmendmentChange],
    before: am.VersionSnapshot,
    language: str,
    *,
    vendor: bool = False,
) -> list[str]:
    with translation.override(language):
        return [change_text(c, before, vendor=vendor) for c in changes]


def _snapshot_lines(snapshot: am.VersionSnapshot) -> list[dict[str, Any]]:
    return [
        {"title": line.title, "variant": line.variant_label, "quantity": line.quantity}
        for line in snapshot.lines
    ]


def _ship_to(sub: VendorOrder, version: VendorOrderVersion) -> list[str]:
    address = versions.delivery_address(sub, version) or {}
    keys = ("name", "line1", "line2", "postcode", "postal_code", "city", "region", "country")
    return [str(address[k]).strip() for k in keys if str(address.get(k) or "").strip()]


# --- an applied amendment -----------------------------------------------------------------------


def amendment_applied(amendment: OrderAmendment, version: VendorOrderVersion) -> None:
    """Notices of an applied minor amendment: the customer's (A2 after an item removed as
    unavailable with something left, the cancel-the-rest offer and its deadline; otherwise the
    plain change notice) and the vendor's (with the invoice notice; the ORD.22 variant when
    platform staff made it)."""
    sub = VendorOrder.objects.select_related("order", "vendor").get(pk=amendment.vendor_order_id)
    order = sub.order
    if not _active(order):
        return
    changes = [am.change_from_json(c) for c in amendment.changes]
    before = am.VersionSnapshot.from_json(amendment.from_version.snapshot)
    after = am.VersionSnapshot.from_json(version.snapshot)
    refund = Decimal(str(amendment.preview.get("refund") or "0"))
    by_staff = amendment.initiator in am.STAFF_INITIATORS
    unavailable = [
        before.line(c.line_key)
        for c in changes
        if isinstance(c, am.RemoveLine | am.ChangeQuantity) and c.unavailable
    ]
    offered = sub.cancel_rest_offered_at is not None and bool(after.lines) and bool(unavailable)
    payload = {
        "amendment": str(amendment.public_id),
        "version": version.number,
        "refund": _money(refund),
        "refund_ref": amendment.refund_ref,
        "total": _money(version.gross_total),
    }
    base = {
        "order_number": order.number,
        "vendor_name": _vendor_name(sub),
        "version": version.number,
        "reason": amendment.reason,
        "total": _money(version.gross_total),
        "refund": _money(refund),
    }
    language = core_services.email_language(order.language)
    customer = {
        **base,
        "changes": _changes(changes, before, language),
        "order_url": _order_url(order),
    }
    if offered:
        customer.update(
            items=", ".join(line.title for line in unavailable if line is not None),
            hold_until=_when(sub.ship_hold_until, language),
            cancel_url=_order_url(order),
        )
        template = ITEM_UNAVAILABLE
        variant: dict[str, Any] = {"refunded": refund > 0}
    else:
        template = AMENDMENT_APPLIED
        variant = {"refunded": refund > 0, "by_staff": by_staff}
    variant["linked"] = bool(customer["order_url"])
    _record(
        sub,
        audience=OrderEventAudience.CUSTOMER,
        recipient=order.email,
        template_key=template,
        language=language,
        context=customer,
        variant=variant,
        event_key=f"amendment:{amendment.public_id}",
        payload=payload,
    )
    vendor_language = core_services.email_language(_vendor_language(sub))
    address_changed = any(isinstance(c, am.ChangeAddress) for c in changes)
    _record(
        sub,
        audience=OrderEventAudience.VENDOR,
        recipient=sub.vendor.contact_email,
        template_key=VENDOR_AMENDMENT_APPLIED,
        language=vendor_language,
        context={
            **base,
            "changes": _changes(changes, before, vendor_language, vendor=True),
            "lines": _snapshot_lines(after),
            "ship_to": _ship_to(sub, version) if address_changed else [],
        },
        variant={
            "address_changed": address_changed,
            "by_staff": by_staff,
            "refunded": refund > 0,
            "invoice_issued": sub.invoice_issued_at is not None,
        },
        event_key=f"amendment:{amendment.public_id}",
        payload=payload,
    )


# --- a package cancelled ------------------------------------------------------------------------


def package_cancelled(
    sub: VendorOrder,
    *,
    by: str,
    cause: str,
    reason: str,
    refunded: Decimal,
    refund_fee: bool,
    refund_ref: str,
    lines: Sequence[dict[str, Any]],
) -> None:
    """Notices of a package cancelled with a full refund: by the shop or platform staff (the
    customer's notice by cause: A1 for the shop's fault, the customer's request, a platform
    error), or by the customer after an item was unavailable (cancel the rest). The vendor is
    told in every case, with who cancelled, the cause, whether the refund fee is charged and the
    invoice notice; `lines` are what it must not ship."""
    sub = VendorOrder.objects.select_related("order", "vendor").get(pk=sub.pk)
    order = sub.order
    if not _active(order):
        return
    payload = {
        "by": by,
        "cause": cause,
        "refund": _money(refunded),
        "refund_ref": refund_ref,
        "refund_fee": refund_fee,
    }
    base = {
        "order_number": order.number,
        "vendor_name": _vendor_name(sub),
        "reason": reason,
        "refund": _money(refunded),
        "lines": list(lines),
    }
    event_key = f"vendor-order:{sub.public_id}:package-cancelled:{refund_ref or 'none'}"
    order_url = _order_url(order)
    variant: dict[str, Any] = {"linked": bool(order_url)}
    if by == BY_CUSTOMER:
        template = REST_CANCELLED
    else:
        template = PACKAGE_CANCELLED
        variant["cause"] = cause
    _record(
        sub,
        audience=OrderEventAudience.CUSTOMER,
        recipient=order.email,
        template_key=template,
        language=order.language,
        context={**base, "order_url": order_url},
        variant=variant,
        event_key=event_key,
        payload=payload,
    )
    _record(
        sub,
        audience=OrderEventAudience.VENDOR,
        recipient=sub.vendor.contact_email,
        template_key=VENDOR_PACKAGE_CANCELLED,
        language=_vendor_language(sub),
        context=base,
        variant={
            "by": by,
            "cause": cause,
            "refund_fee": refund_fee,
            "invoice_issued": sub.invoice_issued_at is not None,
        },
        event_key=event_key,
        payload=payload,
    )


def current_lines(sub: VendorOrder) -> list[dict[str, Any]]:
    """The current version's lines, for a cancellation notice (read before the package goes)."""
    return _snapshot_lines(am.VersionSnapshot.from_json(versions.current_version(sub).snapshot))


# --- reading them back --------------------------------------------------------------------------


def customer_notices(sub: VendorOrder) -> list[OrderEvent]:
    """The notices sent to the customer about this package, oldest first (the customer area)."""
    return list(
        OrderEvent.objects.filter(vendor_order=sub, audience=OrderEventAudience.CUSTOMER).order_by(
            "created_at", "id"
        )
    )
