"""Creating and changing orders (spec 07 §7.2, ORD.2).

Everything here takes plain values, not live catalogue, tax or shipping rows: the caller (the
checkout, ORD.4) resolves prices, tax and the shipping choice, and these services store them as
snapshots. Mutations accept `dry_run=True` and return a ChangeSet, and write one AuditLog entry
per changed object (SEC.4). `tax` is never imported here; `shipping` only for the carrier's
tracking link (ORD.7).
"""

import datetime as dt
import logging
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db import connection, transaction
from django.db.models import Prefetch, QuerySet, Sum
from django.utils import timezone
from django.utils.functional import Promise
from django.utils.module_loading import import_string
from django.utils.text import slugify

from apps.accounts.models import User
from apps.cart import engine as cart_engine
from apps.cart.models import Cart
from apps.catalog.models import Product, Variant
from apps.core import flags
from apps.core.audit import VENDOR_FIELD, AuditContext
from apps.core.changeset import ChangeSet
from apps.core.money import CENT, CURRENCY
from apps.core.services import audit_changes, flag_enabled
from apps.shipping.models import Carrier
from apps.vendors import services as vendors_services
from apps.vendors.models import Vendor
from apps.vendors.services import has_vendor_permission

from . import engine, notifications
from .messages import CLOSED_SITE, CLOSED_VENDOR
from .models import (
    Order,
    OrderLine,
    OrderNumberSequence,
    OrderState,
    PrivacyStatus,
    StockReservation,
    VendorOrder,
)
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


logger = logging.getLogger(__name__)

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
        "has_vehicle": bool(order.vehicle_snapshot),
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
    vehicle: dict[str, Any] | None = None,
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
        vehicle_snapshot=engine.clean_vehicle_snapshot(vehicle),
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
            elif target == "cancelled":
                notifications.notify_order_cancelled(locked)
    except _DryRunRollback:
        pass
    order.refresh_from_db()
    return changeset


class OrdersFrozen(ValueError):
    """The vendor's open orders are frozen (a termination awaiting staff review): views answer
    with a 4xx."""


