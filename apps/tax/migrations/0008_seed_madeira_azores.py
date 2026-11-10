"""D-28 / CMP.28: Madeira and the Azores are inside the EU VAT area, with rates of their own.

Directive 2006/112/EC art. 6 leaves them in the Union VAT territory (only the Canary Islands,
seeded outside by 0006, are out). Both are territories of PT with the `own_rates` status and
the standard rate of the Autonomous Regions (Madeira 22%, Azores 16%); other categories are
for staff to add in the tax admin. Postcodes: Madeira 9000..9499 (incl. Porto Santo), Azores
9500..9999 (CTT code ranges). Needs human review. Idempotent; never overwrites a staff row.
The reverse deletes only the rows that still equal the seed.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

from django.db import migrations

OWN_RATES = "own_rates"
VALID_FROM = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

# (code, name, postcode patterns, standard rate)
SEED = [
    ("PT-MA", "Madeira", ["9000..9499"], Decimal("22.00")),
    ("PT-AZ", "Azores", ["9500..9999"], Decimal("16.00")),
]


def seed(apps: Any, schema_editor: Any) -> None:
    Territory = apps.get_model("tax", "Territory")
    TaxRate = apps.get_model("tax", "TaxRate")
    for code, name, patterns, standard in SEED:
        territory, _ = Territory.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "country": "PT",
                "vat_status": OWN_RATES,
                "postcode_patterns": patterns,
            },
        )
        if not TaxRate.objects.filter(territory=territory, category="standard").exists():
            TaxRate.objects.create(
                country="PT",
                territory=territory,
                category="standard",
                rate=standard,
                source="manual",
                valid_from=VALID_FROM,
            )


def unseed(apps: Any, schema_editor: Any) -> None:
    Territory = apps.get_model("tax", "Territory")
    TaxRate = apps.get_model("tax", "TaxRate")
    for code, name, patterns, standard in SEED:
        TaxRate.objects.filter(
            territory__code=code, category="standard", rate=standard, source="manual"
        ).delete()
        Territory.objects.filter(
            code=code,
            name=name,
            country="PT",
            vat_status=OWN_RATES,
            postcode_patterns=patterns,
            rates__isnull=True,
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tax", "0007_vatvalidation_requester"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
