"""TAX.7 data migration: the initial dataset of territories outside the EU VAT area (spec 05 T15).

Each territory is left out of the Union's VAT territory by Article 6 of Council Directive
2006/112/EC (EUR-Lex, CELEX 32006L0112): paragraph 1 names Mount Athos (a), the Canary Islands
(b), the French territories of Articles 349 and 355(1) TFEU (c), that is the overseas
departments seeded here, and the Aland Islands (d); paragraph 2 names Buesingen, Heligoland,
Ceuta, Melilla, Livigno, Campione d'Italia and the Italian waters of Lake Lugano (no postcode).
Postcodes come from the national postal operators' public code lists (Poste Italiane, Correos,
La Poste, Posti, ELTA, Deutsche Post). Ranges are kept to what is certain, fewer territories
rather than wrong ones, for the owner to review; staff extend them in the tax admin:

- Livigno 23041 and Campione d'Italia 22061 (IT).
- Canary Islands 35* (Las Palmas) and 38* (Santa Cruz de Tenerife), Ceuta 51*, Melilla 52* (ES).
- Guadeloupe 971*, Martinique 972*, French Guiana 973*, Reunion 974*, Mayotte 976* (FR): the
  first three digits are the INSEE department code.
- Aland 22* (FI): 22xxx is used by Aland only. Addresses written with country AX count as FI
  (`engine.TERRITORY_COUNTRY_ALIASES`).
- Mount Athos 63086 (GR, Karyes), Heligoland 27498 and Buesingen am Hochrhein 78266 (DE).

Northern Ireland (XI, `BT*`) is not seeded: its status is the open decision D-02.

Seeding is idempotent and never overwrites a row staff changed: an existing code is left alone.
The reverse deletes only the seeded rows that still have no tax rate.
"""

from typing import Any

from django.db import migrations

OUTSIDE_VAT_AREA = "outside_vat_area"

# (code, name, country, postcode patterns); frozen copy of the dataset at the time of TAX.7.
SEED = [
    ("IT-LIVIGNO", "Livigno", "IT", ["23041"]),
    ("IT-CAMPIONE", "Campione d'Italia", "IT", ["22061"]),
    ("ES-CN", "Canary Islands", "ES", ["35*", "38*"]),
    ("ES-CE", "Ceuta", "ES", ["51*"]),
    ("ES-ML", "Melilla", "ES", ["52*"]),
    ("FR-971", "Guadeloupe", "FR", ["971*"]),
    ("FR-972", "Martinique", "FR", ["972*"]),
    ("FR-973", "French Guiana", "FR", ["973*"]),
    ("FR-974", "Réunion", "FR", ["974*"]),
    ("FR-976", "Mayotte", "FR", ["976*"]),
    ("FI-01", "Åland", "FI", ["22*"]),
    ("GR-69", "Mount Athos", "GR", ["63086"]),
    ("DE-HELGOLAND", "Heligoland", "DE", ["27498"]),
    ("DE-BUSINGEN", "Büsingen am Hochrhein", "DE", ["78266"]),
]


def seed(apps: Any, schema_editor: Any) -> None:
    Territory = apps.get_model("tax", "Territory")
    for code, name, country, patterns in SEED:
        Territory.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "country": country,
                "vat_status": OUTSIDE_VAT_AREA,
                "postcode_patterns": patterns,
            },
        )


def unseed(apps: Any, schema_editor: Any) -> None:
    Territory = apps.get_model("tax", "Territory")
    Territory.objects.filter(code__in=[row[0] for row in SEED], rates__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tax", "0005_exemption_law_country"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
