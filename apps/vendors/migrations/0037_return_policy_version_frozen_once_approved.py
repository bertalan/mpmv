"""Freeze an approved return-policy version at the database level (VEN.40 2/3).

Once `vendors_returnpolicyversion.status` is `approved`, the database refuses any DELETE and
any UPDATE, whatever the client (ORM, queryset, raw SQL). The only write allowed is clearing
`reviewed_by` to NULL: that column is `ON DELETE SET NULL` towards the staff user, so deleting
or erasing that account must keep working. The service never writes an approved row (a newer
approval does not touch the older one: the public policy is the newest approved version).
Pending and rejected versions are not frozen. TRUNCATE is not blocked because the test runner
flushes tables with it.
"""

from django.db import migrations

FORBID = """
CREATE OR REPLACE FUNCTION vendors_returnpolicyversion_frozen() RETURNS trigger AS $$
BEGIN
    IF OLD.status = 'approved' THEN
        IF TG_OP = 'UPDATE'
           AND NEW.reviewed_by_id IS NULL
           AND (to_jsonb(NEW) - 'reviewed_by_id' - 'updated_at')
               = (to_jsonb(OLD) - 'reviewed_by_id' - 'updated_at') THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'vendors_returnpolicyversion %: an approved version is frozen (% refused)',
            OLD.id, TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER vendors_returnpolicyversion_frozen_once_approved
    BEFORE UPDATE OR DELETE ON vendors_returnpolicyversion
    FOR EACH ROW EXECUTE FUNCTION vendors_returnpolicyversion_frozen();
"""

ALLOW = """
DROP TRIGGER IF EXISTS vendors_returnpolicyversion_frozen_once_approved
    ON vendors_returnpolicyversion;
DROP FUNCTION IF EXISTS vendors_returnpolicyversion_frozen();
"""


class Migration(migrations.Migration):
    dependencies = [("vendors", "0036_vendor_role_fields")]

    operations = [migrations.RunSQL(FORBID, reverse_sql=ALLOW)]
