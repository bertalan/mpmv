"""SEC.5 ReDoS: `extract_dimensions_mm` runs in linear time on a long product text."""

import time

import pytest

from apps.ingest.extraction import extract_dimensions_mm

BUDGET = 2.0  # seconds; the quadratic version needs minutes on these inputs


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30 x 20 x 10 cm", {"length_mm": 300, "width_mm": 200, "height_mm": 100}),
        ("30x20 CM", {"length_mm": 300, "width_mm": 200}),
        ("1,5 \u00d7 2.5 m", {"length_mm": 1500, "width_mm": 2500}),
        ("size: 120*80*45mm.", {"length_mm": 120, "width_mm": 80, "height_mm": 45}),
        ("a.5x3mm", {"length_mm": 5, "width_mm": 3}),
        ("12345x6 mm", {"length_mm": 12345, "width_mm": 6}),
        ("0 x 5 cm", {}),
        ("30 x 20", {}),
        ("", {}),
    ],
)
def test_extract_dimensions_keeps_its_output(text: str, expected: dict[str, int]) -> None:
    assert extract_dimensions_mm(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "1" * 100_000,
        "1" * 100_000 + "x",
        "1." * 50_000,
        "1 x " * 25_000 + "!",
        "1" * 50_000 + " " * 50_000 + "x",
    ],
)
def test_extract_dimensions_is_linear_on_hostile_text(text: str) -> None:
    started = time.perf_counter()
    extract_dimensions_mm(text)
    assert time.perf_counter() - started < BUDGET
