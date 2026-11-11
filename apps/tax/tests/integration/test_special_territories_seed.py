"""TAX.7: the initial dataset of special territories (spec 05 §5.2, case T15)."""

import pytest

from apps.tax import engine, services
from apps.tax.models import Territory

pytestmark = [pytest.mark.django_db, pytest.mark.keep_seeded_territories]

# code -> (country, postcode patterns) of the spec's list (05 §5.2 `Territory`).
EXPECTED = {
    "IT-LIVIGNO": ("IT", ["23041"]),
    "IT-CAMPIONE": ("IT", ["22061"]),
    "ES-CN": ("ES", ["35*", "38*"]),
    "ES-CE": ("ES", ["51*"]),
    "ES-ML": ("ES", ["52*"]),
    "FR-971": ("FR", ["971*"]),
    "FR-972": ("FR", ["972*"]),
    "FR-973": ("FR", ["973*"]),
    "FR-974": ("FR", ["974*"]),
    "FR-976": ("FR", ["976*"]),
    "FR-MF": ("FR", ["97150"]),
    "FR-BL": ("FR", ["97133"]),
    "FI-01": ("FI", ["22*"]),
    "GR-69": ("GR", ["63086"]),
    "DE-HELGOLAND": ("DE", ["27498"]),
    "DE-BUSINGEN": ("DE", ["78266"]),
}
# D-28: Madeira and the Azores are INSIDE the VAT area, with rates of their own.
OWN_RATES_EXPECTED = {
    "PT-MA": ("PT", ["9000..9499"]),
    "PT-AZ": ("PT", ["9500..9999"]),
}


def test_the_dataset_holds_the_territories_of_the_spec_outside_the_vat_area() -> None:
    seeded = {t.code: t for t in Territory.objects.filter(vat_status=engine.OUTSIDE_VAT_AREA)}
    assert {code: (t.country, t.postcode_patterns) for code, t in seeded.items()} == EXPECTED


def test_madeira_and_the_azores_are_inside_the_vat_area_with_their_own_rates() -> None:
    seeded = {t.code: t for t in Territory.objects.filter(vat_status=engine.OWN_RATES)}
    assert {c: (t.country, t.postcode_patterns) for c, t in seeded.items()} == OWN_RATES_EXPECTED
    assert services.current_rate("PT", "standard", territory=seeded["PT-MA"]).rate == 22
    assert services.current_rate("PT", "standard", territory=seeded["PT-AZ"]).rate == 16


@pytest.mark.parametrize(
    ("postcode", "code"),
    [("9000-050", "PT-MA"), ("9300-001", "PT-MA"), ("9500-101", "PT-AZ"), ("9980-001", "PT-AZ")],
)
def test_madeira_and_azores_postcodes_are_territories_but_not_outside_the_vat_area(
    postcode: str, code: str
) -> None:
    found = services.find_territory("PT", postcode)
    assert found is not None
    assert found.code == code
    assert not engine.is_outside_vat_territory("PT", postcode, services.territory_rules())


@pytest.mark.parametrize("postcode", ["1000-001", "4000-001", "8000-001"])
def test_mainland_portugal_is_in_no_territory(postcode: str) -> None:
    assert services.find_territory("PT", postcode) is None


@pytest.mark.parametrize(("country", "postcode"), [("ES", "35001"), ("ES", "38001")])
def test_the_canary_islands_stay_outside_the_vat_area(country: str, postcode: str) -> None:
    assert engine.is_outside_vat_territory(country, postcode, services.territory_rules())


def test_northern_ireland_is_not_seeded_while_d02_is_open() -> None:
    assert not Territory.objects.filter(code="XI").exists()


