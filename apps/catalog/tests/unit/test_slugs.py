"""CAT.5: product slugs and product paths (spec 03 §3.2, owner decisions Q-30, Q-33, D-25).

The cases are the legacy permalink rules (transliteration, 80-character limit with a truncated
title, `-2` on collisions) without the SKU and without the language: a slug is the
transliterated `{title}` (the language is in the URL prefix).
"""

import inspect

import pytest

from apps.catalog.slugs import (
    build_product_slug,
    parse_product_path,
    product_path,
    unique_product_slug,
)

LANGUAGES = ("en", "it", "de", "fr", "es")


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Leather Bag", "leather-bag"),
        ("Borsa in pelle è più bella", "borsa-in-pelle-e-piu-bella"),
        ("Große Tasche", "grosse-tasche"),
        ("Smørrebrød Æble Œuvre", "smorrebrod-aeble-oeuvre"),
        ("Łódź bag", "lodz-bag"),
        ("Bag 100% cotton & leather!", "bag-100-cotton-leather"),
        ("Women's  bag -- large", "womens-bag-large"),
        ("  Trimmed title  ", "trimmed-title"),
    ],
)
def test_slug_is_the_transliterated_title_only(title: str, expected: str) -> None:
    assert build_product_slug(title) == expected


def test_the_slug_function_takes_no_sku_and_no_language_parameters() -> None:
    parameters = inspect.signature(build_product_slug).parameters
    assert "sku" not in parameters
    assert "lang" not in parameters


@pytest.mark.parametrize("title", ["", "   ", "!!! ???", "★☆"])
def test_a_title_without_letters_or_digits_gets_a_fallback(title: str) -> None:
    assert build_product_slug(title) == "product"


def test_a_long_title_is_truncated_to_80_characters() -> None:
    slug = build_product_slug("a" * 100)
    assert slug == "a" * 80


def test_truncation_never_leaves_a_trailing_dash() -> None:
    slug = build_product_slug("x" * 79 + " tail of the title")
    assert slug == "x" * 79
    slug = build_product_slug("word " * 30)
    assert len(slug) <= 80
    assert not slug.endswith("-")


def test_max_len_is_configurable() -> None:
    assert build_product_slug("Leather travel bag", max_len=9) == "leather-t"


def test_max_len_must_leave_room_for_the_title() -> None:
    with pytest.raises(ValueError, match="max_len"):
        build_product_slug("Bag", max_len=0)


def test_unique_slug_without_collision_is_the_plain_slug() -> None:
    assert unique_product_slug("Leather Bag", taken=set()) == "leather-bag"


def test_a_collision_gets_a_numeric_suffix() -> None:
    taken = {"leather-bag"}
    assert unique_product_slug("Leather Bag", taken=taken) == "leather-bag-2"
    taken.add("leather-bag-2")
    assert unique_product_slug("Leather Bag", taken=taken) == "leather-bag-3"


def test_the_suffix_keeps_the_slug_within_max_len() -> None:
    plain = build_product_slug("b" * 100)
    slug = unique_product_slug("b" * 100, taken={plain})
    assert slug == "b" * 78 + "-2"
    assert len(slug) == 80


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("en", "/product/leather-bag-en/"),
        ("it", "/it/prodotto/leather-bag-en/"),
        ("de", "/de/product/leather-bag-en/"),
    ],
)
def test_product_path_per_language(lang: str, expected: str) -> None:
    assert product_path(lang, "leather-bag-en", default_language="en") == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/product/leather-bag-en/", ("en", "leather-bag-en")),
        ("/it/prodotto/borsa-it-sku-1/", ("it", "borsa-it-sku-1")),
        ("/de/product/tasche-de/", ("de", "tasche-de")),
        ("/product/leather-bag-en", None),  # no trailing slash
        ("/product/a/b/", None),
        ("/it/product/borsa-it/", None),  # wrong segment for the language
        ("/en/product/leather-bag-en/", None),  # the default language has no prefix
        ("/xx/product/bag/", None),  # unknown language
        ("/product/Not A Slug/", None),
        ("/shop/vendor-a/", None),
    ],
)
def test_parse_product_path(path: str, expected: tuple[str, str] | None) -> None:
    assert parse_product_path(path, languages=LANGUAGES, default_language="en") == expected
