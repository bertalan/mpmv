"""TAX.7: the initial dataset of special territories (spec 05 §5.2, case T15)."""

import pytest

from apps.tax import engine, services
from apps.tax.models import Territory

pytestmark = pytest.mark.django_db

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
    "FI-01": ("FI", ["22*"]),
    "GR-69": ("GR", ["63086"]),
    "DE-HELGOLAND": ("DE", ["27498"]),
    "DE-BUSINGEN": ("DE", ["78266"]),
}


def test_the_dataset_holds_the_territories_of_the_spec_outside_the_vat_area() -> None:
    seeded = {t.code: t for t in Territory.objects.all()}
    assert {code: (t.country, t.postcode_patterns) for code, t in seeded.items()} == EXPECTED
    assert {t.vat_status for t in seeded.values()} == {engine.OUTSIDE_VAT_AREA}


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
