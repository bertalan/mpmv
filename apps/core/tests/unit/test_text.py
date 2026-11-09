"""Pure text helpers (`apps.core.text`): ASCII integers from untrusted request text."""

import pytest

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