@pytest.mark.parametrize(
    ("country", "postcode"),
    [
        ("IT", "23041"),  # Livigno
        ("IT", "22061"),  # Campione d'Italia
        ("ES", "35001"),  # Las Palmas
        ("ES", "38001"),  # Santa Cruz de Tenerife
        ("ES", "51001"),
        ("ES", "52001"),
        ("FI", "22100"),  # Mariehamn
        ("AX", "22100"),
        ("FR", "97110"),  # Guadeloupe
        ("GP", "97100"),
        ("GP", ""),
        ("RE", "00000"),
        ("AX", ""),
        ("FR", "97150"),  # Saint-Martin
        ("FR", "97133"),  # Saint-Barthelemy
        ("MF", ""),
        ("BL", "00000"),
        ("FR", "97600"),  # Mayotte
        ("GR", "63086"),
        ("DE", "27498"),
        ("DE", "78266"),
    ],
)
def test_t15_seeded_territories_are_outside_the_vat_territory(country: str, postcode: str) -> None:
    assert services.find_territory(country, postcode) is not None
    assert engine.is_outside_vat_territory(country, postcode, services.territory_rules())


@pytest.mark.parametrize(
    ("country", "postcode"),
    [
        ("IT", "35100"),
        ("IT", "23100"),
        ("ES", "28001"),
        ("FR", "75001"),
        ("FR", "20000"),
        ("FI", "00100"),
        ("GR", "10431"),
        ("DE", "10115"),
        ("DE", "78267"),
    ],
)
def test_mainland_addresses_are_in_no_seeded_territory(country: str, postcode: str) -> None:
    assert services.find_territory(country, postcode) is None


def test_the_seed_is_idempotent_and_keeps_staff_edits() -> None:
    from importlib import import_module

    from django.apps import apps

    migration = import_module("apps.tax.migrations.0006_seed_special_territories")
    Territory.objects.filter(code="ES-CN").update(postcode_patterns=["35001"])
    migration.seed(apps, None)
    assert Territory.objects.count() == len(EXPECTED) + len(OWN_RATES_EXPECTED)
    assert Territory.objects.get(code="ES-CN").postcode_patterns == ["35001"]


def test_the_reverse_deletes_only_rows_still_equal_to_the_seed() -> None:
    from importlib import import_module

    from django.apps import apps

    migration = import_module("apps.tax.migrations.0006_seed_special_territories")
    Territory.objects.filter(code="ES-CN").update(postcode_patterns=["35001"])
    migration.unseed(apps, None)
    assert set(Territory.objects.values_list("code", flat=True)) == {"ES-CN", *OWN_RATES_EXPECTED}


def test_the_madeira_azores_seed_is_idempotent_and_reversible() -> None:
    from importlib import import_module

    from django.apps import apps

    from apps.tax.models import TaxRate

    migration = import_module("apps.tax.migrations.0009_seed_madeira_azores")
    migration.seed(apps, None)
    assert TaxRate.objects.filter(territory__isnull=False).count() == 2
    migration.unseed(apps, None)
    assert not Territory.objects.filter(vat_status=engine.OWN_RATES).exists()
    assert not TaxRate.objects.exists()


@pytest.mark.parametrize(
    ("country", "postcode", "code"),
    [
        ("FR", "97150", "FR-MF"),
        ("FR", "97133", "FR-BL"),
        ("FR", "97100", "FR-971"),
        ("MF", "", "FR-MF"),
        ("BL", "", "FR-BL"),
    ],
)
def test_the_seeded_territory_found_is_the_most_specific(
    country: str, postcode: str, code: str
) -> None:
    territory = services.find_territory(country, postcode)
    assert territory is not None
    assert territory.code == code


def test_the_madeira_azores_reverse_keeps_a_rate_staff_entered_later() -> None:
    import datetime as dt
    from decimal import Decimal
    from importlib import import_module

    from django.apps import apps

    from apps.tax.models import TaxRate

    migration = import_module("apps.tax.migrations.0009_seed_madeira_azores")
    madeira = Territory.objects.get(code="PT-MA")
    seeded = TaxRate.objects.get(territory=madeira, category="standard")
    TaxRate.objects.filter(pk=seeded.pk).update(valid_to=dt.datetime(2027, 1, 1, tzinfo=dt.UTC))
    TaxRate.objects.create(
        country="PT",
        territory=madeira,
        category="standard",
        rate=Decimal("22.00"),
        source="manual",
        valid_from=dt.datetime(2027, 1, 1, tzinfo=dt.UTC),
    )
    migration.unseed(apps, None)
    assert TaxRate.objects.filter(territory=madeira, valid_from__year=2027).exists()
    assert Territory.objects.filter(code="PT-MA").exists()
