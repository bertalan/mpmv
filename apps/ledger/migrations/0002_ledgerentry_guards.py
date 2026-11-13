"""Database guards of ledger_ledgerentry (VEN.4).

- UPDATE and DELETE raise, whatever the client (ORM, raw SQL, admin), like core_auditlog. TRUNCATE
  is not blocked because the test runner flushes tables with it.
- The insert trigger sets `balance_after`, the vendor's running balance including the new row.
  A per-vendor advisory lock serialises concurrent inserts of one vendor, so two writers never
  compute from the same previous balance, and rows written by raw SQL get the right value too.
"""

from django.db import migrations

FORBID = """
CREATE FUNCTION ledger_ledgerentry_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ledger_ledgerentry is append-only (% refused)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_ledgerentry_no_update_delete
    BEFORE UPDATE OR DELETE ON ledger_ledgerentry
    FOR EACH ROW EXECUTE FUNCTION ledger_ledgerentry_append_only();

CREATE FUNCTION ledger_ledgerentry_running_balance() RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('ledger_running_balance', NEW.vendor_id));
    NEW.balance_after := NEW.amount + COALESCE(
        (SELECT SUM(amount) FROM ledger_ledgerentry WHERE vendor_id = NEW.vendor_id), 0);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_ledgerentry_running_balance
    BEFORE INSERT ON ledger_ledgerentry
    FOR EACH ROW EXECUTE FUNCTION ledger_ledgerentry_running_balance();
"""

ALLOW = """
DROP TRIGGER IF EXISTS ledger_ledgerentry_running_balance ON ledger_ledgerentry;
DROP FUNCTION IF EXISTS ledger_ledgerentry_running_balance();
DROP TRIGGER IF EXISTS ledger_ledgerentry_no_update_delete ON ledger_ledgerentry;
DROP FUNCTION IF EXISTS ledger_ledgerentry_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_ledgerentry")]

    operations = [migrations.RunSQL(FORBID, reverse_sql=ALLOW)]
