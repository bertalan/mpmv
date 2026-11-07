"""TAX.11: the pattern rewrite of the territory postcode migration is idempotent and reversible."""

import importlib
from types import ModuleType

import pytest
from django.db.migrations.exceptions import IrreversibleError


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


def test_a_reversed_old_range_migrates_with_its_bounds_in_order(migration: ModuleType) -> None:
    # The old grammar accepted 22199-22100; the shared grammar refuses a low bound above the high.
    assert migration.upgrade("22199-22100") == "22100..22199"
    assert migration.upgrade(" 22199-22100 ") == "22100..22199"


@pytest.mark.parametrize(
    "pattern",
    ["BT1..BT9", "1000-001..1999-999", "AB-1*"],
)
def test_the_reverse_step_refuses_patterns_the_old_grammar_refuses(
    migration: ModuleType, pattern: str
) -> None:
    with pytest.raises(IrreversibleError, match="old grammar"):
        migration.downgrade(pattern)
