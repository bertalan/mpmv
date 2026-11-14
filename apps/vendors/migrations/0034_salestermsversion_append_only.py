"""Make vendors_salestermsversion append-only at the database level (VEN.35).

A published version of a seller's sales terms is never edited or deleted: the next version
replaces it as the one in force. UPDATE and DELETE raise, whatever the client. TRUNCATE is not
blocked because the test runner flushes tables with it.
"""

from django.db import migrations

FORBID = """
CREATE OR REPLACE FUNCTION vendors_salestermsversion_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'vendors_salestermsversion is append-only (% refused)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER vendors_salestermsversion_no_update_delete
    BEFORE UPDATE OR DELETE ON vendors_salestermsversion
    FOR EACH ROW EXECUTE FUNCTION vendors_salestermsversion_append_only();
"""

ALLOW = """
DROP TRIGGER IF EXISTS vendors_salestermsversion_no_update_delete ON vendors_salestermsversion;
DROP FUNCTION IF EXISTS vendors_salestermsversion_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("vendors", "0033_sales_terms")]

    operations = [migrations.RunSQL(FORBID, reverse_sql=ALLOW)]
