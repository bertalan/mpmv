"""Creating and changing orders (spec 07 §7.2, ORD.2).

Everything here takes plain values, not live catalogue, tax or shipping rows: the caller (the
checkout, ORD.4) resolves prices, tax and the shipping choice, and these services store them as
snapshots. Mutations accept `dry_run=True` and return a ChangeSet, and write one AuditLog entry
per changed object (SEC.4). `tax` and `shipping` are never imported here.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.core.audit import VENDOR_FIELD, AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import audit_changes
from apps.vendors.models import Vendor

from . import engine
from .models import Order, OrderLine, OrderNumberSequence, VendorOrder

DRY_RUN_NUMBER = "(assigned when placed)"


@dataclass(frozen=True)
class LineInput:
    """One line as the checkout resolved it. `sku`, `title` and `variant_label` are display
    snapshots of the vendor's labels; `product` is only a convenience link."""

    product: Product | None
    sku: str
    title: str
    unit_price_net: Decimal
    quantity: int
    tax_rate: Decimal
    tax_reason: str
    variant_label: str = ""
    discount_net: Decimal = Decimal("0.00")
    legal_note_key: str = ""


@dataclass(frozen=True)
class VendorOrderInput:
    vendor: Vendor
    lines: Sequence[LineInput]
    shipping_net: Decimal = Decimal("0.00")
    shipping_tax: Decimal = Decimal("0.00")
    shipping_choice: dict[str, Any] = field(default_factory=dict)
    commission_snapshot: dict[str, Any] = field(default_factory=dict)
    tax_profile_snapshot: dict[str, Any] = field(default_factory=dict)


# --- numbering -------------------------------------------------------------------------------


def next_order_number(*, now: dt.datetime | None = None) -> str:
    """The next order number of the year, e.g. `2027-000123` (prefix: ORDER_NUMBER_PREFIX).

    Locks the year's counter row until the surrounding transaction ends, so concurrent orders
    queue up and a rolled-back order gives its number back: no duplicates, no gaps. Call it
    inside the transaction that saves the order.
    """
    year = timezone.localtime(now or timezone.now()).year
    with transaction.atomic():
        OrderNumberSequence.objects.bulk_create(
            [OrderNumberSequence(year=year)], ignore_conflicts=True
        )
        counter = OrderNumberSequence.objects.select_for_update().get(year=year)
        counter.last_value += 1
        counter.save(update_fields=["last_value"])
    prefix = str(getattr(settings, "ORDER_NUMBER_PREFIX", ""))
    return engine.format_order_number(year, counter.last_value, prefix=prefix)


# --- snapshots for the audit trail (no addresses or e-mail: personal data) ---------------------


def _order_snapshot(order: Order) -> dict[str, Any]:
    return {
        "number": order.number,
        "state": order.state,
        "customer": order.customer_id,
        "language": order.language,
        "billing_country": order.billing_country,
        "shipping_country": order.shipping_country,
        "customer_tax_status": order.customer_tax_status,
        "goods_net": order.goods_net,
        "shipping_net": order.shipping_net,
        "tax_total": order.tax_total,
        "grand_total": order.grand_total,
    }


def _vendor_order_snapshot(sub: VendorOrder) -> dict[str, Any]:
    return {
        VENDOR_FIELD: str(sub.vendor.public_id),
        "order": sub.order.number,
        "state": sub.state,
        "goods_net": sub.goods_net,
        "shipping_net": sub.shipping_net,
        "tax_total": sub.tax_total,
        "gross_total": sub.gross_total,
        "tracking_number": sub.tracking_number,
        "carrier": sub.carrier,
        "shipped_at": sub.shipped_at,
        "delivered_at": sub.delivered_at,
    }


def _line_snapshot(line: OrderLine) -> dict[str, Any]:
    return {
        VENDOR_FIELD: str(line.vendor_order.vendor.public_id),
        "sku": line.sku,
        "title": line.title,
        "unit_price_net": line.unit_price_net,
        "quantity": line.quantity,
        "net_total": line.net_total,
        "tax_rate": line.tax_rate,
        "tax_amount": line.tax_amount,
    }


# --- creating an order -----------------------------------------------------------------------


def _build_vendor_order(
    order: Order, item: VendorOrderInput
) -> tuple[VendorOrder, list[OrderLine], engine.Totals]:
    if not item.lines:
        raise ValueError("a vendor order needs at least one line")
    amounts = [
        engine.line_amounts(
            entry.unit_price_net, entry.quantity, entry.discount_net, entry.tax_rate
        )
        for entry in item.lines
    ]
    sub_totals = engine.totals(
        amounts, shipping_net=item.shipping_net, shipping_tax=item.shipping_tax
    )
    sub = VendorOrder(
        order=order,
        vendor=item.vendor,
        goods_net=sub_totals.goods_net,
        shipping_net=sub_totals.shipping_net,
        tax_total=sub_totals.tax_total,
        gross_total=sub_totals.grand_total,
        shipping_choice=item.shipping_choice,
        commission_snapshot=item.commission_snapshot,
        tax_profile_snapshot=item.tax_profile_snapshot,
    )
    lines = [
        OrderLine(
            vendor_order=sub,
            product=entry.product,
            sku=entry.sku,
            title=entry.title,
            variant_label=entry.variant_label,
            unit_price_net=entry.unit_price_net,
            quantity=entry.quantity,
            discount_net=entry.discount_net,
            net_total=a.net,
            tax_rate=entry.tax_rate,
            tax_reason=entry.tax_reason,
            tax_amount=a.tax,
            legal_note_key=entry.legal_note_key,
        )
        for entry, a in zip(item.lines, amounts, strict=True)
    ]
    return sub, lines, sub_totals


