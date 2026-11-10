"""Re-check fixes for D-29 and MIG.4 (ORD.2).

- The trigger enforces the scrub gates itself, not only the `mpmv.order_scrub` setting:
  * anonymise: the Rome date is after 31 December of (Rome year of `created_at` + 10), and every
    changed personal column equals its fixed placeholder (the same values as
    `apps.orders.engine.ANONYMISED*`, frozen here);
  * minimise (and any change of `minimised_at`): the Rome date is on or after `created_at` +
    24 months. The trigger cannot read Django settings, so 24 months is frozen here as a FLOOR;
    `ORDER_ACTIVE_PHASE_MONTHS` can only make the phase longer (the service enforces that).
  The clock is the database's own `now()`.
- `created_at` is the insert time; an explicit value is accepted on INSERT only for a legacy
  order (`legacy_wp_order_id` set), so migrated orders keep their original date and the retention
  clock is right. For any other order the trigger resets it to `now()`.
Reverse restores the 0005 guard functions.
"""

import importlib
import re

from django.db import migrations, models
from django.utils import timezone

_previous = importlib.import_module("apps.orders.migrations.0005_fiscal_guards")


def functions(sql: str, *names: str) -> str:
    """The `CREATE [OR REPLACE] FUNCTION` blocks of `names` in `sql`, as CREATE OR REPLACE."""
    blocks = []
    for name in names:
        match = re.search(
            rf"CREATE (?:OR REPLACE )?FUNCTION {name}\(\).*?\$\$ LANGUAGE plpgsql;", sql, re.S
        )
        if match is None:
            raise RuntimeError(f"{name} not found")
        blocks.append(match.group(0).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))
    return "\n\n".join(blocks)


FORWARD = r"""
CREATE OR REPLACE FUNCTION orders_order_guard() RETURNS trigger AS $$
DECLARE
    mode text := COALESCE(current_setting('mpmv.order_scrub', true), '');
    today date := (now() AT TIME ZONE 'Europe/Rome')::date;
    placeholder jsonb := '{"anonymised": true}'::jsonb;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.legacy_wp_order_id IS NULL THEN
            IF NEW.state NOT IN ('checkout', 'quote_flow', 'awaiting_payment') THEN
                RAISE EXCEPTION 'an order cannot start in state %', NEW.state
                    USING ERRCODE = 'check_violation';
            END IF;
            NEW.created_at := now();
        END IF;
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
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'orders_order: number, countries, totals, legacy id are immutable'
            USING ERRCODE = 'check_violation';
    END IF;

    IF (NEW.email, NEW.customer_name, NEW.customer_vat_number, NEW.customer_tax_id,
        NEW.billing_address, NEW.shipping_address, NEW.vat_validation, NEW.anonymised_at)
       IS DISTINCT FROM
       (OLD.email, OLD.customer_name, OLD.customer_vat_number, OLD.customer_tax_id,
        OLD.billing_address, OLD.shipping_address, OLD.vat_validation, OLD.anonymised_at) THEN
        IF mode <> 'anonymise' THEN
            RAISE EXCEPTION 'orders_order: the fiscal snapshot is immutable (D-29)'
                USING ERRCODE = 'check_violation';
        END IF;
        IF today <= make_date(
               EXTRACT(year FROM OLD.created_at AT TIME ZONE 'Europe/Rome')::int + 10, 12, 31) THEN
            RAISE EXCEPTION 'orders_order: anonymisation is due after 31 December of year 10'
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

    IF NEW.minimised_at IS DISTINCT FROM OLD.minimised_at THEN
        IF mode NOT IN ('minimise', 'anonymise') THEN
            RAISE EXCEPTION 'orders_order: minimised_at is set by the scrub service only'
                USING ERRCODE = 'check_violation';
        END IF;
        IF today < ((OLD.created_at AT TIME ZONE 'Europe/Rome')::date
                    + interval '24 months')::date THEN
            RAISE EXCEPTION 'orders_order: the active phase (24 months) has not ended'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.state IS DISTINCT FROM OLD.state AND (OLD.state, NEW.state) NOT IN (__ARROWS__
    ) THEN
        RAISE EXCEPTION 'an order cannot go from % to %', OLD.state, NEW.state
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""".replace("__ARROWS__", _previous.ORDER_ARROWS)

REVERSE = functions(_previous.FORWARD, "orders_order_guard")


class Migration(migrations.Migration):
    dependencies = [("orders", "0005_fiscal_guards")]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="created_at",
            field=models.DateTimeField(default=timezone.now, editable=False),
        ),
        migrations.RunSQL(FORWARD, reverse_sql=REVERSE),
    ]
