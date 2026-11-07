"""Make api_apimutationlog append-only at the database level, like core_auditlog.

UPDATE and DELETE raise, whatever the client. TRUNCATE is not blocked because the test runner
flushes tables with it; in STAGE/LIVE the application role is not granted TRUNCATE (OPS tasks).
"""

from django.db import migrations

FORBID = """
CREATE OR REPLACE FUNCTION api_apimutationlog_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'api_apimutationlog is append-only (% refused)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER api_apimutationlog_no_update_delete
    BEFORE UPDATE OR DELETE ON api_apimutationlog
    FOR EACH ROW EXECUTE FUNCTION api_apimutationlog_append_only();
"""

ALLOW = """
DROP TRIGGER IF EXISTS api_apimutationlog_no_update_delete ON api_apimutationlog;
DROP FUNCTION IF EXISTS api_apimutationlog_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("api", "0002_mutation_log")]

    operations = [migrations.RunSQL(FORBID, reverse_sql=ALLOW)]
