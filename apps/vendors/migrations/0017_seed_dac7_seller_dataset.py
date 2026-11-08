"""VEN.23: payout requirement rows for the DAC7 seller dataset (owner decision MQ-6).

The rows show staff what a payout needs; the DAC7 check (`engine.dac7_check`) enforces the
dataset whatever the rows say, including the parts a row cannot express (the place of birth of
an individual whose TIN country issues no TIN).
- Every vendor: the country that issued the TIN.
- Sole proprietors (individual sellers): the date of birth.
- Every other legal form (entity sellers): the business registration number.
The non-EU TIN row of 0007 is deactivated: whether a TIN or a place of birth is needed depends
on the issuing country, which the DAC7 check decides.
"""

from typing import Any

from django.db import migrations

ENTITY_FORMS = ("partnership", "srl", "spa", "cooperative", "other")

# (legal_form, field, note)
SEED = [
    ("", "tin_country", "DAC7: the country that issued the TIN."),
    ("sole_proprietorship", "date_of_birth", "DAC7: individual sellers, date of birth."),
    *(
        (form, "business_registration_number", "DAC7: entity sellers, registration number.")
        for form in ENTITY_FORMS
    ),
]
NON_EU_TIN = {"country": "", "region": "non_eu", "legal_form": "", "field_name": "tax_code"}


def seed(apps: Any, schema_editor: Any) -> None:
    LegalFieldRequirement = apps.get_model("vendors", "LegalFieldRequirement")
    for legal_form, field_name, note in SEED:
        LegalFieldRequirement.objects.get_or_create(
            country="",
            region="",
            legal_form=legal_form,
            field_name=field_name,
            purpose="payout",
            defaults={"note": note, "active": True},
        )
    LegalFieldRequirement.objects.filter(purpose="payout", **NON_EU_TIN).update(
        active=False, note="DAC7: the TIN, or a place of birth, is checked by the DAC7 rules."
    )


def unseed(apps: Any, schema_editor: Any) -> None:
    LegalFieldRequirement = apps.get_model("vendors", "LegalFieldRequirement")
    for legal_form, field_name, _ in SEED:
        LegalFieldRequirement.objects.filter(
            country="", region="", legal_form=legal_form, field_name=field_name, purpose="payout"
        ).delete()
    LegalFieldRequirement.objects.filter(purpose="payout", **NON_EU_TIN).update(
        active=True, note="Non-EU vendors: foreign TIN (DAC7, invoices)."
    )


class Migration(migrations.Migration):
    dependencies = [
        ("vendors", "0016_dac7_seller_dataset"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
