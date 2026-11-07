"""VEN.12: payout requirements for non-EU vendors and the DAC7 dataset (owner decision).

Rules are additive, on top of 0005:
- Vendors established outside the EU: IBAN and their foreign TIN (`tax_code`).
- Every vendor (DAC7, spec 11 CMP.1, and the FatturaPA `Sede`): registered office street, postal
  code, city and country, and the country of tax residence. Italy: the province too.
With 0005 (VAT number for EU vendors, codice fiscale for Italian ones) every vendor that may
request a payout has a VAT number or a TIN.
"""

from typing import Any

from django.db import migrations

# (country, region, field, note)
SEED = [
    ('', 'non_eu', 'iban', 'Non-EU vendors: account for payouts (DAC7).'),
    ('', 'non_eu', 'tax_code', 'Non-EU vendors: foreign TIN (DAC7, invoices).'),
    *(
        ('', '', field, 'DAC7 and FatturaPA Sede: registered office.')
        for field in (
            'registered_street',
            'registered_postal_code',
            'registered_city',
            'registered_country',
        )
    ),
    ('', '', 'tax_residence_country', 'DAC7: country of tax residence.'),
    ('IT', '', 'registered_province', 'FatturaPA Sede: Provincia of an Italian office.'),
]


def seed(apps: Any, schema_editor: Any) -> None:
    LegalFieldRequirement = apps.get_model('vendors', 'LegalFieldRequirement')
    for country, region, field_name, note in SEED:
        LegalFieldRequirement.objects.get_or_create(
            country=country,
            region=region,
            legal_form='',
            field_name=field_name,
            purpose='payout',
            defaults={'note': note, 'active': True},
        )


def unseed(apps: Any, schema_editor: Any) -> None:
    LegalFieldRequirement = apps.get_model('vendors', 'LegalFieldRequirement')
    for country, region, field_name, _ in SEED:
        LegalFieldRequirement.objects.filter(
            country=country, region=region, legal_form='', field_name=field_name, purpose='payout'
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('vendors', '0006_dac7_identity'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
