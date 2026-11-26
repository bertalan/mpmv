"""Pure text helpers (`apps.core.text`): ASCII integers from untrusted request text."""

import pytest

from apps.core import text
from apps.core.text import ascii_int


@pytest.mark.parametrize(("text", "value"), [("0", 0), ("1001", 1001), ("007", 7)])
def test_plain_ascii_digits_are_an_integer(text: str, value: int) -> None:
    assert ascii_int(text) == value


@pytest.mark.parametrize(
    "text",
    [
        "",
        "²",  # superscript two: isdigit() but int() raises
        "①",  # circled one
        "١٢",  # Arabic-Indic digits: int() accepts them, we do not
        chr(0xFF11),  # fullwidth one (written as chr: ruff RUF001)
        "1²",
        " 1",
        "1 ",
        "+1",
        "-1",
        "1_000",
        "1.0",
        "0x10",
        "1" * 19,  # longer than a bigint can hold
        "1" * 5000,  # beyond int()'s digit limit
        "1\x00",
    ],
)
def test_anything_else_is_none_never_an_exception(text: str) -> None:
    assert ascii_int(text) is None


def test_the_digit_limit_can_be_set() -> None:
    assert ascii_int("12345", max_digits=5) == 12345
    assert ascii_int("123456", max_digits=5) is None


# --- SEO.17: meta descriptions


def test_meta_description_takes_the_first_text_that_is_not_blank() -> None:
    assert (
        text.meta_description("", "  ", None, "Hand-thrown mugs.", "Later") == "Hand-thrown mugs."
    )


def test_meta_description_is_plain_text_on_one_line() -> None:
    html = "<p>Oak &amp; ash</p>\n<p>  made   <b>here</b></p>"
    assert text.meta_description(html) == "Oak & ash made here"


def test_a_long_meta_description_ends_on_a_whole_word_within_the_limit() -> None:
    source = " ".join(["handmade"] * 50)  # 449 characters
    found = text.meta_description(source)
    assert len(found) <= 160
    assert found.endswith("handmade…")
    assert found.removesuffix("…").split(" ") == ["handmade"] * len(found.split(" "))


def test_a_cut_drops_trailing_punctuation_before_the_ellipsis() -> None:
    assert text.meta_description("Alpha beta, gamma delta", limit=13) == "Alpha beta…"


def test_one_long_word_is_cut_inside_it() -> None:
    assert text.meta_description("x" * 200, limit=10) == "x" * 9 + "…"


def test_nothing_to_say_gives_an_empty_description() -> None:
    assert text.meta_description("", "<p> </p>", None) == ""
