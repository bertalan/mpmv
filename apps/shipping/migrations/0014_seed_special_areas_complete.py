"""SHP.15: the remaining special areas of the standard shipping template.

Completes the SHP.14 seed (owner decision Q-97) from the postal code lists of the carrier zone
tables in use by vendors, cross-checked with the public Greek and UK postcode structure.

- Greece, Ionian islands: 49* (Corfu and Paxoi), 28* (Kefalonia, Ithaca), 29* (Zakynthos), 31*
  (Lefkada and Meganisi) and 801* (Kythira and Antikythira). Each of these two-digit prefixes is an island-only regional unit, so
  none of them reaches mainland Greece (Patras 26*, Pyrgos 27*, Agrinio 30*, Preveza 48*).
- Greece, Sporades: 370* (Skiathos, Skopelos, Alonissos) and 34007 (Skyros).
- Greece, Saronic islands: the exact codes 18010 (Aegina, Agistri), 18020 (Poros), 18040
  (Hydra), 18050 (Spetses) and 189* (Salamina). 180* is not used as a prefix: the Methana
  peninsula (18030) is mainland by owner decision, and the pattern grammar has no exclusion.
  Piraeus and the Athens basin use 181* to 188*, which stay mainland.
- United Kingdom, Northern Ireland: BT*, a remote area priced "subject to quote" by default.
  It is a shipping area only: the tax territory logic (TAX.7, D-02) is unaffected, so the area
  carries no territory code and is not an extra-VAT kind.

Seeding is idempotent: an existing area is left alone. The reverse removes only the areas added
here, and only when no method treats them.
"""

from typing import Any

from django.db import migrations

ISLAND = 'island'
REMOTE = 'remote'

# (code, name, kind, countries, postcode patterns); frozen copies.
SEED: list[tuple[str, str, str, list[str], list[str]]] = [
    ('gr-ionian-islands', 'Ionian islands', ISLAND, ['GR'], ['49*', '28*', '29*', '31*', '801*']),
    ('gr-sporades', 'Sporades (Skiathos, Skopelos, Alonissos)', ISLAND, ['GR'], ['370*', '34007']),
    ('gr-saronic-islands', 'Saronic islands', ISLAND, ['GR'], ['18010', '18020', '18040', '18050', '189*']),
    ('gb-northern-ireland', 'Northern Ireland', REMOTE, ['GB'], ['BT*']),
]


def seed(apps: Any, schema_editor: Any) -> None:
    SpecialArea = apps.get_model('shipping', 'SpecialArea')
    for code, name, kind, countries, patterns in SEED:
        SpecialArea.objects.get_or_create(
            code=code,
            defaults={
                'name': name,
                'kind': kind,
                'countries': countries,
                'postcode_patterns': patterns,
                'states': [],
                'territory_code': '',
            },
        )


def unseed(apps: Any, schema_editor: Any) -> None:
    SpecialArea = apps.get_model('shipping', 'SpecialArea')
    SpecialArea.objects.filter(code__in=[code for code, *_ in SEED], treatments=None).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('shipping', '0013_shipping_vat_follows_goods_schema'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