def create_order(
    *,
    email: str,
    language: str,
    billing_address: dict[str, Any],
    billing_country: str,
    shipping_address: dict[str, Any],
    shipping_country: str,
    vendor_orders: Sequence[VendorOrderInput],
    ctx: AuditContext,
    customer: User | None = None,
    customer_tax_status: str = "b2c",
    vat_validation: dict[str, Any] | None = None,
    state: str = "checkout",
    legacy_wp_order_id: int | None = None,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> tuple[Order | None, ChangeSet]:
    """Create an order with its vendor sub-orders and line snapshots, numbered and audited.

    The totals are computed here (rounded per line, spec 01), never taken from the caller.
    Dry run returns `(None, changeset)` and uses no number.
    """
    if not vendor_orders:
        raise ValueError("an order needs at least one vendor order")
    if len({item.vendor.pk for item in vendor_orders}) != len(vendor_orders):
        raise ValueError("an order has one sub-order per vendor: the same vendor appears twice")
    if state not in engine.ORDER_INITIAL_STATES:
        raise ValueError(f"{state!r} is not an initial state of an order")

    order = Order(
        number=DRY_RUN_NUMBER,
        customer=customer,
        email=email,
        language=language,
        billing_address=billing_address,
        billing_country=billing_country,
        shipping_address=shipping_address,
        shipping_country=shipping_country,
        customer_tax_status=customer_tax_status,
        vat_validation=vat_validation or {},
        state=state,
        legacy_wp_order_id=legacy_wp_order_id,
    )
    built = [_build_vendor_order(order, item) for item in vendor_orders]
    grand = engine.add_totals(totals for _, _, totals in built)
    order.goods_net, order.shipping_net = grand.goods_net, grand.shipping_net
    order.tax_total, order.grand_total = grand.tax_total, grand.grand_total

    def changes() -> ChangeSet:
        changeset = ChangeSet()
        changeset.create("orders.order", str(order.public_id), _order_snapshot(order))
        for sub, lines, _ in built:
            changeset.create("orders.vendororder", str(sub.public_id), _vendor_order_snapshot(sub))
            for line in lines:
                changeset.create("orders.orderline", str(line.public_id), _line_snapshot(line))
        return changeset

    if dry_run:
        return None, changes()
    with transaction.atomic():
        order.number = next_order_number(now=now)
        order.transfer_group = order.number
        order.save()
        for sub, lines, _ in built:
            sub.order = order
            sub.save()
            for line in lines:
                line.vendor_order = sub
                line.save()
        changeset = changes()
        audit_changes(changeset, ctx=ctx, action="orders.create")
    return order, changeset


# --- transitions -----------------------------------------------------------------------------


class _DryRunRollback(Exception):
    pass


def transition_order(
    order: Order, target: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Move an order along the state machine; raises `IllegalTransition` otherwise."""
    changeset = ChangeSet()
    try:
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            before = _order_snapshot(locked)
            engine.check_order_transition(locked.state, target)
            locked.state = target
            locked.save(update_fields=["state", "updated_at"])
            changeset.update("orders.order", str(locked.public_id), before, _order_snapshot(locked))
            if dry_run:
                raise _DryRunRollback
            audit_changes(changeset, ctx=ctx, action="orders.transition")
    except _DryRunRollback:
        pass
    order.refresh_from_db()
    return changeset


def transition_vendor_order(
    sub: VendorOrder,
    target: str,
    *,
    ctx: AuditContext,
    tracking_number: str = "",
    carrier: str = "",
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> ChangeSet:
    """Move a vendor sub-order along its state machine, stamp shipping/delivery, and roll the
    order's fulfilment state up (spec 07: the order state is derived from its sub-orders)."""
    changeset = ChangeSet()
    stamp = now or timezone.now()
    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=sub.order_id)
            locked = (
                VendorOrder.objects.select_for_update()
                .select_related("vendor", "order")
                .get(pk=sub.pk)
            )
            before = _vendor_order_snapshot(locked)
            engine.check_vendor_order_transition(locked.state, target)
            if target == "shipped":
                locked.tracking_number = tracking_number or locked.tracking_number
                if not locked.tracking_number:
                    raise ValueError("a vendor order needs a tracking number to be shipped")
                locked.carrier = carrier or locked.carrier
                locked.shipped_at = stamp
            if target == "delivered":
                locked.delivered_at = stamp
            locked.state = target
            locked.save()
            changeset.update(
                "orders.vendororder", str(locked.public_id), before, _vendor_order_snapshot(locked)
            )
            states = list(order.vendor_orders.values_list("state", flat=True))
            derived = engine.derive_order_state(order.state, states)
            if derived != order.state:
                order_before = _order_snapshot(order)
                engine.check_order_transition(order.state, derived)
                order.state = derived
                order.save(update_fields=["state", "updated_at"])
                changeset.update(
                    "orders.order", str(order.public_id), order_before, _order_snapshot(order)
                )
            if dry_run:
                raise _DryRunRollback
            audit_changes(changeset, ctx=ctx, action="orders.transition")
    except _DryRunRollback:
        pass
    sub.refresh_from_db()
    return changeset
