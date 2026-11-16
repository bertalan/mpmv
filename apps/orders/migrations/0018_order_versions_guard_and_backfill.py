"""ORD.20: vendor order versions are append-only, and every paid vendor order gets its v1.

The trigger refuses any DELETE and any UPDATE of a version, except the anonymisation scrub
(D-29, `mpmv.order_scrub` = `anonymise`) replacing a package's own address with the placeholder.

The backfill writes v1 of each vendor order from its lines and snapshot fields, as
`apps.orders.versions.version_one_snapshot` does, frozen here so a later change of that code
does not change this migration. Lines placed before ORD.20 have no fiscal-category or weight
snapshot: the backfill takes them from the product as it is now (no variant override, which the
line does not record), the only source left for those rows.

Size on LIVE: the backfill goes row by row (one vendor order, its lines and one insert at a time,
`iterator()`), inside the migration's transaction. That is fine for the order volume of the
first LIVE deploy; a much larger table would need a batched management command instead.
"""

import re
import zoneinfo
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import migrations

FORWARD = """
CREATE FUNCTION orders_vendororderversion_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'orders_vendororderversion is append-only (DELETE refused)'
            USING ERRCODE = 'check_violation';
    END IF;
    IF COALESCE(current_setting('mpmv.order_scrub', true), '') = 'anonymise'
       AND (to_jsonb(NEW) - 'shipping_address') = (to_jsonb(OLD) - 'shipping_address')
       AND NEW.shipping_address = '{"anonymised": true}'::jsonb THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'orders_vendororderversion is append-only (UPDATE refused)'
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER orders_vendororderversion_append_only
    BEFORE UPDATE OR DELETE ON orders_vendororderversion
    FOR EACH ROW EXECUTE FUNCTION orders_vendororderversion_guard();
"""

UNPAID_STATES = ("checkout", "quote_flow", "awaiting_payment", "cancelled")

BACKWARD = """
DROP TRIGGER IF EXISTS orders_vendororderversion_append_only ON orders_vendororderversion;
DROP FUNCTION IF EXISTS orders_vendororderversion_guard();
"""


_NORTHERN_IRELAND = re.compile(r"BT[0-9]")


def _country(rate: Decimal, reason: str, sub: Any) -> str | None:
    if rate == 0:
        return None
    if reason == "domestic_vendor_country":
        return str((sub.vendor_snapshot or {}).get("country") or sub.vendor.country)
    postcode = re.sub(r"\s+", "", str((sub.order.shipping_address or {}).get("postcode") or ""))
    if sub.order.shipping_country == "GB" and _NORTHERN_IRELAND.match(postcode.upper()):
        return "XI"  # an OSS sale to Northern Ireland is taxed in XI (TAX.18)
    return str(sub.order.shipping_country)


def _portion(net: Any, tax: Any, rate: Any, reason: str, on: str, **extra: Any) -> dict[str, Any]:
    return {
        "net": str(net),
        "tax": str(tax),
        "rate": str(rate),
        "reason": reason,
        "tax_on": on,
        "country_of_taxation": extra.get("country"),
        "legal_note_key": extra.get("note") or None,
        "nature_code": extra.get("nature") or None,
    }


def backfill(apps: Any, schema_editor: Any) -> None:
    vendor_order_model = apps.get_model("orders", "VendorOrder")
    version_model = apps.get_model("orders", "VendorOrderVersion")
    line_model = apps.get_model("orders", "OrderLine")
    tz = zoneinfo.ZoneInfo(settings.BUSINESS_TIME_ZONE)
    # v1 exists from the payment on: an order still unpaid gets it when its payment succeeds.
    subs = (
        vendor_order_model.objects.filter(current_version__isnull=True)
        .exclude(order__paid_at__isnull=True, order__state__in=UNPAID_STATES)
        .select_related("order", "vendor")
    )
    for sub in subs.iterator():
        # `tax_on` is the payment date (joint opinion 2026-11-16, art. 6 c. 4 DPR 633/72); a
        # legacy row with no recorded payment falls back to its creation day.
        on = (sub.order.paid_at or sub.order.created_at).astimezone(tz).date().isoformat()
        lines = []
        for line in (
            line_model.objects.filter(vendor_order=sub).select_related("product").order_by("id")
        ):
            product = line.product
            category = line.fiscal_category or (product.fiscal_category if product else "")
            weight = line.weight_kg if line.weight_kg is not None else (
                product.weight_kg if product else None
            )
            lines.append(
                {
                    "line_key": str(line.public_id),
                    "sku": line.sku,
                    "title": line.title,
                    "unit_price_net": str(line.unit_price_net),
                    "quantity": line.quantity,
                    "discount_net": str(line.discount_net),
                    "portions": [
                        _portion(
                            line.net_total,
                            line.tax_amount,
                            line.tax_rate,
                            line.tax_reason,
                            on,
                            country=_country(line.tax_rate, line.tax_reason, sub),
                            note=line.legal_note_key,
                            nature=line.vat_nature_code,
                        )
                    ],
                    "product_id": line.product_id,
                    "variant_label": line.variant_label,
                    "replaces": "",
                    "fiscal_category": category,
                    "weight_kg": None if weight is None else str(weight),
                }
            )
        choice = dict(sub.shipping_choice or {})
        shipping_portions = []
        if sub.shipping_net:
            stored = choice.get("tax_portions") or [
                {
                    "net": sub.shipping_net,
                    "tax": sub.shipping_tax_amount,
                    "rate": sub.shipping_tax_rate,
                    "reason": sub.shipping_tax_reason,
                }
            ]
            for item in stored:
                rate, reason = Decimal(str(item["rate"])), str(item["reason"])
                goods = next(
                    (
                        entry["portions"][0]
                        for entry in lines
                        if Decimal(entry["portions"][0]["rate"]) == rate
                        and entry["portions"][0]["reason"] == reason
                    ),
                    None,
                )
                shipping_portions.append(
                    _portion(
                        item["net"],
                        item["tax"],
                        rate,
                        reason,
                        on,
                        country=goods["country_of_taxation"] if goods else _country(rate, reason, sub),
                        note=goods["legal_note_key"] if goods else None,
                        nature=goods["nature_code"] if goods else sub.shipping_vat_nature_code,
                    )
                )
        snapshot = {
            "lines": lines,
            "shipping": {
                "net": str(sub.shipping_net),
                "portions": shipping_portions,
                "key": str(choice.get("key") or ""),
                "label": str(choice.get("label") or ""),
                "carrier_code": str(choice.get("carrier_code") or ""),
                "zone_code": str(choice.get("zone_code") or ""),
                "min_days": choice.get("min_days"),
                "max_days": choice.get("max_days"),
            },
            "commission": dict(sub.commission_snapshot or {}),
            "destination_country": str(sub.order.shipping_country),
        }
        version = version_model.objects.create(
            vendor_order=sub,
            number=1,
            snapshot=snapshot,
            goods_net=sub.goods_net,
            shipping_net=sub.shipping_net,
            tax_total=sub.tax_total,
            gross_total=sub.gross_total,
        )
        vendor_order_model.objects.filter(pk=sub.pk).update(current_version=version)


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0017_orderline_amendment_snapshots"),
        ("catalog", "0015_field_help_text"),
    ]

    operations = [
        migrations.RunSQL(FORWARD, BACKWARD),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
