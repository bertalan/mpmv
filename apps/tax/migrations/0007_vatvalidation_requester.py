"""D-28: the VIES consultation log (requester, kind, detail, VIES date), append-only in the DB.

UPDATE and DELETE raise, like cms_legalpageversion; TRUNCATE stays allowed for the test runner.
"""

from django.db import migrations, models

FORBID = """
CREATE OR REPLACE FUNCTION tax_vatvalidation_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'tax_vatvalidation is append-only (% refused)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tax_vatvalidation_no_update_delete
    BEFORE UPDATE OR DELETE ON tax_vatvalidation
    FOR EACH ROW EXECUTE FUNCTION tax_vatvalidation_append_only();
"""

ALLOW = """
DROP TRIGGER IF EXISTS tax_vatvalidation_no_update_delete ON tax_vatvalidation;
DROP FUNCTION IF EXISTS tax_vatvalidation_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("tax", "0006_seed_special_territories"),
    ]

    operations = [
        migrations.AddField(
            model_name="vatvalidation",
            name="requester",
            field=models.CharField(
                blank=True,
                help_text="VAT number with prefix sent to VIES as requester (the vendor's).",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="vatvalidation",
            name="detail",
            field=models.CharField(
                blank=True, help_text="VIES error code or reason.", max_length=64
            ),
        ),
        migrations.AddField(
            model_name="vatvalidation",
            name="requester_kind",
            field=models.CharField(
                blank=True,
                help_text="vendor, operator (no vendor proof) or empty (anonymous).",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="vatvalidation",
            name="vies_request_date",
            field=models.DateTimeField(
                blank=True, help_text="VIES requestDate, next to the local checked_at.", null=True
            ),
        ),
        migrations.RunSQL(FORBID, reverse_sql=ALLOW),
    ]
