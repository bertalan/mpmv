"""SHP.14: the special areas of the standard shipping template.

Postcode and province lists from the public postal codes of each country. Where an exact list is
uncertain the seed keeps a conservative subset (fewer areas rather than wrong ones), for the
owner to review: staff extend the lists in the admin or import a carrier's remote-area list as a
platform file (both audited).

- Islands: Sardinia and Sicily (whole postcode ranges and their provinces), the Italian minor
  islands outside them (Bay of Naples, Tuscan and Pontine islands), the Balearic Islands,
  Corsica, Crete and the Aegean islands, Madeira (with Porto Santo) and the Azores.
- Remote: the Venice lagoon (the historic centre, Giudecca, Lido, Murano, Burano) and the
  Highlands and Islands postcode areas the grammar can express without the space of a UK
  postcode (HS, ZE).
- Extra-VAT territories, each naming its TAX.1 territory code: Livigno, Campione d'Italia, the
  Canary Islands, Ceuta, Melilla, the French overseas departments, Åland, Mount Athos,
  Heligoland and Büsingen. A territory that has an ISO code of its own also lists it as a
  country, so an address written either way matches.

Seeding is idempotent: existing areas are left alone.
"""

from typing import Any

from django.db import migrations

ISLAND = 'island'
REMOTE = 'remote'
EXTRA_VAT = 'extra_vat'

# (code, name, kind, countries, postcode patterns, provinces, territory code); frozen copies.
SEED: list[tuple[str, str, str, list[str], list[str], list[str], str]] = [
    (
        'it-sardinia',
        'Sardinia',
        ISLAND,
        ['IT'],
        ['07*', '08*', '09*'],
        ['CA', 'NU', 'OR', 'SS', 'SU'],
        '',
    ),
    (
        'it-sicily',
        'Sicily',
        ISLAND,
        ['IT'],
        ['90000..98999'],
        ['AG', 'CL', 'CT', 'EN', 'ME', 'PA', 'RG', 'SR', 'TP'],
        '',
    ),
    (
        'it-minor-islands',
        'Italian minor islands',
        ISLAND,
        ['IT'],
        [
            '80071',  # Anacapri
            '80073',  # Capri
            '80074',  # Casamicciola Terme (Ischia)
            '80075',  # Forio (Ischia)
            '80076',  # Lacco Ameno (Ischia)
            '80077',  # Ischia
            '80079',  # Procida
            '57031',  # Capoliveri (Elba)
            '57032',  # Capraia Isola
            '57033',  # Marciana Marina (Elba)
            '57034',  # Campo nell'Elba
            '57036',  # Porto Azzurro (Elba)
            '57037',  # Portoferraio (Elba)
            '57038',  # Rio Marina (Elba)
            '57039',  # Rio nell'Elba
            '58012',  # Isola del Giglio
            '04027',  # Ponza
            '04031',  # Ventotene
        ],
        [],
        '',
    ),
    (
        'it-venice-lagoon',
        'Venice lagoon',
        REMOTE,
        ['IT'],
        [
            '30121',
            '30122',
            '30123',
            '30124',
            '30125',  # historic centre
            '30126',  # Lido
            '30133',  # Giudecca
            '30135',  # Santa Croce
            '30141',  # Murano
            '30142',  # Burano
        ],
        [],
        '',
    ),
    ('es-balearic-islands', 'Balearic Islands', ISLAND, ['ES'], ['07*'], [], ''),
    ('fr-corsica', 'Corsica', ISLAND, ['FR'], ['20*'], [], ''),
    ('gr-crete', 'Crete', ISLAND, ['GR'], ['70..74'], [], ''),
    (
        'gr-aegean-islands',
        'Aegean islands (North Aegean, Cyclades, Dodecanese)',
        ISLAND,
        ['GR'],
        ['81..85'],
        [],
        '',
    ),
    ('pt-madeira', 'Madeira and Porto Santo', ISLAND, ['PT'], ['9000..9499'], [], ''),
    ('pt-azores', 'Azores', ISLAND, ['PT'], ['9500..9999'], [], ''),
    (
        'gb-highlands-islands',
        'UK Highlands and Islands (Outer Hebrides, Shetland)',
        REMOTE,
        ['GB'],
        ['HS*', 'ZE*'],
        [],
        '',
    ),
    ('it-livigno', 'Livigno', EXTRA_VAT, ['IT'], ['23041'], [], 'IT-LIVIGNO'),
    ('it-campione', "Campione d'Italia", EXTRA_VAT, ['IT'], ['22061'], [], 'IT-CAMPIONE'),
    ('es-canary-islands', 'Canary Islands', EXTRA_VAT, ['ES'], ['35*', '38*'], [], 'ES-CN'),
    ('es-ceuta', 'Ceuta', EXTRA_VAT, ['ES'], ['51*'], [], 'ES-CE'),
    ('es-melilla', 'Melilla', EXTRA_VAT, ['ES'], ['52*'], [], 'ES-ML'),
    ('fr-guadeloupe', 'Guadeloupe', EXTRA_VAT, ['FR', 'GP'], ['971*'], [], 'FR-971'),
    ('fr-martinique', 'Martinique', EXTRA_VAT, ['FR', 'MQ'], ['972*'], [], 'FR-972'),
    ('fr-guiana', 'French Guiana', EXTRA_VAT, ['FR', 'GF'], ['973*'], [], 'FR-973'),
    ('fr-reunion', 'Réunion', EXTRA_VAT, ['FR', 'RE'], ['974*'], [], 'FR-974'),
    ('fr-mayotte', 'Mayotte', EXTRA_VAT, ['FR', 'YT'], ['976*'], [], 'FR-976'),
    ('fi-aland', 'Åland', EXTRA_VAT, ['FI', 'AX'], ['22*', 'AX-22*'], [], 'FI-01'),
    ('gr-mount-athos', 'Mount Athos', EXTRA_VAT, ['GR'], ['63086'], [], 'GR-69'),
    ('de-heligoland', 'Heligoland', EXTRA_VAT, ['DE'], ['27498'], [], 'DE-HELGOLAND'),
    ('de-busingen', 'Büsingen am Hochrhein', EXTRA_VAT, ['DE'], ['78266'], [], 'DE-BUSINGEN'),
]


def seed(apps: Any, schema_editor: Any) -> None:
    SpecialArea = apps.get_model('shipping', 'SpecialArea')
    for code, name, kind, countries, patterns, states, territory in SEED:
        SpecialArea.objects.get_or_create(
            code=code,
            defaults={
                'name': name,
                'kind': kind,
                'countries': countries,
                'postcode_patterns': patterns,
                'states': states,
                'territory_code': territory,
            },
        )


def unseed(apps: Any, schema_editor: Any) -> None:
    SpecialArea = apps.get_model('shipping', 'SpecialArea')
    SpecialArea.objects.filter(code__in=[code for code, *_ in SEED], treatments=None).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('shipping', '0008_special_areas'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
