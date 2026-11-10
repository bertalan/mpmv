"""Never-paid rule for anonymisation (D-30 re-check, ORD.2).

- `paid_at` is set by the trigger the first time `state` becomes `paid` and never changes. A legacy
  loader may supply it on INSERT (only with `legacy_wp_order_id`); a legacy order inserted in a
  paid-or-later state without it gets `created_at`. A legacy `cancelled` order without `paid_at`
  counts as never paid, so the loader must supply it for orders that were paid then cancelled.
- Immediate anonymisation (before `tax_retention_until`) needs `paid_at IS NULL` AND the state
  `cancelled` (expired and failed checkouts end there), or a checkout / quote_flow /
  awaiting_payment order abandoned for more than 30 days (frozen constant, mirrored by
  `engine.ABANDONED_AFTER_DAYS`). A live awaiting_payment order is never anonymised.
- An anonymised order cannot change state at all, nor in the same UPDATE as its anonymisation.
- `privacy_status = 'anonymized'` requires every personal field to be its placeholder in the
  same UPDATE.
Reverse restores the 0008 guard (0009 adds the column; its reverse drops it).
"""

import importlib
import re

from django.db import migrations

_previous = importlib.import_module("apps.orders.migrations.0008_privacy_guards")

BACKFILL = """
UPDATE orders_order SET paid_at = created_at
 WHERE paid_at IS NULL
   AND state IN ('paid', 'in_fulfillment', 'fulfilled', 'closed', 'partially_refunded', 'refunded');
SET CONSTRAINTS ALL IMMEDIATE;
"""

FUNCTION = r"""
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
            NEW.paid_at := NULL;
        ELSIF NEW.paid_at IS NULL AND NEW.state IN
              ('paid', 'in_fulfillment', 'fulfilled', 'closed', 'partially_refunded',
               'refunded') THEN
            NEW.paid_at := NEW.created_at;  -- a legacy order past `paid` was paid
        END IF;
        IF NEW.state = 'paid' AND NEW.paid_at IS NULL THEN
            NEW.paid_at := now();
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

    -- paid_at: set here when the order first becomes paid; never changes afterwards.
    IF OLD.paid_at IS NOT NULL THEN
        IF NEW.paid_at IS DISTINCT FROM OLD.paid_at THEN
            RAISE EXCEPTION 'orders_order: paid_at is immutable' USING ERRCODE = 'check_violation';
        END IF;
    ELSIF NEW.state = 'paid' THEN
        NEW.paid_at := now();
    ELSIF NEW.paid_at IS NOT NULL THEN
        RAISE EXCEPTION 'orders_order: paid_at is set by the database when the order is paid'
            USING ERRCODE = 'check_violation';
    END IF;

    -- No state change on an anonymised row, or together with the anonymisation.
    IF NEW.state IS DISTINCT FROM OLD.state
       AND (OLD.privacy_status = 'anonymized' OR NEW.privacy_status = 'anonymized') THEN
        RAISE EXCEPTION 'orders_order: an anonymised order cannot change state'
            USING ERRCODE = 'check_violation';
    END IF;

    -- Anonymise at once only if never paid (paid_at empty) and cancelled, or a checkout
    -- abandoned for 30 days (frozen); else from the day after the
    -- retention date (D-31).
    anonymise_ok := (OLD.paid_at IS NULL AND (
                        OLD.state = 'cancelled'
                        OR (OLD.state IN ('checkout', 'quote_flow', 'awaiting_payment')
                            AND now() - OLD.created_at > interval '30 days')))
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
            -- The invariant: anonymized means every personal field is its placeholder.
            IF NEW.email IS DISTINCT FROM 'anonymised@example.invalid'
               OR NEW.customer_name IS DISTINCT FROM 'ANONYMISED'
               OR NEW.customer_vat_number IS DISTINCT FROM 'ANONYMISED'
               OR NEW.customer_tax_id IS DISTINCT FROM 'ANONYMISED'
               OR NEW.billing_address IS DISTINCT FROM placeholder
               OR NEW.shipping_address IS DISTINCT FROM placeholder
               OR NEW.vat_validation IS DISTINCT FROM placeholder THEN
                RAISE EXCEPTION 'orders_order: anonymized needs every personal field replaced'
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
"""

_arrows = importlib.import_module("apps.orders.migrations.0003_database_guards").ORDER_ARROWS

FORWARD = BACKFILL + FUNCTION.replace("__ARROWS__", _arrows)


def _restore_0008() -> str:
    match = re.search(
        r"CREATE OR REPLACE FUNCTION orders_order_guard\(\).*?\$\$ LANGUAGE plpgsql;",
        _previous.FORWARD,
        re.S,
    )
    if match is None:
        raise RuntimeError("0008 guard not found")
    return match.group(0)


class Migration(migrations.Migration):
    dependencies = [("orders", "0009_paid_at")]

    operations = [migrations.RunSQL(FORWARD, reverse_sql=_restore_0008())]
