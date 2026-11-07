"""SHP.12: the initial country groups shipping zones can use.

- `eu`: the EU member states.
- The UN M49 continents and regions: Africa, the Americas (and Northern America, Central America
  and the Caribbean, South America), Asia, Europe, Oceania. Antarctica (AQ) is in none.
- `europe` is UN M49 Europe plus Cyprus (M49: Western Asia), so every EU member state is in it.

Staff edit the groups in the admin afterwards (audited); this migration only seeds them, with
published English and Italian names. Seeding is idempotent: existing groups are left alone.
"""

from typing import Any

from django.conf import settings
from django.db import migrations

from apps.core.translations import source_hash

# Frozen copies at the time of this migration.
EU_MEMBER_STATES = (
    'AT BE BG CY CZ DE DK EE ES FI FR GR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK'
)
AFRICA = (
    'DZ EG LY MA SD TN EH '  # Northern Africa
    'IO BI KM DJ ER ET TF KE MG MW MU YT MZ RE RW SC SO SS UG TZ ZM ZW '  # Eastern Africa
    'AO CM CF TD CG CD GQ GA ST '  # Middle Africa
    'BW SZ LS NA ZA '  # Southern Africa
    'BJ BF CV CI GM GH GN GW LR ML MR NE NG SH SN SL TG'  # Western Africa
)
NORTHERN_AMERICA = 'BM CA GL PM US'
CENTRAL_AMERICA_CARIBBEAN = (
    'AI AG AW BS BB BQ VG KY CU CW DM DO GD GP HT JM MQ MS PR BL KN LC MF VC SX TT TC VI '
    'BZ CR SV GT HN MX NI PA'
)
SOUTH_AMERICA = 'AR BO BV BR CL CO EC FK GF GY PY PE GS SR UY VE'
ASIA = (
    'KZ KG TJ TM UZ '  # Central Asia
    'CN HK MO KP JP MN KR TW '  # Eastern Asia
    'BN KH ID LA MY MM PH SG TH TL VN '  # South-eastern Asia
    'AF BD BT IN IR MV NP PK LK '  # Southern Asia
    'AM AZ BH CY GE IQ IL JO KW LB OM QA SA PS SY TR AE YE'  # Western Asia
)
EUROPE = (
    'BY BG CZ HU PL MD RO RU SK UA '  # Eastern Europe
    'AX DK EE FO FI GG IS IE IM JE LV LT NO SJ SE GB '  # Northern Europe
    'AL AD BA HR GI GR VA IT MT ME MK PT SM RS SI ES '  # Southern Europe
    'AT BE FR DE LI LU MC NL CH '  # Western Europe
    'CY'  # EU member state, M49 Western Asia
)
OCEANIA = (
    'AU CX CC HM NZ NF '  # Australia and New Zealand
    'FJ NC PG SB VU '  # Melanesia
    'GU KI MH FM NR MP PW UM '  # Micronesia
    'AS CK PF NU PN WS TK TO TV WF'  # Polynesia
)

SEED = [
    ('eu', EU_MEMBER_STATES, 'European Union', 'Unione europea'),
    ('europe', EUROPE, 'Europe', 'Europa'),
    ('africa', AFRICA, 'Africa', 'Africa'),
    (
        'americas',
        f'{NORTHERN_AMERICA} {CENTRAL_AMERICA_CARIBBEAN} {SOUTH_AMERICA}',
        'Americas',
        'Americhe',
    ),
    ('northern-america', NORTHERN_AMERICA, 'Northern America', 'America settentrionale'),
    (
        'central-america-caribbean',
        CENTRAL_AMERICA_CARIBBEAN,
        'Central America and the Caribbean',
        'America centrale e Caraibi',
    ),
    ('south-america', SOUTH_AMERICA, 'South America', 'America meridionale'),
    ('asia', ASIA, 'Asia', 'Asia'),
    ('oceania', OCEANIA, 'Oceania', 'Oceania'),
]


def seed(apps: Any, schema_editor: Any) -> None:
    CountryGroup = apps.get_model('shipping', 'CountryGroup')
    Translation = apps.get_model('shipping', 'CountryGroupTranslation')
    source = settings.LANGUAGE_CODE
    for code, countries, name_en, name_it in SEED:
        group, created = CountryGroup.objects.get_or_create(
            code=code, defaults={'countries': countries.split()}
        )
        if not created:
            continue
        # Historical models skip TranslationModel.save: fill the workflow fields here.
        digest = source_hash({'name': name_en})
        for language, name in ((source, name_en), ('it', name_it)):
            Translation.objects.create(
                country_group=group,
                language=language,
                name=name,
                status='published',
                source_hash=digest,
            )


def unseed(apps: Any, schema_editor: Any) -> None:
    CountryGroup = apps.get_model('shipping', 'CountryGroup')
    CountryGroup.objects.filter(code__in=[code for code, *_ in SEED]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('shipping', '0004_default_methods_and_postcode_ranges'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
