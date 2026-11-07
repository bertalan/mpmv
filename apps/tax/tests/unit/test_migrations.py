"""TAX.11: the pattern rewrite of the territory postcode migration is idempotent and reversible."""

import importlib
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def migration() -> ModuleType:
    return importlib.import_module("apps.tax.migrations.0003_territory_postcode_ranges")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("22100-22199", "22100..22199"),
        ("1000-1999", "1000..1999"),
        ("9000-001", "9000-001"),  # PT postcode: bounds of different length
        ("00-950", "00-950"),  # PL postcode
        ("1-22", "1-22"),
        ("AB1-AB9", "AB1-AB9"),  # not two numbers: an exact postcode
        ("35*", "35*"),
        ("23041", "23041"),
    ],
)
def test_the_rewrite_is_idempotent_and_reversible(
    migration: ModuleType, old: str, new: str
) -> None:
    assert migration.upgrade(old) == new
    assert migration.upgrade(new) == new  # running it twice changes nothing
    assert migration.downgrade(new) == old
    assert migration.downgrade(old) == old
