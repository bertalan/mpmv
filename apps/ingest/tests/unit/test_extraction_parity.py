# ruff: noqa: E501
"""SEC.5 ReDoS rewrite: `extract_dimensions_mm` gives the same output as before on real product
dimension strings."""

import pytest

from apps.ingest.extraction import extract_dimensions_mm

# Expected values were taken from the implementation before the SEC.5 ReDoS rewrite; the inputs are valid, real-looking text.

CASES = [
    ("30 x 20 x 10 cm", {"length_mm": 300, "width_mm": 200, "height_mm": 100}),
    ("L 300 mm", {}),
    ("Ø 12 cm", {}),
    ("12,5 x 7,5 cm", {"length_mm": 125, "width_mm": 75}),
    ("Dimensions: 120*80*45 mm.", {"length_mm": 120, "width_mm": 80, "height_mm": 45}),
    ("1,5 \u00d7 2,25 m", {"length_mm": 1500, "width_mm": 2250}),
    ("Size 300 x 200 mm (L x W)", {"length_mm": 300, "width_mm": 200}),
    ("Box 45x30x30CM, weight 2,5 kg", {"length_mm": 450, "width_mm": 300, "height_mm": 300}),
    ("Product 12.5 x 8 x 4.25 cm", {"length_mm": 125, "width_mm": 80, "height_mm": 43}),
    ("W 20 cm", {}),
    ("L 300 mm x 200 mm", {}),
    ("80 cm x 60 cm x 40 cm", {}),
    ("SKU 12345 - 30 x 20 cm", {"length_mm": 300, "width_mm": 200}),
    ("Item 2 x 3 m", {"length_mm": 2000, "width_mm": 3000}),
    ("10x10", {}),
    ("0,5 x 0,5 x 0,5 m", {"length_mm": 500, "width_mm": 500, "height_mm": 500}),
    ("Helmet 28 x 22 x 30 centimetri", {}),
    ("Pack of 3 x 20 x 10 mm", {"length_mm": 3, "width_mm": 20, "height_mm": 10}),
    ("1 234 x 56 mm", {"length_mm": 234, "width_mm": 56}),
    ("18 x 24 cm - 7 x 9 in", {"length_mm": 180, "width_mm": 240}),
    (None, {}),
]


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_extract_dimensions_parity(text: str | None, expected: dict[str, int]) -> None:
    assert extract_dimensions_mm(text) == expected
