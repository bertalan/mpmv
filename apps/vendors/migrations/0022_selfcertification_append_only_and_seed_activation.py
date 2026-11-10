"""CMP.25: make vendors_selfcertification append-only and seed the `activation` requirements.

UPDATE and DELETE raise, whatever the client, like vendors_contractacceptance (migration 0010).
The seed is the DSA article 30 trader data of every EU vendor (telephone and address; name and
email are always present) and, for the companies that must register in Italy, Germany, France
and Spain, the trade register entry. Sole proprietors and the legal form `other` are exempt
(decision on PR 181): staff add per-country rows. Rules are additive.
"""

from typing import Any

from django.db import migrations

REGISTERED_COUNTRIES = ("IT", "DE", "FR", "ES")
COMPANY_FORMS = ("partnership", "srl", "spa", "cooperative")
NOTE = "DSA article 30: trader data held before the vendor sells."

# (country, region, legal form, field); an empty country or form matches every one.
SEED = [
    ("", "eu", "", field)
    for field in ("phone", "registered_street", "registered_postal_code", "registered_city")
] + [
    (country, "", form, "business_registration_number")
    for country in REGISTERED_COUNTRIES
    for form in COMPANY_FORMS
]

FORBID = """
CREATE OR REPLACE FUNCTION vendors_selfcertification_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'vendors_selfcertification is append-only (% refused)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER vendors_selfcertification_no_update_delete
    BEFORE UPDATE OR DELETE ON vendors_selfcertification
    FOR EACH ROW EXECUTE FUNCTION vendors_selfcertification_append_only();
"""

ALLOW = """
DROP TRIGGER IF EXISTS vendors_selfcertification_no_update_delete ON vendors_selfcertification;
DROP FUNCTION IF EXISTS vendors_selfcertification_append_only();
"""


def seed(apps: Any, schema_editor: Any) -> None:
    LegalFieldRequirement = apps.get_model("vendors", "LegalFieldRequirement")
    for country, region, legal_form, field_name in SEED:
        LegalFieldRequirement.objects.get_or_create(
            country=country,
            region=region,
            legal_form=legal_form,
            field_name=field_name,
            purpose="activation",
            defaults={"note": NOTE, "active": True},
        )


def unseed(apps: Any, schema_editor: Any) -> None:
    LegalFieldRequirement = apps.get_model("vendors", "LegalFieldRequirement")
    for country, region, legal_form, field_name in SEED:
        LegalFieldRequirement.objects.filter(
            country=country,
            region=region,
            legal_form=legal_form,
            field_name=field_name,
            purpose="activation",
            note=NOTE,  # rows staff re-created with another note stay
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("vendors", "0021_platform_duties_activation"),
    ]

    operations = [
        migrations.RunSQL(FORBID, reverse_sql=ALLOW),
        migrations.RunPython(seed, unseed),
    ]