def _orders_frozen(vendor: Vendor) -> bool:
    """Whether a hook in `settings.ORDER_FREEZE_HOOKS` freezes the vendor's open orders. Hooks
    are dotted paths (orders never imports the apps that freeze); a voluntary closure freezes
    nothing."""
    return any(import_string(path)(vendor) for path in settings.ORDER_FREEZE_HOOKS)


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
            if _orders_frozen(locked.vendor):  # CMP.5 part 4: frozen pending staff review
                raise OrdersFrozen(
                    "the vendor's open orders are frozen pending staff review (termination)"
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
            elif target == "delivered":
                notifications.notify_vendor_order_delivered(locked)
            elif target == "cancelled":
                notifications.notify_vendor_order_cancelled(locked)
    except _DryRunRollback:
        pass
    sub.refresh_from_db()
    return changeset


def fulfil_vendor_order(
    sub: VendorOrder,
    action: str,
    *,
    actor: User,
    ctx: AuditContext,
    tracking_number: str = "",
    carrier: str = "",
    dry_run: bool = False,
) -> ChangeSet:
    """A vendor member accepts, ships (with tracking) or delivers its own sub-order (ORD.6).

    The actor must be an active member of the sub-order's vendor whose role holds the `orders`
    action (CORE.2); anyone else gets `PermissionDenied`, checked before anything is read from
    the arguments. Raises `engine.UnknownFulfilmentAction`, `engine.InvalidTracking` and
    `engine.IllegalTransition` before any write; the move itself is `transition_vendor_order`.
    """
    if not has_vendor_permission(actor, sub.vendor, "orders"):
        raise PermissionDenied("Only a member of the vendor can fulfil its orders.")
    target = engine.fulfilment_target(action)
    if target == "shipped":
        tracking_number = engine.normalise_tracking(tracking_number)
        carrier = engine.normalise_carrier(carrier)
    else:
        tracking_number = carrier = ""
    return transition_vendor_order(
        sub, target, ctx=ctx, tracking_number=tracking_number, carrier=carrier, dry_run=dry_run
    )


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
            # FIT.17: the vehicle is not a fiscal fact: it goes with the first scrub step.
            values: dict[str, Any] = {
                "updated_at": stamp,
                "privacy_status": target,
                "vehicle_snapshot": {},
            }
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


# --- placing an order (ORD.4) ----------------------------------------------------------------


class TaxOutcome(Protocol):
    """What the order reads from a tax decision (`apps.tax.engine.TaxDecision`, TAX.3). The
    order takes the VAT nature code from here and nowhere else: a caller cannot type one."""

    @property
    def rate(self) -> Decimal: ...
    @property
    def reason(self) -> Any: ...
    @property
    def legal_note_key(self) -> str | None: ...
    @property
    def nature_code(self) -> str | None: ...


@dataclass(frozen=True)
class PlaceLine:
    """A cart line as the checkout previewed it. `expected_unit_price_net` is the price the
    customer saw: it is only compared with the live price, never stored."""

    product: Product
    variant: Variant | None
    quantity: int
    title: str
    expected_unit_price_net: Decimal
    tax: TaxOutcome
    variant_label: str = ""
    discount_net: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class PlaceVendor:
    vendor: Vendor
    lines: Sequence[PlaceLine]
    shipping_net: Decimal = Decimal("0.00")
    shipping_tax: TaxOutcome | None = None  # from resolve_shipping_tax; required with shipping
    shipping_choice: dict[str, Any] = field(default_factory=dict)
    commission_snapshot: dict[str, Any] = field(default_factory=dict)
    tax_profile_snapshot: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.shipping_net > 0 and self.shipping_tax is None:
            raise ValueError("shipping needs a tax decision (shipping_tax)")


@dataclass(frozen=True)
class CheckoutPreview:
    """The checkout's result (ORD.3 builds it): everything `place_order` needs."""

    email: str
    language: str
    billing_address: dict[str, Any]
    billing_country: str
    shipping_address: dict[str, Any]
    shipping_country: str
    vendors: Sequence[PlaceVendor]
    cart: Cart | None = None
    customer: User | None = None
    customer_name: str = ""
    customer_vat_number: str = ""
    customer_tax_id: str = ""
    customer_tax_status: str = "b2c"
    vat_validation: dict[str, Any] | None = None
    #: FIT.17: the garage vehicle active at checkout as opaque JSON (make, model, year labels);
    #: `orders` never imports `fitment`, the checkout adapter passes what the garage shows.
    vehicle: dict[str, Any] | None = None
    # ORD.3: every vendor package as shown (also the ones that cannot be placed), the blocking
    # messages and the totals of the ready packages. `vendors` holds the ready packages only.
    packages: Sequence[engine.PackageSummary] = ()
    blocks: Sequence[engine.Block] = ()
    totals: engine.Totals | None = None

    @property
    def placeable(self) -> bool:
        """True when nothing blocks the checkout and at least one package is ready."""
        return not self.blocks and bool(self.vendors)

    @property
    def needs_quote(self) -> bool:
        return any(b.code == engine.NEEDS_QUOTE for b in self.blocks)


@dataclass(frozen=True)
class Refusal:
    code: str  # price_changed | unavailable | out_of_stock
    product_id: int
    detail: str


class OrderRefused(Exception):
    """The order was not placed: nothing was saved. `reasons` says why, for every line."""

    def __init__(self, reasons: Sequence[Refusal]) -> None:
        self.reasons = list(reasons)
        super().__init__("; ".join(f"{r.code}: {r.detail}" for r in self.reasons))


CLOSING_SWITCHES = (flags.CHECKOUT, flags.PAYMENTS)


class CheckoutClosed(Exception):
    """A kill switch (ARC.8) is off: no new order is placed and no payment starts (ORD.10).

    `scope` is "site" when the platform value is off, "vendor" when only the listed vendors'
    overrides are. `customer_message` is safe to show: it names no vendor. Orders already placed
    or paid are never touched by the switch."""

    def __init__(self, flag: str, scope: str, vendors: Sequence[int] = ()) -> None:
        self.flag = flag
        self.scope = scope
        self.vendors = list(vendors)
        super().__init__(f"{flag} switched off ({scope})")

    @property
    def customer_message(self) -> Promise:
        return CLOSED_SITE if self.scope == "site" else CLOSED_VENDOR


def ensure_checkout_open(vendors: Sequence[Vendor]) -> None:
    """Raise `CheckoutClosed` unless both the checkout and the payments switches are on for the
    platform and for every vendor of the order. Call it before an order or a payment intent is
    created (PAY.1 calls it before it starts a payment)."""
    for name in CLOSING_SWITCHES:
        if not flag_enabled(name):
            raise CheckoutClosed(name, "site")
        closed = [v.pk for v in vendors if not flag_enabled(name, vendor=v)]
        if closed:
            raise CheckoutClosed(name, "vendor", closed)


@dataclass(frozen=True)
class _NoTax:
    rate: Decimal = Decimal("0")
    reason: str = ""
    legal_note_key: str | None = None
    nature_code: str | None = None


_NO_TAX = _NoTax()


def _stock_key(product: Product, variant: Variant | None) -> tuple[int, int | None]:
    return (product.pk, variant.pk if variant is not None else None)


def _lock_live_rows(
    lines: Sequence[PlaceLine],
) -> tuple[dict[int, Product], dict[int, Variant]]:
    """Lock the products and variants of the order, always in primary-key order (products
    first), so concurrent placements queue instead of deadlocking."""
    product_ids = sorted({entry.product.pk for entry in lines})
    variant_ids = sorted({entry.variant.pk for entry in lines if entry.variant is not None})
    products = {
        p.pk: p
        for p in Product.objects.select_for_update(of=("self",))
        .select_related("vendor__connected_account")
        .filter(pk__in=product_ids)
        .order_by("pk")
    }
    variants = {
        v.pk: v
        for v in Variant.objects.select_for_update().filter(pk__in=variant_ids).order_by("pk")
    }
    return products, variants


def _held(product_id: int, variant_id: int | None, *, now: dt.datetime, cart: Cart | None) -> int:
    holds = StockReservation.objects.filter(product_id=product_id, expires_at__gt=now)
    holds = (
        holds.filter(variant__isnull=True)
        if variant_id is None
        else holds.filter(variant_id=variant_id)
    )
    if cart is not None:
        holds = holds.exclude(cart=cart)  # the cart's own hold is the one being confirmed
    return int(holds.aggregate(total=Sum("quantity"))["total"] or 0)


def _verify(
    preview: CheckoutPreview, *, now: dt.datetime
) -> tuple[dict[int, Product], dict[int, Variant], dict[tuple[int, int | None], int]]:
    """Check the live price and stock of every line (rows locked). Returns the live rows and the
    units to hold per stock key (None-stock products need no hold). Raises `OrderRefused` with
    every reason found; never reprices."""
    lines = [entry for item in preview.vendors for entry in item.lines]
    products, variants = _lock_live_rows(lines)
    reasons: list[Refusal] = []
    wanted: dict[tuple[int, int | None], int] = {}
    limits: dict[tuple[int, int | None], int | None] = {}
    for entry in lines:
        product = products.get(entry.product.pk)
        variant = variants.get(entry.variant.pk) if entry.variant is not None else None
        if product is None or (entry.variant is not None and variant is None):
            reasons.append(Refusal("unavailable", entry.product.pk, "This item no longer exists."))
            continue
        error = cart_engine.purchase_error(
            product_status=product.status,
            sales_mode=vendors_services.effective_sales_mode(product),
            vendor_status=product.vendor.status,
            is_variable=product.kind == Product.Kind.VARIABLE,
            has_variant=variant is not None,
        )
        if error is not None:
            reasons.append(Refusal("unavailable", product.pk, error))
            continue
        live = cart_engine.display_unit_price(
            product.price_net,
            vendors_services.effective_sales_mode(product),
            variant.price_net_override if variant else None,
        )
        if live != entry.expected_unit_price_net:
            reasons.append(
                Refusal(
                    "price_changed",
                    product.pk,
                    f"The price changed from {entry.expected_unit_price_net} to {live} "
                    "(net, per unit).",
                )
            )
            continue
        key = _stock_key(product, variant)
        stock = variant if variant is not None else product
        limits[key] = cart_engine.available_quantity(
            product.stock_policy, stock.stock_qty, stock.stock_status
        )
        wanted[key] = wanted.get(key, 0) + entry.quantity
    holds: dict[tuple[int, int | None], int] = {}
    for key, quantity in wanted.items():
        limit = limits[key]
        if limit is None:
            continue
        free = max(0, limit - _held(key[0], key[1], now=now, cart=preview.cart))
        if quantity > free:
            reasons.append(Refusal("out_of_stock", key[0], f"Only {free} available."))
        else:
            holds[key] = quantity
    if reasons:
        raise OrderRefused(reasons)
    return products, variants, holds


def place_order(
    preview: CheckoutPreview,
    *,
    ctx: AuditContext,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> tuple[Order | None, ChangeSet]:
    """Place the order in one transaction: verify the live prices and stock (rows locked), create
    the order awaiting payment with its snapshots, and confirm the stock holds.

    A price that differs from the one the customer saw refuses the whole order
    (`OrderRefused`, reason `price_changed`): the order is never silently repriced. Stock is held
    until `ORDER_HOLD_MINUTES` pass or the payment succeeds, when it is decremented. The tax
    rate, reason and VAT nature code come from each line's tax decision. Dry run verifies and
    returns the change set without saving or holding anything.

    The payment (PAY.1) starts after this transaction commits: `order.payment_client_secret`
    holds what the browser needs to confirm it (empty when no provider is configured or the
    provider failed; `start_payment` can be called again, and never creates a second intent).
    Raises `CheckoutClosed` before anything is read or saved when a kill switch is off (ORD.10).
    """
    ensure_checkout_open([item.vendor for item in preview.vendors])
    now = now or timezone.now()
    with transaction.atomic():
        products, variants, holds = _verify(preview, now=now)
        vendor_inputs = [
            VendorOrderInput(
                vendor=item.vendor,
                lines=[
                    _line_input(entry, products[entry.product.pk], variants) for entry in item.lines
                ],
                shipping_net=item.shipping_net,
                shipping_tax_rate=item.shipping_tax.rate if item.shipping_tax else Decimal("0"),
                shipping_tax_reason=_reason(item.shipping_tax),
                shipping_vat_nature_code=(item.shipping_tax.nature_code or "")
                if item.shipping_tax
                else "",
                shipping_choice=item.shipping_choice,
                commission_snapshot=item.commission_snapshot,
                tax_profile_snapshot=item.tax_profile_snapshot,
            )
            for item in preview.vendors
        ]
        order, changeset = create_order(
            email=preview.email,
            language=preview.language,
            billing_address=preview.billing_address,
            billing_country=preview.billing_country,
            shipping_address=preview.shipping_address,
            shipping_country=preview.shipping_country,
            vendor_orders=vendor_inputs,
            ctx=ctx,
            customer=preview.customer,
            customer_name=preview.customer_name,
            customer_vat_number=preview.customer_vat_number,
            customer_tax_id=preview.customer_tax_id,
            customer_tax_status=preview.customer_tax_status,
            vat_validation=preview.vat_validation,
            vehicle=preview.vehicle,
            state="awaiting_payment",
            dry_run=dry_run,
            now=now,
        )
        if dry_run or order is None:
            return None, changeset
        if preview.cart is not None:
            StockReservation.objects.filter(cart=preview.cart).delete()
        expires = now + dt.timedelta(minutes=int(getattr(settings, "ORDER_HOLD_MINUTES", 60)))
        StockReservation.objects.bulk_create(
            [
                StockReservation(
                    product_id=product_id,
                    variant_id=variant_id,
                    order=order,
                    quantity=quantity,
                    expires_at=expires,
                )
                for (product_id, variant_id), quantity in holds.items()
            ]
        )
        notifications.notify_order_placed(order)  # ORD.9: in this transaction, not on dry run
    # The order number is final only now (it may roll back with the transaction), so the provider
    # hears about it after the commit (ORD.4).
    # The order is committed: nothing below may raise into the caller. A closed switch or any
    # failure leaves the order awaiting payment (the payment page asks again) and is audited.
    try:
        order.payment_client_secret = start_payment(order, ctx=ctx)
    except Exception as error:
        order.payment_client_secret = ""
        _audit_payment_problem(
            order.number, "orders.payment_start_failed", {"reason": type(error).__name__}, ctx
        )
        logger.warning("payment start failed for order %s: %s", order.number, type(error).__name__)
    return order, changeset


# --- payment (PAY.1, PAY.2) ------------------------------------------------------------------


class PaymentMismatch(Exception):
    """The payment reference does not belong to this order (the page answers 404)."""


class PaymentUnavailable(Exception):
    """The payment provider refused or failed; the order stays awaiting payment."""


def _open_payment_order(order: Order) -> bool:
    return order.state == "awaiting_payment" and not order.payment_intent_ref


def start_payment(order: Order, *, ctx: AuditContext, dry_run: bool = False) -> str:
    """Create the one payment of an order awaiting payment and return its client secret.

    One payment per order: an order that already has one gets none (returns ""), and the provider
    call carries a key derived from the order, so a repeat can never charge twice. Returns "" too
    when no provider is configured. The amount is the order's gross total in `CURRENCY`; the
    `transfer_group` is the order number (D-08: separate charges and transfers). The PAYMENTS
    switch refuses a new payment (`CheckoutClosed`, ORD.10); a provider failure leaves the order
    awaiting payment (`PaymentUnavailable` is swallowed here and the secret is empty)."""
    starter = getattr(settings, "ORDER_PAYMENT_STARTER", "")
    order.refresh_from_db()
    if not starter or not _open_payment_order(order):
        return ""
    ensure_checkout_open([sub.vendor for sub in order.vendor_orders.select_related("vendor")])
    if dry_run:  # the checks above ran; nothing is sent to the provider
        return ""
    try:
        started = import_string(starter)(
            order_ref=order.transfer_group or order.number,
            amount=order.grand_total,
            currency=CURRENCY,
            idempotency_key=f"order-{order.public_id}",
        )
    except PaymentUnavailable:
        return ""
    if started is None:
        return ""
    provider_ref, client_secret = started
    with transaction.atomic():
        claimed = Order.objects.filter(pk=order.pk, payment_intent_ref="").update(
            payment_intent_ref=provider_ref
        )
        if claimed:
            changeset = ChangeSet()
            changeset.update(
                "orders.order",
                str(order.public_id),
                {"payment_intent_ref": ""},
                {"payment_intent_ref": provider_ref},
            )
            audit_changes(changeset, ctx=ctx, action="orders.payment_started")
    order.payment_intent_ref = provider_ref
    return client_secret if claimed else ""


def _audit_payment_problem(
    order_ref: str, action: str, detail: dict[str, Any], ctx: AuditContext
) -> None:
    changeset = ChangeSet()
    changeset.create("orders.order", order_ref, detail)
    audit_changes(changeset, ctx=ctx, action=action)


def mark_order_paid(
    order_ref: str,
    *,
    amount: Decimal,
    currency: str,
    ctx: AuditContext,
    provider_ref: str | None = None,
    dry_run: bool = False,
) -> bool:
    """The provider confirmed a payment for the order numbered `order_ref` (PAY.2): move it to
    `paid`, decrement the stock it held and release the holds, all in the caller's transaction.

    Idempotent: an order already paid is left alone (False). Nothing moves, and the problem is
    audited for staff, when no order has that number, the order is no longer awaiting payment
    (a late payment on a cancelled order), or the amount or currency is not the order's gross
    total, or (when `provider_ref` is given) the payment is not the one started for the order
    (`orders.payment_ref_mismatch`). Returns True when the order became (or, on a dry run, would
    become) paid; a dry run writes nothing."""

    def problem(ref: str, action: str, detail: dict[str, Any], ctx: AuditContext) -> None:
        if not dry_run:
            _audit_payment_problem(ref, action, detail, ctx)

    with transaction.atomic():
        order = Order.objects.select_for_update().filter(number=order_ref).first()
        if order is None:
            problem(order_ref, "orders.payment_unmatched", {"amount": amount}, ctx)
            return False
        if order.state in PAID_OR_LATER:
            return False
        if order.state != "awaiting_payment":
            problem(order_ref, "orders.payment_late", {"state": order.state, "amount": amount}, ctx)
            return False
        if provider_ref is not None and provider_ref != order.payment_intent_ref:
            problem(
                order_ref,
                "orders.payment_ref_mismatch",
                {"paid_with": provider_ref, "expected": order.payment_intent_ref},
                ctx,
            )
            return False
        if currency.upper() != CURRENCY or amount != order.grand_total:
            problem(
                order_ref,
                "orders.payment_mismatch",
                {"paid": amount, "currency": currency, "expected": order.grand_total},
                ctx,
            )
            return False
        if dry_run:
            return True
        transition_order(order, "paid", ctx=ctx)
        _decrement_stock(order, ctx=ctx)
    return True


def _decrement_stock(order: Order, *, ctx: AuditContext) -> None:
    changeset = ChangeSet()
    holds = list(StockReservation.objects.filter(order=order).order_by("product_id", "variant_id"))
    for hold in holds:
        row: Product | Variant
        if hold.variant_id is not None:
            row = Variant.objects.select_for_update().get(pk=hold.variant_id)
            model = "catalog.variant"
        else:
            row = Product.objects.select_for_update(of=("self",)).get(pk=hold.product_id)
            model = "catalog.product"
        before = row.stock_qty
        row.stock_qty = max(0, before - hold.quantity)
        row.save(update_fields=["stock_qty", "updated_at"])
        changeset.update(
            model, str(row.public_id), {"stock_qty": before}, {"stock_qty": row.stock_qty}
        )
    StockReservation.objects.filter(order=order).delete()
    audit_changes(changeset, ctx=ctx, action="orders.stock_decremented")


def record_payment_failed(order_ref: str, *, ctx: AuditContext) -> None:
    """The provider reported a failed attempt: the order stays awaiting payment (the customer may
    try again until the hold ends); the failure is audited."""
    _audit_payment_problem(order_ref, "orders.payment_failed", {}, ctx)


def _line_input(entry: PlaceLine, product: Product, variants: dict[int, Variant]) -> LineInput:
    variant = variants.get(entry.variant.pk) if entry.variant is not None else None
    unit_price = cart_engine.display_unit_price(
        product.price_net,
        vendors_services.effective_sales_mode(product),
        variant.price_net_override if variant else None,
    )
    if unit_price is None:  # _verify refused anything not for sale
        raise OrderRefused([Refusal("unavailable", product.pk, "This item is not for sale.")])
    return LineInput(
        product=product,
        sku=variant.sku if variant is not None else product.sku,
        title=entry.title,
        variant_label=entry.variant_label,
        unit_price_net=unit_price,  # the live price, equal to the one the customer saw
        quantity=entry.quantity,
        tax_rate=entry.tax.rate,
        tax_reason=_reason(entry.tax),
        vat_nature_code=entry.tax.nature_code or "",  # from the decision, never from a caller
        discount_net=entry.discount_net,
        legal_note_key=entry.tax.legal_note_key or "",
    )


def _reason(tax: TaxOutcome | None) -> str:
    if tax is None:
        return ""
    return str(getattr(tax.reason, "value", tax.reason))


def hold_stock(
    cart: Cart,
    product: Product,
    variant: Variant | None,
    quantity: int,
    *,
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Hold `quantity` units for a cart for ORDER_HOLD_MINUTES, replacing the cart's previous
    hold on the same item. Takes the same row locks as `place_order`, so a hold and an order for
    the last unit cannot both succeed. Raises `OrderRefused` (`out_of_stock`) when too few units
    are free. Returns the change set (empty when the item has no stock limit); a dry run
    verifies the stock and writes nothing."""
    now = now or timezone.now()
    with transaction.atomic():
        products, variants = _lock_live_rows(
            [PlaceLine(product, variant, quantity, "", Decimal("0"), _NO_TAX)]
        )
        live = products.get(product.pk)
        live_variant = variants.get(variant.pk) if variant is not None else None
        if live is None or (variant is not None and live_variant is None):
            raise OrderRefused([Refusal("unavailable", product.pk, "This item no longer exists.")])
        stock = live_variant if live_variant is not None else live
        limit = cart_engine.available_quantity(
            live.stock_policy, stock.stock_qty, stock.stock_status
        )
        changeset = ChangeSet()
        if limit is None:
            return changeset
        key = _stock_key(live, live_variant)
        free = max(0, limit - _held(key[0], key[1], now=now, cart=cart))
        if quantity > free:
            raise OrderRefused([Refusal("out_of_stock", live.pk, f"Only {free} available.")])
        expires = now + dt.timedelta(minutes=int(getattr(settings, "ORDER_HOLD_MINUTES", 60)))
        snapshot = {
            "cart": cart.pk,
            "product": key[0],
            "variant": key[1],
            "quantity": quantity,
            "expires_at": expires,
        }
        if dry_run:
            changeset.create(
                "orders.stockreservation", f"cart{cart.pk}:{key[0]}:{key[1]}", snapshot
            )
            return changeset
        own = StockReservation.objects.filter(cart=cart, product_id=key[0])
        (
            own.filter(variant__isnull=True) if key[1] is None else own.filter(variant_id=key[1])
        ).delete()
        hold = StockReservation.objects.create(
            product_id=key[0], variant_id=key[1], cart=cart, quantity=quantity, expires_at=expires
        )
        changeset.create("orders.stockreservation", str(hold.public_id), snapshot)
        return changeset


# --- the customer's own orders (ORD.7) ---------------------------------------------------------


def customer_orders(user: User) -> QuerySet[Order]:
    """The orders a signed-in customer may see in "My orders": their own, never anyone else's.

    Only `active` orders (D-30): a legacy order (MIG.4) is shown when the vendor's migration
    choice (D-16) kept it active; the rest of the last ten years sits in the restricted tax
    archive, visible to the Tax/Audit permission only, so it never shows here. An order still in
    `checkout` is a draft and is not listed. The checkout, order confirmation and payment pages
    own the draft."""
    if not user.is_authenticated:
        return Order.objects.none()
    orders = Order.objects.filter(customer=user, privacy_status=PrivacyStatus.ACTIVE).exclude(
        state=OrderState.CHECKOUT
    )
    # Hook for the restricted tax archive flag (ORD.14, draft #338): once `Order` has it, an
    # archive row is out whatever its `privacy_status`. Boolean field named `tax_archive`.
    if any(field.name == "tax_archive" for field in Order._meta.get_fields()):
        orders = orders.filter(**{"tax_archive": False})
    return orders


def customer_order(user: User, public_id: uuid.UUID) -> Order | None:
    """One order of the customer, or None (the view answers 404 for a stranger's order too)."""
    return (
        customer_orders(user)
        .prefetch_related(
            Prefetch(
                "vendor_orders",
                queryset=VendorOrder.objects.select_related("vendor").prefetch_related("lines"),
            )
        )
        .filter(public_id=public_id)
        .first()
    )


def carrier_tracking_templates(names: Iterable[str]) -> dict[str, str]:
    """The tracking-link template of each carrier name the vendors typed, by the name as typed.

    A name matches a carrier by its code (case-insensitive, spaces as dashes). A name with no
    carrier record has no link: the customer still sees the carrier and the number."""
    wanted = {name: slugify(name) for name in names if name}
    carriers = dict(
        Carrier.objects.filter(code__in=set(wanted.values()), is_active=True).values_list(
            "code", "tracking_url_template"
        )
    )
    return {name: carriers[code] for name, code in wanted.items() if carriers.get(code)}


def payment_client_secret(order: Order) -> str:
    """The browser-side secret of the order's payment, asked from the provider each time (never
    stored). Raises `PaymentMismatch` when the provider says the payment is not this order's and
    `PaymentUnavailable` when it cannot answer."""
    fetcher = getattr(settings, "ORDER_PAYMENT_BROWSER_HOOK", "")
    if not fetcher or not order.payment_intent_ref:
        raise PaymentUnavailable("no payment to confirm")
    return str(
        import_string(fetcher)(order.payment_intent_ref, order.transfer_group or order.number)
    )
