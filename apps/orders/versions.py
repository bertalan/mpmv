"""Versions of a vendor order (ORD.20, spec 07 §Order amendments): v1 from the placed order.

The `OrderLine` rows and the `VendorOrder` snapshot fields stay as they were placed; they are
version 1. `create_version_one` writes that version when the payment succeeds, and the migration
that introduced versions wrote it for the orders paid before. `current_version` reads the
version an amendment starts from.
"""

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from . import amendment_engine as am
from .models import OrderLine, VendorOrder, VendorOrderVersion
from .tz import business_tz

DOMESTIC_REASON = "domestic_vendor_country"


def _country_of_taxation(rate: Decimal, reason: str, sub: VendorOrder) -> str | None:
    """What the tax engine decided as the country of taxation (TaxDecision), which the order
    line does not store: none for a zero rate, the vendor's country for a domestic sale, else the
    delivery country."""
    if rate == 0:
        return None
    if reason == DOMESTIC_REASON:
        return str((sub.vendor_snapshot or {}).get("country") or sub.vendor.country)
    return str(sub.order.shipping_country)


def tax_date(sub: VendorOrder) -> dt.date:
    """The `tax_on` of v1: the day the order's payment succeeded, in the business time zone.

    Joint tax and legal opinion 2026-11-16: the paid part keeps the rate and rules of the original
    payment (art. 6 c. 4 DPR 633/72: a payment before delivery fixes the taxable event). The
    payment's success is `Order.paid_at`, stamped by the database when the payment webhook moves
    the order to `paid` (orders does not read the payments app). Only the date is set here: the
    rates stay the ones the checkout snapshotted, never re-rated. The creation day is a fallback
    for rows without a recorded payment (legacy orders in the backfill)."""
    paid_or_created = sub.order.paid_at or sub.order.created_at
    return paid_or_created.astimezone(business_tz()).date()


def version_one_snapshot(sub: VendorOrder, lines: Sequence[OrderLine]) -> am.VersionSnapshot:
    on = tax_date(sub)
    version_lines = []
    for line in lines:
        portion = am.VersionPortion(
            net=line.net_total,
            tax=line.tax_amount,
            rate=line.tax_rate,
            reason=line.tax_reason,
            tax_on=on,
            country_of_taxation=_country_of_taxation(line.tax_rate, line.tax_reason, sub),
            legal_note_key=line.legal_note_key or None,
            nature_code=line.vat_nature_code or None,
        )
        version_lines.append(
            am.VersionLine(
                line_key=str(line.public_id),
                sku=line.sku,
                title=line.title,
                unit_price_net=line.unit_price_net,
                quantity=line.quantity,
                discount_net=line.discount_net,
                portions=(portion,),
                product_id=line.product_id,
                variant_label=line.variant_label,
            )
        )
    choice: dict[str, Any] = dict(sub.shipping_choice or {})
    return am.VersionSnapshot(
        lines=tuple(version_lines),
        shipping=am.VersionShipping(
            net=sub.shipping_net,
            portions=_shipping_portions(sub, version_lines, choice, on),
            key=str(choice.get("key") or ""),
            label=str(choice.get("label") or ""),
            carrier_code=str(choice.get("carrier_code") or ""),
            zone_code=str(choice.get("zone_code") or ""),
            min_days=choice.get("min_days"),
            max_days=choice.get("max_days"),
        ),
        commission=dict(sub.commission_snapshot or {}),
        destination_country=str(sub.order.shipping_country),
    )


def _shipping_portions(
    sub: VendorOrder, lines: Sequence[am.VersionLine], choice: dict[str, Any], on: dt.date
) -> tuple[am.VersionPortion, ...]:
    if sub.shipping_net == 0:
        return ()
    stored = choice.get("tax_portions") or []
    raw = [
        (Decimal(str(p["net"])), Decimal(str(p["tax"])), Decimal(str(p["rate"])), str(p["reason"]))
        for p in stored
    ]
    if not raw:  # one portion on the vendor order's own fields
        raw = [
            (
                sub.shipping_net,
                sub.shipping_tax_amount,
                sub.shipping_tax_rate,
                sub.shipping_tax_reason,
            )
        ]
    portions = []
    for net, tax, rate, reason in raw:
        # The decision the shipping followed is the one of its goods (TAX.17).
        goods = next(
            (
                line.portions[0]
                for line in lines
                if line.portions[0].rate == rate and line.portions[0].reason == reason
            ),
            None,
        )
        portions.append(
            am.VersionPortion(
                net=net,
                tax=tax,
                rate=rate,
                reason=reason,
                tax_on=on,
                country_of_taxation=(
                    goods.country_of_taxation if goods else _country_of_taxation(rate, reason, sub)
                ),
                legal_note_key=goods.legal_note_key if goods else None,
                nature_code=(
                    goods.nature_code if goods else (sub.shipping_vat_nature_code or None)
                ),
            )
        )
    return tuple(portions)


def _totals(snapshot: am.VersionSnapshot) -> dict[str, Decimal]:
    totals = snapshot.totals
    return {
        "goods_net": totals.goods_net,
        "shipping_net": totals.shipping_net,
        "tax_total": totals.tax_total,
        "gross_total": totals.gross_total,
    }


def create_version_one(sub: VendorOrder, lines: Sequence[OrderLine]) -> VendorOrderVersion:
    """Write v1 of a placed vendor order and point `current_version` at it."""
    snapshot = version_one_snapshot(sub, lines)
    version = VendorOrderVersion(
        vendor_order=sub, number=1, snapshot=snapshot.to_json(), **_totals(snapshot)
    )
    version.save()
    VendorOrder.objects.filter(pk=sub.pk).update(current_version=version)
    sub.current_version = version
    return version


def create_next_version(
    sub: VendorOrder,
    snapshot: am.VersionSnapshot,
    *,
    number: int,
    amendment: Any,
    shipping_address: dict[str, Any] | None,
) -> VendorOrderVersion:
    """Write version `number` of `sub` for an applied amendment and make it current."""
    version = VendorOrderVersion(
        vendor_order=sub,
        number=number,
        amendment=amendment,
        snapshot=snapshot.to_json(),
        shipping_address=shipping_address,
        **_totals(snapshot),
    )
    version.save()
    VendorOrder.objects.filter(pk=sub.pk).update(current_version=version)
    sub.current_version = version
    return version


def current_version(sub: VendorOrder) -> VendorOrderVersion:
    """The current version of `sub`, writing v1 first when the order has none (an order made
    outside `create_order`, such as a test fixture)."""
    if sub.current_version_id is not None:
        return VendorOrderVersion.objects.get(pk=sub.current_version_id)
    existing = VendorOrderVersion.objects.filter(vendor_order=sub).order_by("-number").first()
    if existing is not None:
        return existing
    return create_version_one(sub, list(OrderLine.objects.filter(vendor_order=sub)))


def delivery_address(sub: VendorOrder, version: VendorOrderVersion) -> dict[str, Any]:
    """The package's delivery address in `version`: its own from v2, the order's for v1."""
    if version.shipping_address is not None:
        return dict(version.shipping_address)
    address = dict(sub.order.shipping_address or {})
    address.setdefault("country", sub.order.shipping_country)
    return address
