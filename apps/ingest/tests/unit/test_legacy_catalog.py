"""MIG.3 part 1: the pure rules of the catalog loader."""

from decimal import Decimal

import pytest

from apps.ingest import legacy_catalog as rules


def test_money_is_parsed_with_decimal_and_rounded_half_up() -> None:
    assert rules.parse_money("12.345") == Decimal("12.35")
    assert rules.parse_money(" 7 ") == Decimal("7.00")
    assert rules.parse_money("") is None
    assert rules.parse_money(None) is None


@pytest.mark.parametrize("raw", ["abc", "1,50 EUR", "-3", "NaN", "Infinity", 1.5])
def test_malformed_money_is_rejected(raw: object) -> None:
    with pytest.raises(rules.BadValue):
        rules.parse_money(raw)


def test_stock_quantity_never_goes_below_zero() -> None:
    assert rules.parse_quantity("-4") == 0
    assert rules.parse_quantity("12") == 12
    assert rules.parse_quantity(None) == 0


@pytest.mark.parametrize(
    ("status", "include", "expected"),
    [
        ("publish", False, rules.LOAD_PUBLISHED),
        ("draft", False, rules.SKIP_UNPUBLISHED),
        ("archived", False, rules.SKIP_UNPUBLISHED),
        ("draft", True, rules.LOAD_HIDDEN),
        ("archived", True, rules.LOAD_HIDDEN),
        ("trash", True, rules.SKIP_TRASH),
        ("trash", False, rules.SKIP_TRASH),
        ("weird", True, rules.SKIP_UNKNOWN),
    ],
)
def test_load_decision_follows_the_vendor_choice(status: str, include: bool, expected: str) -> None:
    assert rules.load_decision(status, include_unpublished=include) == expected


def test_group_key_includes_vendor_and_translation_group() -> None:
    assert rules.group_key(1, "a-1", 10) != rules.group_key(2, "a-1", 10)
    assert rules.group_key(1, "a-1", 10) != rules.group_key(1, "a-1", 11)


def test_missing_languages_lists_the_site_languages_without_a_text() -> None:
    assert rules.missing_languages(["en"], ["en", "it"], "en") == ["it"]
    assert rules.missing_languages(["en", "it"], ["en", "it"], "en") == []
