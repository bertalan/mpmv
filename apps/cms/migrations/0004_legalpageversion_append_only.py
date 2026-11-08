"""Make cms_legalpageversion append-only at the database level (CMS.5).

UPDATE and DELETE raise, whatever the client (ORM, raw SQL, admin), like core_auditlog. TRUNCATE
is not blocked because the test runner flushes tables with it.
"""

from django.db import migrations

FORBID = """
CREATE OR REPLACE FUNCTION cms_legalpageversion_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'cms_legalpageversion is append-only (% refused)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER cms_legalpageversion_no_update_delete
    BEFORE UPDATE OR DELETE ON cms_legalpageversion
    FOR EACH ROW EXECUTE FUNCTION cms_legalpageversion_append_only();
"""

ALLOW = """
DROP TRIGGER IF EXISTS cms_legalpageversion_no_update_delete ON cms_legalpageversion;
DROP FUNCTION IF EXISTS cms_legalpageversion_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("cms", "0003_legal_and_recruitment_pages")]

    operations = [migrations.RunSQL(FORBID, reverse_sql=ALLOW)]
