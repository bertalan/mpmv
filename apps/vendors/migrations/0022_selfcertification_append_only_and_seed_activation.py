"""CMP.25: make vendors_selfcertification append-only and seed the `activation` requirements.

UPDATE and DELETE raise, whatever the client, like vendors_contractacceptance (migration 0010).
The seed is the DSA article 30 trader data of every EU vendor (telephone and address; name and
email are always present) and the trade register entry of the legal forms that are registered
(not sole proprietors). Staff edit the rows afterwards; rules are additive.
"""

from typing import Any

from django.db import migrations

# Frozen copy of the member states at the time of this migration.
EU_MEMBER_STATES = [
    "AT",
    "BE",
    "BG",
    "CY",
    "CZ",
    "DE",
    "DK",
    "EE",
    "ES",
    "FI",
    "FR",
    "GR",
    "HR",
    "HU",
    "IE",
    "IT",
    "LT",
    "LU",
    "LV",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SE",
    "SI",
    "SK",
]
REGISTERED_FORMS = ("partnership", "srl", "spa", "cooperative", "other")
NOTE = "DSA article 30: trader data held before the vendor sells."

SEED = [
    ("", field)
    for field in ("phone", "registered_street", "registered_postal_code", "registered_city")
] + [(form, "business_registration_number") for form in REGISTERED_FORMS]

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


def _rows() -> list[tuple[str, str]]:
    """(legal form, field) pairs; the empty form matches every legal form."""
    return SEED


def seed(apps: Any, schema_editor: Any) -> None:
    LegalFieldRequirement = apps.get_model("vendors", "LegalFieldRequirement")
    for legal_form, field_name in _rows():
        LegalFieldRequirement.objects.get_or_create(
            country="",
            region="eu",
            legal_form=legal_form,
            field_name=field_name,
            purpose="activation",
            defaults={"note": NOTE, "active": True},
        )


def unseed(apps: Any, schema_editor: Any) -> None:
    LegalFieldRequirement = apps.get_model("vendors", "LegalFieldRequirement")
    for legal_form, field_name in _rows():
        LegalFieldRequirement.objects.filter(
            country="",
            region="eu",
            legal_form=legal_form,
            field_name=field_name,
            purpose="activation",
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("vendors", "0021_platform_duties_activation"),
    ]

    operations = [
        migrations.RunSQL(FORBID, reverse_sql=ALLOW),
        migrations.RunPython(seed, unseed),
    ]
