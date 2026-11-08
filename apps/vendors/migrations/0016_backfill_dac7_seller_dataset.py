"""VEN.23: fill the new DAC7 fields from what existing vendors already hold.

Without it every vendor that could be paid before 0014 would be blocked from payouts until
staff completed it behind the step-up. Only values that follow from stored data are filled:
- the TIN issuing country is the vendor's country, where the stored TIN is valid there;
- an Italian entity's business registration number is its REA registration ("RM-1234567").
Everything else DAC7 adds (a date of birth, a TIN of another country...) is left to staff. The
reverse step keeps the values: they are valid data, not a schema change.
"""

from typing import Any

from django.db import migrations

from apps.vendors.engine import is_valid_tin

SOLE_PROPRIETORSHIP = "sole_proprietorship"


def backfill(apps: Any, schema_editor: Any) -> None:
    Vendor = apps.get_model("vendors", "Vendor")
    for vendor in Vendor.objects.all().iterator():
        changed = []
        if (
            not vendor.tin_country
            and vendor.tax_code
            and vendor.country
            and is_valid_tin(vendor.tax_code, vendor.country)
        ):
            vendor.tin_country = vendor.country
            changed.append("tin_country")
        if (
            not vendor.business_registration_number
            and vendor.country == "IT"
            and vendor.legal_form
            and vendor.legal_form != SOLE_PROPRIETORSHIP
            and vendor.rea_office
            and vendor.rea_number
        ):
            vendor.business_registration_number = f"{vendor.rea_office}-{vendor.rea_number}"
            changed.append("business_registration_number")
        if changed:
            vendor.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = [
        ("vendors", "0015_seed_dac7_seller_dataset"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
