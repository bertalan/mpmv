"""Freeze an accepted contract version and its texts at the database level (VEN.5).

Once a `vendors_contractacceptance` row references a contract version, the database refuses any
UPDATE or DELETE of the version (`vendors_vendorcontract`) and any INSERT, UPDATE or DELETE of
its texts (`vendors_vendorcontracttranslation`), whatever the client (ORM, queryset, raw SQL).
The accepted text is the legal evidence; the acceptance only keeps its hash.

Each check first locks the version row (`FOR UPDATE`), as `services.accept_contract` does, so
a write and an acceptance of the same version run one after the other: a write that waited for
an acceptance sees it and is refused. TRUNCATE is not blocked because the test runner flushes
tables with it.
"""

from django.db import migrations

FORBID = """
CREATE OR REPLACE FUNCTION vendors_contract_check_not_accepted(contract bigint, op text)
RETURNS void AS $$
BEGIN
    PERFORM 1 FROM vendors_vendorcontract WHERE id = contract FOR UPDATE;
    IF EXISTS (SELECT 1 FROM vendors_contractacceptance WHERE contract_id = contract) THEN
        RAISE EXCEPTION 'vendor contract % has been accepted: % refused', contract, op
            USING ERRCODE = 'insufficient_privilege';
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION vendors_vendorcontract_locked() RETURNS trigger AS $$
BEGIN
    PERFORM vendors_contract_check_not_accepted(OLD.id, TG_OP);
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER vendors_vendorcontract_locked_once_accepted
    BEFORE UPDATE OR DELETE ON vendors_vendorcontract
    FOR EACH ROW EXECUTE FUNCTION vendors_vendorcontract_locked();

CREATE OR REPLACE FUNCTION vendors_vendorcontracttranslation_locked() RETURNS trigger AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        PERFORM vendors_contract_check_not_accepted(OLD.contract_id, TG_OP);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF TG_OP = 'INSERT' OR NEW.contract_id IS DISTINCT FROM OLD.contract_id THEN
        PERFORM vendors_contract_check_not_accepted(NEW.contract_id, TG_OP);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER vendors_vendorcontracttranslation_locked_once_accepted
    BEFORE INSERT OR UPDATE OR DELETE ON vendors_vendorcontracttranslation
    FOR EACH ROW EXECUTE FUNCTION vendors_vendorcontracttranslation_locked();
"""

ALLOW = """
DROP TRIGGER IF EXISTS vendors_vendorcontracttranslation_locked_once_accepted
    ON vendors_vendorcontracttranslation;
DROP FUNCTION IF EXISTS vendors_vendorcontracttranslation_locked();
DROP TRIGGER IF EXISTS vendors_vendorcontract_locked_once_accepted ON vendors_vendorcontract;
DROP FUNCTION IF EXISTS vendors_vendorcontract_locked();
DROP FUNCTION IF EXISTS vendors_contract_check_not_accepted(bigint, text);
"""


class Migration(migrations.Migration):
    dependencies = [("vendors", "0010_contractacceptance_append_only")]

    operations = [migrations.RunSQL(FORBID, reverse_sql=ALLOW)]
