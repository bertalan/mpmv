"""D-30 in the database (ORD.2): `privacy_status` lifecycle and `tax_retention_until`.

Replaces the order guard of 0006:
- `tax_retention_until` is computed by the trigger on INSERT (31 December of the Rome year of
  `created_at` + 10) and never changes. A legacy order's `created_at` is its original date.
- `privacy_status` moves forward only: active -> restricted_tax_only -> anonymized, or active ->
  anonymized. A new order starts `active` (a legacy one may arrive in any status, MIG.4).
  * restricted_tax_only: scrub mode 'restrict' (24-month floor, as in 0006) or
    'restrict_erasure' (an account erasure: at once). The trigger cannot tell why; the erasure
    flow's service is the only caller of that mode.
  * anonymized: scrub mode 'anonymise', the Rome date after `tax_retention_until` (from the
    day after, D-31), OR at once for an order that never reached `paid`
    (checkout, quote_flow, awaiting_payment, cancelled). The personal columns may change only in
    that step and only to their fixed placeholders.
- The scrub mode is the transaction-local `mpmv.order_scrub`; dates use the database's `now()`.
Reverse restores the 0006 guard.
"""

import importlib
import re

from django.db import migrations

_previous = importlib.import_module("apps.orders.migrations.0006_created_at_and_scrub_gates")

