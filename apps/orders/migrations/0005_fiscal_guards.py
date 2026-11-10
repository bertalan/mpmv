"""Owner decision D-29 and MIG.4 in the database (ORD.2).

Replaces the guards of 0003:
- The fiscal snapshot (name, VAT and tax ids, e-mail, addresses, `vat_validation`) is immutable.
  The personal columns may change only inside the anonymisation scrub (`mpmv.order_scrub` =
  'anonymise', set with `set_config(..., true)` only by `apps.orders.services`); `minimised_at`
  only inside the 'minimise' or 'anonymise' scrub, `anonymised_at` only inside 'anonymise'.
  Countries, amounts, rates and the number never change, in any mode. The setting is advisory
  against accidents and plain SQL; the real gate is that application code goes through the
  services (and the database role in LIVE has no ad-hoc writers).
- A final state is accepted on INSERT only for a legacy order (`legacy_wp_order_id` set) and its
  sub-orders (MIG.4); everything else starts in an initial state.
- DELETE of orders and sub-orders is refused.
The state arrows are the same frozen copy as in 0003.
"""

import importlib
import re

from django.db import migrations

_guards = importlib.import_module("apps.orders.migrations.0003_database_guards")
ORDER_ARROWS = _guards.ORDER_ARROWS
VENDOR_ORDER_ARROWS = _guards.VENDOR_ORDER_ARROWS


def _restore(sql: str) -> str:
    """The 0003 guard functions as CREATE OR REPLACE."""
    blocks = []
    for name in ("orders_order_guard", "orders_vendororder_guard"):
        match = re.search(rf"CREATE FUNCTION {name}\(\).*?\$\$ LANGUAGE plpgsql;", sql, re.S)
        if match is None:
            raise RuntimeError(f"{name} not found")
        blocks.append(match.group(0).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))
    return "\n\n".join(blocks)


FORWARD = f"""
CREATE OR REPLACE FUNCTION orders_order_guard() RETURNS trigger AS $$
DECLARE
    mode text := COALESCE(current_setting('mpmv.order_scrub', true), '');
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.legacy_wp_order_id IS NULL
           AND NEW.state NOT IN ('checkout', 'quote_flow', 'awaiting_payment') THEN
            RAISE EXCEPTION 'an order cannot start in state %', NEW.state
                USING ERRCODE = 'check_violation';
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
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'orders_order: number, countries, totals and tax status are immutable'
            USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.email, NEW.customer_name, NEW.customer_vat_number, NEW.customer_tax_id,
        NEW.billing_address, NEW.shipping_address, NEW.vat_validation)
       IS DISTINCT FROM
       (OLD.email, OLD.customer_name, OLD.customer_vat_number, OLD.customer_tax_id,
        OLD.billing_address, OLD.shipping_address, OLD.vat_validation)
       AND mode <> 'anonymise' THEN
        RAISE EXCEPTION 'orders_order: the fiscal snapshot is immutable (D-29)'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.minimised_at IS DISTINCT FROM OLD.minimised_at
       AND mode NOT IN ('minimise', 'anonymise') THEN
        RAISE EXCEPTION 'orders_order: minimised_at is set by the scrub service only'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.anonymised_at IS DISTINCT FROM OLD.anonymised_at AND mode <> 'anonymise' THEN
        RAISE EXCEPTION 'orders_order: anonymised_at is set by the scrub service only'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state AND (OLD.state, NEW.state) NOT IN ({ORDER_ARROWS}
    ) THEN
        RAISE EXCEPTION 'an order cannot go from % to %', OLD.state, NEW.state
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION orders_vendororder_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'pending'
           AND (SELECT legacy_wp_order_id FROM orders_order WHERE id = NEW.order_id) IS NULL THEN
            RAISE EXCEPTION 'a vendor order cannot start in state %', NEW.state
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.public_id IS DISTINCT FROM OLD.public_id
       OR NEW.order_id IS DISTINCT FROM OLD.order_id
       OR NEW.vendor_id IS DISTINCT FROM OLD.vendor_id
       OR NEW.goods_net IS DISTINCT FROM OLD.goods_net
       OR NEW.shipping_net IS DISTINCT FROM OLD.shipping_net
       OR NEW.tax_total IS DISTINCT FROM OLD.tax_total
       OR NEW.gross_total IS DISTINCT FROM OLD.gross_total
       OR NEW.shipping_tax_rate IS DISTINCT FROM OLD.shipping_tax_rate
       OR NEW.shipping_tax_amount IS DISTINCT FROM OLD.shipping_tax_amount
       OR NEW.shipping_tax_reason IS DISTINCT FROM OLD.shipping_tax_reason
       OR NEW.shipping_vat_nature_code IS DISTINCT FROM OLD.shipping_vat_nature_code
       OR NEW.vendor_snapshot IS DISTINCT FROM OLD.vendor_snapshot
       OR NEW.shipping_choice IS DISTINCT FROM OLD.shipping_choice
       OR NEW.commission_snapshot IS DISTINCT FROM OLD.commission_snapshot
       OR NEW.tax_profile_snapshot IS DISTINCT FROM OLD.tax_profile_snapshot
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'orders_vendororder: identity, totals and snapshots are immutable'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state AND (OLD.state, NEW.state) NOT IN ({VENDOR_ORDER_ARROWS}
    ) THEN
        RAISE EXCEPTION 'a vendor order cannot go from % to %', OLD.state, NEW.state
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION orders_forbid_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% rows cannot be deleted', TG_TABLE_NAME
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER orders_order_no_delete
    BEFORE DELETE ON orders_order
    FOR EACH ROW EXECUTE FUNCTION orders_forbid_delete();
CREATE TRIGGER orders_vendororder_no_delete
    BEFORE DELETE ON orders_vendororder
    FOR EACH ROW EXECUTE FUNCTION orders_forbid_delete();
"""  # noqa: S608 - only constants of this file are interpolated

# Reversing drops the delete guards and puts the 0003 guard functions back (they do not know the
# columns that 0004's reverse removes, so `migrate orders 0003` leaves working triggers).
REVERSE = """
DROP TRIGGER orders_vendororder_no_delete ON orders_vendororder;
DROP TRIGGER orders_order_no_delete ON orders_order;
DROP FUNCTION orders_forbid_delete();
""" + _restore(_guards.FORWARD)


class Migration(migrations.Migration):
    dependencies = [("orders", "0004_fiscal_snapshot")]

    operations = [migrations.RunSQL(FORWARD, reverse_sql=REVERSE)]
