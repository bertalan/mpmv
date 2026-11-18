"""Versions of a vendor order (ORD.20, spec 07 §Order amendments): v1 from the placed order.

The `OrderLine` rows and the `VendorOrder` snapshot fields stay as they were placed; they are
version 1. `create_version_one` writes that version when the payment succeeds, and the migration
that introduced versions wrote it for the orders paid before. `current_version` reads the
version an amendment starts from.
"""

import datetime as dt
import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from apps.core.audit import VENDOR_FIELD
from apps.tax.engine import NORTHERN_IRELAND_CODE, is_northern_ireland

from . import amendment_engine as am
from . import engine
from .models import OrderLine, VendorOrder, VendorOrderVersion
from .tz import business_tz

DOMESTIC_REASON = "domestic_vendor_country"


def _country_of_taxation(rate: Decimal, reason: str, sub: VendorOrder) -> str | None:
    """What the tax engine decided as the country of taxation (TaxDecision), which the order
    line does not store: none for a zero rate, the vendor's country for a domestic sale, else the
    delivery country, which is `XI` for Northern Ireland (an OSS sale to `BT*` is taxed there,
    TAX.18)."""
    if rate == 0:
        return None
    if reason == DOMESTIC_REASON:
        return str((sub.vendor_snapshot or {}).get("country") or sub.vendor.country)
    postcode = str((sub.order.shipping_address or {}).get("postcode") or "")
    if is_northern_ireland(sub.order.shipping_country, postcode):
        return NORTHERN_IRELAND_CODE
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
                fiscal_category=line.fiscal_category,
                weight_kg=line.weight_kg,
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
    stored = choice.get("tax_portions") or []
    if sub.shipping_tax_rate is None:
        # ORD.29: split across rates. The portions are the only source of truth, so a vendor
        # order without them, or whose shipping VAT or net is not their sum, is refused here
        # instead of falling back to a single rate.
        engine.check_shipping_portions(
            sub.shipping_net,
            engine.stored_shipping_portions(choice),
            shipping_tax=sub.shipping_tax_amount,
        )
    if sub.shipping_net == 0:
        return ()
    raw = [
        (
            Decimal(str(p["net"])),
            Decimal(str(p["tax"])),
            Decimal(str(p["rate"])),
            str(p["reason"]),
            p.get("nature_code") or None,
            p.get("legal_note_key") or None,
        )
        for p in stored
    ]
    if not raw:  # one portion on the vendor order's own fields (a single rate)
        single = sub.shipping_tax_rate
        if single is None:  # unreachable: checked above
            raise engine.ShippingPortionsMismatch("shipping split across rates needs its portions")
        raw = [
            (
                sub.shipping_net,
                sub.shipping_tax_amount,
                single,
                sub.shipping_tax_reason or "",
                sub.shipping_vat_nature_code or None,
                None,
            )
        ]
    portions = []
    for net, tax, rate, reason, nature_code, legal_note_key in raw:
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
                legal_note_key=legal_note_key or (goods.legal_note_key if goods else None),
                nature_code=nature_code
                or (goods.nature_code if goods else (sub.shipping_vat_nature_code or None)),
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
    """The current version of `sub`. Every paid vendor order has one: v1 is written when the
    payment succeeds, and migration 0017 wrote it for the orders paid before."""
    if sub.current_version_id is None:
        raise am.AmendmentRefused(
            "This package has no version yet: it can be amended only once its order is paid."
        )
    return VendorOrderVersion.objects.get(pk=sub.current_version_id)


def digest(snapshot: Mapping[str, Any]) -> str:
    """The SHA-256 of a version snapshot in canonical JSON: names a version in the audit trail
    (spec 07 §Audit: the before and after version digests)."""
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def audit_snapshot(version: VendorOrderVersion) -> dict[str, Any]:
    """What the audit trail keeps of a version (no address: personal data)."""
    return {
        VENDOR_FIELD: str(version.vendor_order.vendor.public_id),
        "number": version.number,
        "digest": digest(version.snapshot),
        "goods_net": version.goods_net,
        "shipping_net": version.shipping_net,
        "tax_total": version.tax_total,
        "gross_total": version.gross_total,
    }


def delivery_address(sub: VendorOrder, version: VendorOrderVersion) -> dict[str, Any]:
    """The package's delivery address in `version`: its own from v2, the order's for v1."""
    if version.shipping_address is not None:
        return dict(version.shipping_address)
    address = dict(sub.order.shipping_address or {})
    address.setdefault("country", sub.order.shipping_country)
    return address
