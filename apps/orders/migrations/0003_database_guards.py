"""Database guards for orders (ORD.2): immutable snapshots, legal state moves, summed totals.

The Python layer (model `save()`, services) is not the only way to reach the tables:
`QuerySet.update()`, `bulk_update` and raw SQL all skip it. These triggers make the promises hold
for every client. They raise `check_violation`, which Django reports as `IntegrityError`.

- `orders_orderline`: no DELETE; UPDATE only to clear `product_id` (the product was deleted).
- `orders_order`, `orders_vendororder`: money, numbering, identity and snapshot columns cannot
  change; buyer data (e-mail, addresses) and tracking dates can (GDPR anonymisation, shipping).
- State: INSERT only in an initial state; UPDATE only along an arrow. The arrow lists below are
  a FROZEN COPY of `apps.orders.engine.ORDER_TRANSITIONS` and `VENDOR_ORDER_TRANSITIONS` at the
  time of this migration; changing the machine needs a new migration that replaces the function,
  and `tests/integration/test_db_guards.py` fails while engine and database disagree.
- Sums are checked at COMMIT by deferred constraint triggers: each sub-order equals its lines
  (goods net, tax = line tax + shipping tax) and each order equals its sub-orders.
"""

from django.db import migrations

ORDER_ARROWS = """
        ('checkout', 'awaiting_payment'),
        ('checkout', 'quote_flow'),
        ('quote_flow', 'awaiting_payment'),
        ('awaiting_payment', 'cancelled'),
        ('awaiting_payment', 'paid'),
        ('paid', 'in_fulfillment'),
        ('paid', 'partially_refunded'),
        ('in_fulfillment', 'fulfilled'),
        ('in_fulfillment', 'partially_refunded'),
        ('fulfilled', 'closed'),
        ('fulfilled', 'partially_refunded'),
        ('partially_refunded', 'refunded')"""

VENDOR_ORDER_ARROWS = """
        ('pending', 'accepted'),
        ('pending', 'cancelled'),
        ('accepted', 'cancelled'),
        ('accepted', 'refunded'),
        ('accepted', 'shipped'),
        ('shipped', 'delivered'),
        ('shipped', 'refunded'),
        ('delivered', 'completed'),
        ('delivered', 'refunded'),
        ('completed', 'refunded')"""