FORWARD = r"""
CREATE OR REPLACE FUNCTION orders_order_guard() RETURNS trigger AS $$
DECLARE
    mode text := COALESCE(current_setting('mpmv.order_scrub', true), '');
    today date := (now() AT TIME ZONE 'Europe/Rome')::date;
    placeholder jsonb := '{"anonymised": true}'::jsonb;
    anonymise_ok boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.legacy_wp_order_id IS NULL THEN
            IF NEW.state NOT IN ('checkout', 'quote_flow', 'awaiting_payment') THEN
                RAISE EXCEPTION 'an order cannot start in state %', NEW.state
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.privacy_status <> 'active' THEN
                RAISE EXCEPTION 'an order starts active' USING ERRCODE = 'check_violation';
            END IF;
            NEW.created_at := now();
        END IF;
        NEW.tax_retention_until := make_date(
            EXTRACT(year FROM NEW.created_at AT TIME ZONE 'Europe/Rome')::int + 10, 12, 31);
        RETURN NEW;
    END IF;

    IF NEW.public_id IS DISTINCT FROM OLD.public_id
       OR NEW.number IS DISTINCT FROM OLD.number
       OR NEW.customer_tax_status IS DISTINCT FROM OLD.customer_tax_status
       OR NEW.billing_country IS DISTINCT FROM OLD.billing_country
       OR NEW.shipping_country IS DISTINCT FROM OLD.shipping_country
       OR NEW.goods_net IS DISTINCT FROM OLD.goods_net
       OR NEW.shipping_net IS DISTINCT FROM OLD.shipping_net
       OR NEW.tax_total IS DISTINCT FROM OLD.tax_total
       OR NEW.grand_total IS DISTINCT FROM OLD.grand_total
       OR NEW.legacy_wp_order_id IS DISTINCT FROM OLD.legacy_wp_order_id
       OR NEW.tax_retention_until IS DISTINCT FROM OLD.tax_retention_until
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'orders_order: number, countries, totals, legacy id, dates are immutable'
            USING ERRCODE = 'check_violation';
    END IF;

    anonymise_ok := OLD.state IN ('checkout', 'quote_flow', 'awaiting_payment', 'cancelled')
                    OR today > OLD.tax_retention_until;

    IF (NEW.email, NEW.customer_name, NEW.customer_vat_number, NEW.customer_tax_id,
        NEW.billing_address, NEW.shipping_address, NEW.vat_validation)
       IS DISTINCT FROM
       (OLD.email, OLD.customer_name, OLD.customer_vat_number, OLD.customer_tax_id,
        OLD.billing_address, OLD.shipping_address, OLD.vat_validation) THEN
        IF mode <> 'anonymise' OR NEW.privacy_status <> 'anonymized' THEN
            RAISE EXCEPTION 'orders_order: the fiscal snapshot is immutable (D-29)'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NOT anonymise_ok THEN
            RAISE EXCEPTION 'orders_order: anonymisation is due after the retention date'
                USING ERRCODE = 'check_violation';
        END IF;
        IF (NEW.email IS DISTINCT FROM OLD.email
               AND NEW.email IS DISTINCT FROM 'anonymised@example.invalid')
           OR (NEW.customer_name IS DISTINCT FROM OLD.customer_name
               AND NEW.customer_name IS DISTINCT FROM 'ANONYMISED')
           OR (NEW.customer_vat_number IS DISTINCT FROM OLD.customer_vat_number
               AND NEW.customer_vat_number IS DISTINCT FROM 'ANONYMISED')
           OR (NEW.customer_tax_id IS DISTINCT FROM OLD.customer_tax_id
               AND NEW.customer_tax_id IS DISTINCT FROM 'ANONYMISED')
           OR (NEW.billing_address IS DISTINCT FROM OLD.billing_address
               AND NEW.billing_address IS DISTINCT FROM placeholder)
           OR (NEW.shipping_address IS DISTINCT FROM OLD.shipping_address
               AND NEW.shipping_address IS DISTINCT FROM placeholder)
           OR (NEW.vat_validation IS DISTINCT FROM OLD.vat_validation
               AND NEW.vat_validation IS DISTINCT FROM placeholder) THEN
            RAISE EXCEPTION 'orders_order: personal fields may only take their fixed placeholders'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.privacy_status IS DISTINCT FROM OLD.privacy_status THEN
        IF (OLD.privacy_status, NEW.privacy_status) NOT IN (
            ('active', 'restricted_tax_only'),
            ('active', 'anonymized'),
            ('restricted_tax_only', 'anonymized')) THEN
            RAISE EXCEPTION 'privacy status cannot go from % to %',
                OLD.privacy_status, NEW.privacy_status USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.privacy_status = 'restricted_tax_only' THEN
            IF mode NOT IN ('restrict', 'restrict_erasure') THEN
                RAISE EXCEPTION 'orders_order: privacy status is changed by the scrub service only'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF mode = 'restrict' AND today < ((OLD.created_at AT TIME ZONE 'Europe/Rome')::date
                                              + interval '24 months')::date THEN
                RAISE EXCEPTION 'orders_order: the active phase (24 months) has not ended'
                    USING ERRCODE = 'check_violation';
            END IF;
        ELSE
            IF mode <> 'anonymise' THEN
                RAISE EXCEPTION 'orders_order: privacy status is changed by the scrub service only'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NOT anonymise_ok THEN
                RAISE EXCEPTION 'orders_order: anonymisation is due after the retention date'
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
    END IF;

    IF (NEW.restricted_at IS DISTINCT FROM OLD.restricted_at
        OR NEW.anonymised_at IS DISTINCT FROM OLD.anonymised_at) AND mode = '' THEN
        RAISE EXCEPTION 'orders_order: privacy dates are set by the scrub service only'
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.state IS DISTINCT FROM OLD.state AND (OLD.state, NEW.state) NOT IN (__ARROWS__
    ) THEN
        RAISE EXCEPTION 'an order cannot go from % to %', OLD.state, NEW.state
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""".replace("__ARROWS__", _previous._previous.ORDER_ARROWS)


def _restore_0006() -> str:
    match = re.search(
        r"CREATE OR REPLACE FUNCTION orders_order_guard\(\).*?\$\$ LANGUAGE plpgsql;",
        _previous.FORWARD,
        re.S,
    )
    if match is None:
        raise RuntimeError("0006 guard not found")
    return match.group(0)


class Migration(migrations.Migration):
    dependencies = [("orders", "0007_privacy_lifecycle")]

    operations = [migrations.RunSQL(FORWARD, reverse_sql=_restore_0006())]
