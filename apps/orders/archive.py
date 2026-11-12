"""The restricted tax-only archive of migrated legacy orders (MIG.4, MIG.20, D-16, D-29, D-30).

A migrated order outside the vendor's migration choice is loaded as a normal `Order` with
`tax_archive=True` and `privacy_status="restricted_tax_only"`. It keeps the name, the billing
address and the VAT number, the amounts, dates, statuses, lines and tax lines, and the per-order
commission snapshot as recorded. The shipping address, phone, e-mail, IP address, user agent and
order notes are never read (`apps.orders.legacy` is a whitelist).

Visibility: only the Tax/Audit platform role sees the archive (`archive_orders`); customers and
vendors never do (the Manager lists exclude it). The role is `roles.TAX_AUDIT` in
`apps.manager.roles`; this app cannot import the manager, so the key is repeated here and a
manager test keeps the two equal.

Every migrated order gets a new MPMV number; the legacy number stays in `legacy_wp_order_id` and
is searchable (`find_archive_order`). Refunds are stored inside the order (`historical_refunds`),
with no refund model, no ledger entry and no tax recalculation.

Retention: `purge_expired_archive` takes the archive orders past `tax_retention_until` (31
December of year 10) out of the tax archive. The database refuses to delete an order row, so
"delete" is the D-30 scrub: every personal field is replaced by its placeholder (amounts, dates,
countries and rates stay, as CMP.29 keeps them for the ledger totals).
"""

import datetime as dt
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.vendors.models import Vendor

from . import engine, legacy
from .models import Order
from .services import LineInput, VendorOrderInput, anonymise_order, create_order
from .tz import business_tz

ARCHIVE_ROLE = "tax_audit"


def load_archive_order(
    record: Mapping[str, Any],
    *,
    vendors: Mapping[str, Vendor],
    ctx: AuditContext,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> tuple[Order | None, ChangeSet]:
    """Load one legacy order into the tax archive, numbered anew, audited (`orders.create`).

    `vendors` maps the record's `vendor_key` to the migrated `Vendor`. Raises
    `legacy.LegacyRecordError` (all problems) for a bad record, `legacy.OrderInProgress` for an
    order not closed on the legacy site, or a record older than the 10-year retention (not
    migrated with personal data). Idempotent: a legacy number already loaded returns the stored
    order and an empty ChangeSet. Dry run saves nothing and takes no number.
    """
    parsed = legacy.parse_record(record)
    stamp = now or timezone.now()
    tz = business_tz()
    if engine.retention_over(
        engine.tax_retention_until(parsed.created_at, tz), stamp.astimezone(tz).date()
    ):
        raise legacy.LegacyRecordError(
            ["the order is older than the 10-year tax retention: it is not migrated"]
        )
    existing = Order.objects.filter(legacy_wp_order_id=parsed.legacy_order_id).first()
    if existing is not None:
        return existing, ChangeSet()
    missing = sorted({v.vendor_key for v in parsed.vendors} - set(vendors))
    if missing:
        raise legacy.LegacyRecordError([f"unknown vendor key(s): {', '.join(missing)}"])
    inputs = [
        VendorOrderInput(
            vendor=vendors[v.vendor_key],
            lines=[
                LineInput(
                    product=None,
                    sku=line.sku,
                    title=line.title,
                    variant_label=line.variant_label,
                    unit_price_net=line.unit_price_net,
                    quantity=line.quantity,
                    discount_net=line.discount_net,
                    tax_rate=line.tax_rate,
                    tax_reason=line.tax_reason,
                )
                for line in v.lines
            ],
            shipping_net=v.shipping_net,
            shipping_tax_rate=v.shipping_tax_rate,
            shipping_tax_reason=v.shipping_tax_reason,
            state=parsed.vendor_state,
            commission_snapshot=v.commission,  # as recorded, never recomputed
        )
        for v in parsed.vendors
    ]
    try:
        with transaction.atomic():
            return create_order(
                email="",
                language="en",
                billing_address=parsed.billing_address,
                billing_country=parsed.billing_country,
                shipping_address={},
                shipping_country=parsed.shipping_country,
                vendor_orders=inputs,
                ctx=ctx,
                customer_name=parsed.customer_name,
                customer_vat_number=parsed.vat_number,
                customer_tax_status="b2b" if parsed.vat_number else "b2c",
                state=parsed.order_state,
                legacy_wp_order_id=parsed.legacy_order_id,
                placed_at=parsed.created_at,
                paid_at=parsed.paid_at,
                tax_archive=True,
                historical_refunds=list(parsed.refunds),
                dry_run=dry_run,
                now=stamp,
            )
    except ValueError as error:
        raise legacy.LegacyRecordError([str(error)]) from error


# --- visibility ------------------------------------------------------------------------------


def archive_orders(role_keys: Iterable[str]) -> QuerySet[Order]:
    """The tax archive for an actor holding these platform roles: all of it for the Tax/Audit
    role, nothing for any other role (an owner, finance or support user included)."""
    if ARCHIVE_ROLE not in set(role_keys):
        return Order.objects.none()
    return Order.objects.filter(tax_archive=True)


def find_archive_order(role_keys: Iterable[str], legacy_number: int | str) -> Order | None:
    """Search the archive by the legacy order number."""
    try:
        number = int(legacy_number)
    except (TypeError, ValueError):
        return None
    return archive_orders(role_keys).filter(legacy_wp_order_id=number).first()


def refunded_total(order: Order) -> Decimal:
    """The amount refunded on a migrated order, from the refunds stored inside it."""
    return sum((Decimal(r["amount"]) for r in order.historical_refunds), Decimal("0.00"))


# --- retention -------------------------------------------------------------------------------


def purge_expired_archive(
    *, ctx: AuditContext, dry_run: bool = False, now: dt.datetime | None = None
) -> ChangeSet:
    """Take every archive order past its 10-year retention out of the archive: the personal
    fields are replaced by their placeholders (the row itself cannot be deleted, D-29). Each
    order writes an `orders.anonymise` AuditLog entry; a dry run changes nothing and lists them.
    Idempotent."""
    stamp = now or timezone.now()
    today = stamp.astimezone(business_tz()).date()
    due = Order.objects.filter(
        tax_archive=True, privacy_status="restricted_tax_only", tax_retention_until__lt=today
    ).order_by("pk")
    changeset = ChangeSet()
    for order in due:
        changeset.changes.extend(
            anonymise_order(order, ctx=ctx, dry_run=dry_run, now=stamp).changes
        )
    return changeset
