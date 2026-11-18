"""CMS.15: the file size and type shown before a download (pure rules, no database)."""

from decimal import Decimal

import pytest

from apps.cms.engine import file_size_parts, file_type_label


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, (Decimal(0), "B")),
        (512, (Decimal(512), "B")),
        (999, (Decimal(999), "B")),
        (1_000, (Decimal("1.0"), "KB")),
        (1_234, (Decimal("1.2"), "KB")),
        (1_200_000, (Decimal("1.2"), "MB")),
        (10_450_000, (Decimal("10.5"), "MB")),
        (3_000_000_000, (Decimal("3.0"), "GB")),
    ],
)
def test_the_size_is_in_decimal_units_with_one_decimal_from_a_kilobyte(
    size: int, expected: tuple[Decimal, str]
) -> None:
    assert file_size_parts(size) == expected


def test_a_size_that_rounds_up_moves_to_the_next_unit() -> None:
    assert file_size_parts(999_950) == (Decimal("1.0"), "MB")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("terms.pdf", "PDF"),
        ("Terms.PDF", "PDF"),
        ("archive.tar.gz", "GZ"),
        ("noextension", ""),
        ("", ""),
    ],
)
def test_the_type_is_the_upper_case_extension(name: str, expected: str) -> None:
    assert file_type_label(name) == expected
