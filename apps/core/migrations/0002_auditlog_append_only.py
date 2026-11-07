"""Make core_auditlog append-only at the database level.

UPDATE and DELETE raise, whatever the client (ORM, raw SQL, admin). TRUNCATE is not blocked
because the test runner flushes tables with it; in STAGE/LIVE the application role is not
granted TRUNCATE (see OPS tasks).
"""

from django.db import migrations

FORBID = """
CREATE OR REPLACE FUNCTION core_auditlog_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'core_auditlog is append-only (% refused)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER core_auditlog_no_update_delete
    BEFORE UPDATE OR DELETE ON core_auditlog
    FOR EACH ROW EXECUTE FUNCTION core_auditlog_append_only();
"""

ALLOW = """
DROP TRIGGER IF EXISTS core_auditlog_no_update_delete ON core_auditlog;
DROP FUNCTION IF EXISTS core_auditlog_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [migrations.RunSQL(FORBID, reverse_sql=ALLOW)]
