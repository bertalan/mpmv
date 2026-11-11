"""CAT.20 (1/3): the pure brand rules: name key for duplicate detection, Wikidata item ID by
format only, absolute https links."""

import pytest

from apps.catalog.engine import (
    brand_name_key,
    https_url_error,
    same_as_errors,
    wikidata_id_error,
)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Acme", "  acme "),
        ("Acme & Sons", "ACME and sons"),
        ("Café Noir", "cafe-noir"),
        ("A.C.M.E.", "acme"),
        ("\uff22rand", "brand"),
    ],
)
def test_name_key_matches_spelling_variants(a: str, b: str) -> None:
    assert brand_name_key(a) == brand_name_key(b)


def test_name_key_keeps_different_brands_apart() -> None:
    assert brand_name_key("Acme") != brand_name_key("Acne")
    assert brand_name_key("") == ""


@pytest.mark.parametrize("value", ["", "Q1", "Q42", "Q123456789"])
def test_wikidata_id_accepts_the_item_format(value: str) -> None:
    assert wikidata_id_error(value) is None


@pytest.mark.parametrize("value", ["q42", "Q", "Q0", "Q01", "P31", "Q12a", " Q42", "Q" + "9" * 12])
def test_wikidata_id_refuses_other_shapes(value: str) -> None:
    assert wikidata_id_error(value)


def test_https_url_accepts_only_absolute_https() -> None:
    assert https_url_error("https://example.com/brand") is None
    for bad in (
        "http://example.com",
        "//example.com",
        "/relative",
        "javascript:alert(1)",
        "https://",
        "https://user:pw@example.com/",
        "https://exa mple.com",
        "ftp://example.com",
    ):
        assert https_url_error(bad), bad


def test_same_as_errors_name_the_bad_entries_and_refuse_duplicates() -> None:
    assert (
        same_as_errors(["https://www.wikidata.org/wiki/Q42", "https://it.wikipedia.org/wiki/X"])
        == []
    )
    errors = same_as_errors(["https://a.example/x", "http://b.example", "https://a.example/x"])
    assert len(errors) == 2