FORWARD = f"""
CREATE FUNCTION orders_orderline_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'orders_orderline is an immutable snapshot (DELETE refused)'
            USING ERRCODE = 'check_violation';
    END IF;
    IF (to_jsonb(NEW) - 'product_id') IS DISTINCT FROM (to_jsonb(OLD) - 'product_id')
       OR NOT (NEW.product_id IS NULL OR NEW.product_id IS NOT DISTINCT FROM OLD.product_id) THEN
        RAISE EXCEPTION 'orders_orderline is an immutable snapshot (only product_id may be cleared)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER orders_orderline_immutable
    BEFORE UPDATE OR DELETE ON orders_orderline
    FOR EACH ROW EXECUTE FUNCTION orders_orderline_guard();

CREATE FUNCTION orders_order_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.state NOT IN ('checkout', 'quote_flow', 'awaiting_payment') THEN
            RAISE EXCEPTION 'an order cannot start in state %', NEW.state
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.public_id IS DISTINCT FROM OLD.public_id
       OR NEW.number IS DISTINCT FROM OLD.number
       OR NEW.customer_tax_status IS DISTINCT FROM OLD.customer_tax_status
       OR NEW.goods_net IS DISTINCT FROM OLD.goods_net
       OR NEW.shipping_net IS DISTINCT FROM OLD.shipping_net
       OR NEW.tax_total IS DISTINCT FROM OLD.tax_total
       OR NEW.grand_total IS DISTINCT FROM OLD.grand_total
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'orders_order: number, totals and tax status are immutable'
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

CREATE TRIGGER orders_order_guard
    BEFORE INSERT OR UPDATE ON orders_order
    FOR EACH ROW EXECUTE FUNCTION orders_order_guard();

CREATE FUNCTION orders_vendororder_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'pending' THEN
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

CREATE TRIGGER orders_vendororder_guard
    BEFORE INSERT OR UPDATE ON orders_vendororder
    FOR EACH ROW EXECUTE FUNCTION orders_vendororder_guard();

CREATE FUNCTION orders_check_totals(p_order bigint) RETURNS void AS $$
DECLARE
    o orders_order%ROWTYPE;
    sub RECORD;
    sums RECORD;
BEGIN
    SELECT * INTO o FROM orders_order WHERE id = p_order;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    FOR sub IN
        SELECT v.id, v.goods_net, v.tax_total, v.shipping_tax_amount,
               COALESCE(SUM(l.net_total), 0) AS lines_net,
               COALESCE(SUM(l.tax_amount), 0) AS lines_tax
        FROM orders_vendororder v
        LEFT JOIN orders_orderline l ON l.vendor_order_id = v.id
        WHERE v.order_id = p_order
        GROUP BY v.id
    LOOP
        IF sub.goods_net <> sub.lines_net
           OR sub.tax_total <> sub.lines_tax + sub.shipping_tax_amount THEN
            RAISE EXCEPTION 'vendor order % does not equal the sum of its lines', sub.id
                USING ERRCODE = 'check_violation';
        END IF;
    END LOOP;
    SELECT COALESCE(SUM(goods_net), 0) AS goods_net,
           COALESCE(SUM(shipping_net), 0) AS shipping_net,
           COALESCE(SUM(tax_total), 0) AS tax_total,
           COALESCE(SUM(gross_total), 0) AS grand_total
      INTO sums FROM orders_vendororder WHERE order_id = p_order;
    IF o.goods_net <> sums.goods_net OR o.shipping_net <> sums.shipping_net
       OR o.tax_total <> sums.tax_total OR o.grand_total <> sums.grand_total THEN
        RAISE EXCEPTION 'order % does not equal the sum of its vendor orders', o.number
            USING ERRCODE = 'check_violation';
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION orders_totals_trigger() RETURNS trigger AS $$
DECLARE
    target bigint;
BEGIN
    IF TG_TABLE_NAME = 'orders_order' THEN
        target := NEW.id;
    ELSIF TG_TABLE_NAME = 'orders_vendororder' THEN
        target := NEW.order_id;
    ELSE
        SELECT order_id INTO target FROM orders_vendororder WHERE id = NEW.vendor_order_id;
    END IF;
    PERFORM orders_check_totals(target);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER orders_order_totals
    AFTER INSERT OR UPDATE ON orders_order
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION orders_totals_trigger();
CREATE CONSTRAINT TRIGGER orders_vendororder_totals
    AFTER INSERT OR UPDATE ON orders_vendororder
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION orders_totals_trigger();
CREATE CONSTRAINT TRIGGER orders_orderline_totals
    AFTER INSERT OR UPDATE ON orders_orderline
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION orders_totals_trigger();
"""  # noqa: S608 - only constants of this file are interpolated

REVERSE = """
DROP TRIGGER orders_orderline_totals ON orders_orderline;
DROP TRIGGER orders_vendororder_totals ON orders_vendororder;
DROP TRIGGER orders_order_totals ON orders_order;
DROP FUNCTION orders_totals_trigger();
DROP FUNCTION orders_check_totals(bigint);
DROP TRIGGER orders_vendororder_guard ON orders_vendororder;
DROP FUNCTION orders_vendororder_guard();
DROP TRIGGER orders_order_guard ON orders_order;
DROP FUNCTION orders_order_guard();
DROP TRIGGER orders_orderline_immutable ON orders_orderline;
DROP FUNCTION orders_orderline_guard();
"""


class Migration(migrations.Migration):
    dependencies = [("orders", "0002_snapshots_and_checks")]

    operations = [migrations.RunSQL(FORWARD, reverse_sql=REVERSE)]
