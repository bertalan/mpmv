"""VEN.12: the initial legal field requirements for payouts (owner decision).

Staff edit them in the admin afterwards; this migration only seeds them. Rules are additive.
- Italy, every legal form: PEC, codice fiscale, fiscal regime and the delivery channel (a
  7-character SDI code, or 0000000 with a PEC: the rule on `sdi_recipient_code`).
- Italy, companies (not sole proprietors): REA office, REA number and liquidation status.
- Every EU member state, Italy included: VAT number and IBAN.
"""

from typing import Any

from django.db import migrations

# Frozen copy of the member states at the time of this migration.
EU_MEMBER_STATES = (
    'AT BE BG CY CZ DE DK EE ES FI FR GR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK'.split()
)
ITALIAN_COMPANY_FORMS = ('partnership', 'srl', 'spa', 'cooperative')

SEED = [
    ('IT', '', 'pec', 'Certified email for e-invoices and legal notices.'),
    ('IT', '', 'tax_code', 'Codice fiscale on invoices and DAC7.'),
    ('IT', '', 'fiscal_regime', 'FatturaPA RegimeFiscale.'),
    ('IT', '', 'sdi_recipient_code', 'Delivery channel: 7-character SDI code, or 0000000 + PEC.'),
    *(
        ('IT', form, field, 'FatturaPA IscrizioneREA for companies.')
        for form in ITALIAN_COMPANY_FORMS
        for field in ('rea_office', 'rea_number', 'liquidation_status')
    ),
    *(
        (country, '', field, note)
        for country in EU_MEMBER_STATES
        for field, note in (
            ('vat_number', 'EU vendors: VAT identification (DAC7, invoices).'),
            ('iban', 'EU vendors: account for payouts (DAC7).'),
        )
    ),
]


def seed(apps: Any, schema_editor: Any) -> None:
    LegalFieldRequirement = apps.get_model('vendors', 'LegalFieldRequirement')
    for country, legal_form, field_name, note in SEED:
        LegalFieldRequirement.objects.get_or_create(
            country=country,
            legal_form=legal_form,
            field_name=field_name,
            purpose='payout',
            defaults={'note': note, 'active': True},
        )


def unseed(apps: Any, schema_editor: Any) -> None:
    LegalFieldRequirement = apps.get_model('vendors', 'LegalFieldRequirement')
    for country, legal_form, field_name, _ in SEED:
        LegalFieldRequirement.objects.filter(
            country=country, legal_form=legal_form, field_name=field_name, purpose='payout'
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('vendors', '0004_legal_identity'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
