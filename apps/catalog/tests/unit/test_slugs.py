"""CAT.5: SKU-free product slugs and product paths (spec 03 §3.2, owner decisions Q-30, Q-33).

The cases are the legacy permalink rules (transliteration, 80-character limit with a truncated
title, `-2` on collisions) without the SKU part: a slug is `{title}-{lang}` and never carries
the SKU.
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
    ("title", "lang", "expected"),
    [
        ("Leather Bag", "en", "leather-bag-en"),
        ("Borsa in pelle è più bella", "it", "borsa-in-pelle-e-piu-bella-it"),
        ("Große Tasche", "de", "grosse-tasche-de"),
        ("Smørrebrød Æble Œuvre", "fr", "smorrebrod-aeble-oeuvre-fr"),
        ("Łódź bag", "en", "lodz-bag-en"),
        ("Bag 100% cotton & leather!", "en", "bag-100-cotton-leather-en"),
        ("Women's  bag -- large", "en", "womens-bag-large-en"),
        ("  Trimmed title  ", "es", "trimmed-title-es"),
        ("Bag", "EN", "bag-en"),
    ],
)
def test_slug_is_the_transliterated_title_and_the_language(
    title: str, lang: str, expected: str
) -> None:
    assert build_product_slug(title, lang) == expected


def test_the_slug_function_takes_no_sku() -> None:
    assert "sku" not in inspect.signature(build_product_slug).parameters


@pytest.mark.parametrize("title", ["", "   ", "!!! ???", "★☆"])
def test_a_title_without_letters_or_digits_gets_a_fallback(title: str) -> None:
    assert build_product_slug(title, "it") == "product-it"


def test_a_long_title_is_truncated_to_80_characters_keeping_the_language() -> None:
    slug = build_product_slug("a" * 100, "en")
    assert slug == "a" * 77 + "-en"
    assert len(slug) == 80


def test_truncation_never_leaves_a_dash_before_the_language() -> None:
    title = "x" * 76 + " tail of the title"
    slug = build_product_slug(title, "en")
    assert slug == "x" * 76 + "-en"
    assert "--" not in slug


def test_max_len_is_configurable() -> None:
    assert build_product_slug("Leather travel bag", "en", max_len=12) == "leather-t-en"


def test_max_len_must_leave_room_for_the_title() -> None:
    with pytest.raises(ValueError, match="max_len"):
        build_product_slug("Bag", "en", max_len=3)


def test_unique_slug_without_collision_is_the_plain_slug() -> None:
    assert unique_product_slug("Leather Bag", "en", taken=set()) == "leather-bag-en"


def test_a_collision_gets_a_numeric_suffix() -> None:
    taken = {"leather-bag-en"}
    assert unique_product_slug("Leather Bag", "en", taken=taken) == "leather-bag-en-2"
    taken.add("leather-bag-en-2")
    assert unique_product_slug("Leather Bag", "en", taken=taken) == "leather-bag-en-3"


def test_the_suffix_keeps_the_slug_within_max_len() -> None:
    plain = build_product_slug("b" * 100, "en")
    slug = unique_product_slug("b" * 100, "en", taken={plain})
    assert slug == "b" * 75 + "-en-2"
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
