"""Make compliance_withdrawalstatement append-only at the database level (CMP.6).

DELETE is refused. UPDATE is refused except one transition: `acknowledged_at` from NULL to a
value (the acknowledgement was queued after the row was written). Every other column, and a
second change of `acknowledged_at`, raises, whatever the client. TRUNCATE is not blocked
because the test runner flushes with it; the application role is not granted it in STAGE/LIVE.
"""

from django.db import migrations

FORBID = """
CREATE OR REPLACE FUNCTION compliance_withdrawalstatement_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND OLD.acknowledged_at IS NULL
       AND (to_jsonb(NEW) - 'acknowledged_at') = (to_jsonb(OLD) - 'acknowledged_at') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'compliance_withdrawalstatement is append-only (% refused)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER compliance_withdrawalstatement_no_change
    BEFORE UPDATE OR DELETE ON compliance_withdrawalstatement
    FOR EACH ROW EXECUTE FUNCTION compliance_withdrawalstatement_append_only();
"""

ALLOW = """
DROP TRIGGER IF EXISTS compliance_withdrawalstatement_no_change ON compliance_withdrawalstatement;
DROP FUNCTION IF EXISTS compliance_withdrawalstatement_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("compliance", "0006_withdrawal_statement")]

    operations = [migrations.RunSQL(FORBID, reverse_sql=ALLOW)]
