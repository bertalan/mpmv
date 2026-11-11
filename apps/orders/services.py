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
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.core.audit import VENDOR_FIELD, AuditContext
from apps.core.changeset import ChangeSet
from apps.core.money import CENT
from apps.core.services import audit_changes
from apps.vendors.models import Vendor

from . import engine, notifications
from .models import Order, OrderLine, OrderNumberSequence, VendorOrder
from .tz import business_tz

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
    vat_nature_code: str = ""
    discount_net: Decimal = Decimal("0.00")
    legal_note_key: str = ""


@dataclass(frozen=True)
class VendorOrderInput:
    vendor: Vendor
    lines: Sequence[LineInput]
    shipping_net: Decimal = Decimal("0.00")
    shipping_tax_rate: Decimal = Decimal("0")
    shipping_tax_reason: str = ""
    shipping_vat_nature_code: str = ""
    state: str = "pending"  # anything else only for a legacy order (MIG.4)
    shipping_choice: dict[str, Any] = field(default_factory=dict)
    commission_snapshot: dict[str, Any] = field(default_factory=dict)
    tax_profile_snapshot: dict[str, Any] = field(default_factory=dict)


PAID_OR_LATER = frozenset(engine.ORDER_STATES) - engine.ORDER_INITIAL_STATES - {"cancelled"}
# The shape of `CommissionRule.snapshot()` (VEN.2). Required keys; `promo_label` and `valid_from`
# are allowed too. A constant copy: orders does not import vendors for this.
# The VIES log kept in the snapshot (D-30): time, consultation number and the name VIES returned.
VIES_KEYS = ("at", "consultation", "name")
COMMISSION_KEYS = ("rule_id", "version", "percent", "base", "fee_percent", "fee_fixed", "fee_base")
COMMISSION_OPTIONAL_KEYS = ("promo_label", "valid_from")
MAX_PREFIX_LENGTH = 14  # number = prefix + "YYYY-" + 6+ digits must fit in 32 characters

# --- numbering -------------------------------------------------------------------------------


def _year_and_prefix(now: dt.datetime | None) -> tuple[int, str]:
    """The numbering year is the business year (settings.BUSINESS_TIME_ZONE, Europe/Rome), so an
    order placed at 00:30 on 1 January in Rome starts the new year even when UTC says 31 Dec."""
    prefix = str(getattr(settings, "ORDER_NUMBER_PREFIX", ""))
    if len(prefix) > MAX_PREFIX_LENGTH:
        raise ImproperlyConfigured(
            f"ORDER_NUMBER_PREFIX is longer than {MAX_PREFIX_LENGTH} characters"
        )
    return (now or timezone.now()).astimezone(business_tz()).year, prefix


def peek_order_number(*, now: dt.datetime | None = None) -> str:
    """The number the next order would get, e.g. `2027-000123`. A preview: it takes no lock and
    changes nothing, and the real number may differ if another order is placed first."""
    year, prefix = _year_and_prefix(now)
    last = OrderNumberSequence.objects.filter(year=year).values_list("last_value", flat=True)
    return engine.format_order_number(year, (last.first() or 0) + 1, prefix=prefix)


def _take_order_number(*, now: dt.datetime | None = None) -> str:
    """Issue the next order number of the year (prefix: ORDER_NUMBER_PREFIX).

    Locks the year's counter row until the surrounding transaction ends, so concurrent orders
    queue up and a rolled-back order gives its number back: no duplicates, no gaps. Only
    `create_order` calls it, inside the transaction that saves the order.
    """
    year, prefix = _year_and_prefix(now)
    with transaction.atomic():
        OrderNumberSequence.objects.bulk_create(
            [OrderNumberSequence(year=year)], ignore_conflicts=True
        )
        counter = OrderNumberSequence.objects.select_for_update().get(year=year)
        counter.last_value += 1
        counter.save(update_fields=["last_value"])
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
        "shipping_tax_rate": sub.shipping_tax_rate,
        "shipping_tax_amount": sub.shipping_tax_amount,
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


