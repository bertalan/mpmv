"""CAT.13: the vendor SKU is a free-text label (spec 03 §3.2, owner decisions Q-30 and Q-33).

The raw SKU is kept as entered; a match key (NFKC, trimmed, inner whitespace collapsed,
case-folded) decides uniqueness and matching. Odd characters warn, never block.
"""

import pytest

from apps.catalog import engine


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("BAG-100", "bag-100"),
        (" BAG-100 ", "bag-100"),  # SKU-TRIM
        ("AB 100", "ab 100"),  # SKU-SPACE
        ("ab  100 ", "ab 100"),
        ("AB\t\n100", "ab 100"),
        ("Bag-100", "bag-100"),  # SKU-CASE
        ("BAG/100", "bag/100"),  # SKU-SLASH
        ("\uff22\uff21\uff27\uff0d\uff11\uff10\uff10", "bag-100"),  # full-width forms (NFKC)
        ("STRASSE", "strasse"),
        ("Stra\u00dfe", "strasse"),  # case folding, not lower()
        ("\ufb01t-1", "fit-1"),  # ligature (NFKC)
    ],
)
def test_the_match_key_is_normalised_trimmed_collapsed_and_case_folded(raw: str, key: str) -> None:
    assert engine.sku_match_key(raw) == key


def test_the_match_key_never_raises() -> None:
    assert engine.sku_match_key("   ") == ""


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_a_sku_that_is_empty_once_normalised_is_an_error(raw: str) -> None:
    assert engine.sku_errors(raw)


def test_a_match_key_longer_than_the_field_is_an_error() -> None:
    assert engine.sku_errors("X" * (engine.SKU_MAX_LENGTH + 1))
    # One character that NFKC expands past the limit.
    assert engine.sku_errors("A" * (engine.SKU_MAX_LENGTH - 1) + "ﷺ")


@pytest.mark.parametrize("raw", ["BAG-100", " BAG-100 ", "AB 100", "BAG/100", "ab  100 "])
def test_odd_skus_are_not_errors(raw: str) -> None:
    assert engine.sku_errors(raw) == []


@pytest.mark.parametrize("raw", ["BAG-100", "AB 100", "A.B_C-1", "x9"])
def test_plain_skus_have_no_warnings(raw: str) -> None:
    assert engine.sku_warnings(raw) == []


@pytest.mark.parametrize(
    "raw",
    [
        " BAG-100 ",  # SKU-TRIM: surrounding spaces
        "BAG-100 ",
        "BAG/100",  # SKU-SLASH
        "BAG\\100",
        'BAG"100',
        "BAG'100",
        "BAG\u2019100",  # typographic quote
        "AB  100",  # repeated inner spaces
        "AB\t100",  # tab
        "BAG\u200b100",  # zero-width space
        "BAG\x07100",  # control character
        "\u0412\u0410G-100",  # Cyrillic look-alike letters
        "\uff22\uff21\uff27-100",  # full-width look-alikes
    ],
)
def test_troublesome_characters_raise_a_warning(raw: str) -> None:
    warnings = engine.sku_warnings(raw)
    assert warnings
    assert all(isinstance(w, str) and w for w in warnings)
