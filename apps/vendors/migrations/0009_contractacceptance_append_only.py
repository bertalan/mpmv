"""Make vendors_contractacceptance append-only at the database level (VEN.5).

UPDATE and DELETE raise, whatever the client (ORM, raw SQL, admin), like core_auditlog. TRUNCATE
is not blocked because the test runner flushes tables with it.
"""

from django.db import migrations

FORBID = """
CREATE OR REPLACE FUNCTION vendors_contractacceptance_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'vendors_contractacceptance is append-only (% refused)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER vendors_contractacceptance_no_update_delete
    BEFORE UPDATE OR DELETE ON vendors_contractacceptance
    FOR EACH ROW EXECUTE FUNCTION vendors_contractacceptance_append_only();
"""

ALLOW = """
DROP TRIGGER IF EXISTS vendors_contractacceptance_no_update_delete ON vendors_contractacceptance;
DROP FUNCTION IF EXISTS vendors_contractacceptance_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("vendors", "0008_onboarding_contract")]

    operations = [migrations.RunSQL(FORBID, reverse_sql=ALLOW)]