def _vendor_identity(vendor: Vendor) -> dict[str, Any]:
    """Who the vendor was when the order was placed (for the confirmation, ORD.9)."""
    return {
        "display_name": vendor.display_name,
        "legal_name": vendor.legal_name,
        "vat_number": vendor.vat_number,
        "country": vendor.country,
    }


def _build_vendor_order(
    order: Order, item: VendorOrderInput
) -> tuple[VendorOrder, list[OrderLine], engine.Totals]:
    if not item.lines:
        raise ValueError("a vendor order needs at least one line")
    missing = [key for key in COMMISSION_KEYS if key not in item.commission_snapshot]
    if missing:
        raise ValueError(f"commission_snapshot needs {', '.join(missing)}")
    unknown = set(item.commission_snapshot) - set(COMMISSION_KEYS) - set(COMMISSION_OPTIONAL_KEYS)
    if unknown:
        raise ValueError(f"commission_snapshot has unknown keys: {', '.join(sorted(unknown))}")
    if item.shipping_net < 0 or item.shipping_net != item.shipping_net.quantize(CENT):
        raise ValueError("shipping must be a non-negative amount in cents")
    for entry in item.lines:
        if entry.unit_price_net <= 0:
            raise ValueError("the unit price must be above zero")
        if entry.unit_price_net != entry.unit_price_net.quantize(CENT):
            raise ValueError("the unit price must be in cents")
    shipping_tax = engine.shipping_tax(item.shipping_net, item.shipping_tax_rate)
    amounts = [
        engine.line_amounts(
            entry.unit_price_net, entry.quantity, entry.discount_net, entry.tax_rate
        )
        for entry in item.lines
    ]
    sub_totals = engine.totals(amounts, shipping_net=item.shipping_net, shipping_tax=shipping_tax)
    sub = VendorOrder(
        order=order,
        vendor=item.vendor,
        goods_net=sub_totals.goods_net,
        shipping_net=sub_totals.shipping_net,
        tax_total=sub_totals.tax_total,
        gross_total=sub_totals.grand_total,
        shipping_tax_rate=item.shipping_tax_rate,
        shipping_tax_amount=shipping_tax,
        shipping_tax_reason=item.shipping_tax_reason,
        shipping_vat_nature_code=item.shipping_vat_nature_code,
        state=item.state,
        vendor_snapshot=_vendor_identity(item.vendor),
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
            vat_nature_code=entry.vat_nature_code,
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
    customer_name: str = "",
    customer_vat_number: str = "",
    customer_tax_id: str = "",
    customer_tax_status: str = "b2c",
    vat_validation: dict[str, Any] | None = None,
    state: str = "checkout",
    legacy_wp_order_id: int | None = None,
    placed_at: dt.datetime | None = None,
    paid_at: dt.datetime | None = None,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> tuple[Order | None, ChangeSet]:
    """Create an order with its vendor sub-orders and line snapshots, numbered and audited.

    The totals are computed here (rounded per line, spec 01), never taken from the caller.
    Dry run returns `(None, changeset)` and uses no number.

    The number is taken inside the transaction and returned to the pool if it rolls back, so a
    rolled-back order's number may go to another order. Therefore `number` (and `transfer_group`,
    which equals it) must be sent to the payment provider only AFTER the transaction commits
    (`transaction.on_commit`), never from inside it (ORD.4).
    """
    if vat_validation and not set(VIES_KEYS) <= set(vat_validation):
        raise ValueError(f"vat_validation needs {', '.join(VIES_KEYS)}")
    if not vendor_orders:
        raise ValueError("an order needs at least one vendor order")
    if len({item.vendor.pk for item in vendor_orders}) != len(vendor_orders):
        raise ValueError("an order has one sub-order per vendor: the same vendor appears twice")
    if state not in engine.ORDER_STATES:
        raise ValueError(f"{state!r} is not an order state")
    legacy = legacy_wp_order_id is not None
    if (placed_at is not None or paid_at is not None) and not legacy:
        raise ValueError("only a legacy order may choose its dates (placed_at, paid_at)")
    if state not in engine.ORDER_INITIAL_STATES and not legacy:
        raise ValueError(f"{state!r} is not an initial state of an order")
    for item in vendor_orders:
        if item.state != "pending" and not legacy:
            raise ValueError("a vendor order starts pending unless the order is a legacy one")
        if item.state not in engine.VENDOR_ORDER_STATES:
            raise ValueError(f"{item.state!r} is not a vendor order state")

    order = Order(
        number=DRY_RUN_NUMBER,
        customer=customer,
        customer_name=customer_name,
        customer_vat_number=customer_vat_number,
        customer_tax_id=customer_tax_id,
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
    order.paid_at = paid_at if legacy else None
    if placed_at is not None:
        order.created_at = placed_at  # MIG.4: the original date starts the retention clock
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
        order.number = _take_order_number(now=now)
        order.transfer_group = order.number
        order.save()
        order.refresh_from_db(fields=["paid_at"])  # the trigger may have set it (legacy states)
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
            if target == "paid":  # ORD.9: in this transaction, so a rollback sends nothing
                notifications.notify_order_paid(locked)
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
            if order.state not in PAID_OR_LATER:
                raise ValueError(f"the order must be paid first (it is {order.state})")
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
            order_before = _order_snapshot(order)
            while (derived := engine.derive_order_state(order.state, states)) != order.state:
                engine.check_order_transition(order.state, derived)
                order.state = derived
                order.save(update_fields=["state", "updated_at"])
            changeset.update(
                "orders.order", str(order.public_id), order_before, _order_snapshot(order)
            )
            if dry_run:
                raise _DryRunRollback
            audit_changes(changeset, ctx=ctx, action="orders.transition")
            if target == "shipped":  # ORD.9
                notifications.notify_vendor_order_shipped(locked)
    except _DryRunRollback:
        pass
    sub.refresh_from_db()
    return changeset


# --- retention: the scrub services (owner decision D-29) ---------------------------------------
#
# The fiscal snapshot of an order is kept, unchanged, for 10 years from the end of its calendar
# year (GDPR art. 6(1)(c) and 17(3)(b); VAT Directive art. 242a). The database refuses any
# rewrite of it. These two services are the only path, and each runs only after its date:
# `restrict_order` (D-30: active -> restricted_tax_only) drops the non-fiscal fields;
# `anonymise_order` replaces the personal fields from the day after `tax_retention_until`, or at
# once for an order never paid. The retention jobs that call them, the masking, the accounting
# role and the erasure flow are separate tasks.


class ScrubNotDue(ValueError):
    """The retention period of this scrub has not ended."""


def _set_scrub_mode(mode: str) -> None:
    """Transaction-local: the trigger lets the matching scrub write the protected columns."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('mpmv.order_scrub', %s, true)", [mode])


def _scrub_snapshot(order: Order) -> dict[str, Any]:
    return {
        "number": order.number,
        "privacy_status": order.privacy_status,
        "tax_retention_until": order.tax_retention_until,
    }


def _clear_non_fiscal(order: Order, changeset: ChangeSet) -> None:
    for sub in order.vendor_orders.select_for_update().select_related("vendor"):
        if not (sub.carrier_contact_phone or sub.support_notes):
            continue
        before = {
            VENDOR_FIELD: str(sub.vendor.public_id),
            "has_contact_phone": bool(sub.carrier_contact_phone),
            "has_support_notes": bool(sub.support_notes),
        }
        VendorOrder.objects.filter(pk=sub.pk).update(carrier_contact_phone="", support_notes="")
        after = {**before, "has_contact_phone": False, "has_support_notes": False}
        changeset.update("orders.vendororder", str(sub.public_id), before, after)


def _scrub(
    order: Order,
    target: str,
    *,
    reason: str,
    ctx: AuditContext,
    dry_run: bool,
    now: dt.datetime | None,
) -> ChangeSet:
    """Move an order forward in its privacy lifecycle (D-30), after its date. The database
    trigger checks the same gates against its own clock."""
    stamp = now or timezone.now()
    changeset = ChangeSet()
    try:
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            if locked.privacy_status == target or (
                target == "restricted_tax_only" and locked.privacy_status == "anonymized"
            ):
                return changeset  # idempotent: already there (or further)
            today = stamp.astimezone(business_tz()).date()
            if target == "restricted_tax_only":
                mode = "restrict_erasure" if reason == "account_erasure" else "restrict"
                # The database trigger enforces 24 months as a floor; the setting only lengthens.
                months = max(
                    int(getattr(settings, "ORDER_ACTIVE_PHASE_MONTHS", 24)),
                    engine.MIN_ACTIVE_PHASE_MONTHS,
                )
                if mode == "restrict" and not engine.restriction_due(
                    locked.created_at, stamp, months, business_tz()
                ):
                    raise ScrubNotDue(f"the active phase of {months} months has not ended")
            else:
                mode = "anonymise"
                at_once = engine.anonymisable_at_once(
                    locked.state, locked.paid_at, locked.created_at, stamp
                )
                if not at_once and not engine.retention_over(locked.tax_retention_until, today):
                    raise ScrubNotDue(
                        f"anonymisation is due after {locked.tax_retention_until} "
                        "(or at once for a cancelled or abandoned order never paid)"
                    )
            engine.check_privacy_transition(locked.privacy_status, target)
            before = _scrub_snapshot(locked)
            _set_scrub_mode(mode)
            _clear_non_fiscal(locked, changeset)
            values: dict[str, Any] = {"updated_at": stamp, "privacy_status": target}
            if target == "anonymized":
                values.update(
                    engine.ANONYMISED_ORDER_FIELDS,
                    email=engine.ANONYMISED_EMAIL,
                    customer=None,
                    anonymised_at=stamp,
                )
            else:
                values["restricted_at"] = stamp
            Order.objects.filter(pk=locked.pk).update(**values)
            _set_scrub_mode("")
            locked.refresh_from_db()
            changeset.update("orders.order", str(locked.public_id), before, _scrub_snapshot(locked))
            if dry_run:
                raise _DryRunRollback
            audit_changes(
                changeset,
                ctx=ctx,
                action="orders.restrict" if target == "restricted_tax_only" else "orders.anonymise",
            )
    except _DryRunRollback:
        pass
    order.refresh_from_db()
    return changeset


def restrict_order(
    order: Order,
    *,
    ctx: AuditContext,
    reason: str = "active_phase_over",
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> ChangeSet:
    """active -> restricted_tax_only: drop the non-fiscal fields (carrier contact phone, support
    notes) on entry; the fiscal snapshot stays, masked by the UI later (D-30).

    Allowed once `ORDER_ACTIVE_PHASE_MONTHS` (default 24, never shorter: the database enforces
    24) have passed, or at once with `reason="account_erasure"`. Raises `ScrubNotDue` otherwise.
    The database checks its own clock; `now` serves dry runs. Idempotent."""
    return _scrub(order, "restricted_tax_only", reason=reason, ctx=ctx, dry_run=dry_run, now=now)


def anonymise_order(
    order: Order, *, ctx: AuditContext, dry_run: bool = False, now: dt.datetime | None = None
) -> ChangeSet:
    """-> anonymized: replace the personal fields (name, VAT and tax ids, e-mail, addresses, VAT
    validation log, buyer link) with fixed placeholders, keeping amounts, countries, dates and
    tax rates. Allowed from the day after `tax_retention_until` (31 December of year 10, business
    time zone), or at once for a cancelled or abandoned order that never reached `paid`
    (`paid_at` is empty). Raises `ScrubNotDue` before
    that. Idempotent."""
    return _scrub(order, "anonymized", reason="", ctx=ctx, dry_run=dry_run, now=now)
