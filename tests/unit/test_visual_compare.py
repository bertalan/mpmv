"""THM.5: the visual comparison harness of the e2e suite (spec 14 §14.10).

Screenshots are compared with baselines under `tests/e2e/baselines/<theme>/<page>-<width>.png`
with a small tolerance, and a diff image is saved on failure. Baselines come only from the CI
image (Linux Chromium): the comparison runs in CI (or with `MPMV_VISUAL=compare` in the same
container), is skipped elsewhere, and `MPMV_VISUAL=update` (`make e2e-baselines`) rewrites them.
"""

from pathlib import Path

import pytest
from PIL import Image

from tests.e2e.visual import (
    BASELINE_DIR,
    baseline_path,
    compare_png,
    visual_mode,
)

WHITE = (255, 255, 255)


def png(
    path: Path, size: tuple[int, int] = (100, 100), colour: tuple[int, int, int] = WHITE
) -> Path:
    Image.new("RGB", size, colour).save(path)
    return path


def test_baselines_live_under_tests_e2e_by_theme_page_and_width() -> None:
    assert baseline_path("base", "styleguide", 360) == BASELINE_DIR / "base" / "styleguide-360.png"
    assert BASELINE_DIR.parts[-3:] == ("tests", "e2e", "baselines")


def test_identical_images_match(tmp_path: Path) -> None:
    result = compare_png(png(tmp_path / "a.png"), png(tmp_path / "b.png"), tmp_path / "diff.png")
    assert result.ok
    assert result.changed == 0
    assert not (tmp_path / "diff.png").exists()


def test_a_few_antialiasing_pixels_stay_within_the_tolerance(tmp_path: Path) -> None:
    actual = Image.new("RGB", (100, 100), WHITE)
    actual.putpixel((10, 10), (0, 0, 0))  # 1 pixel in 10 000
    actual.putpixel((20, 20), (250, 250, 250))  # below the per-channel threshold
    actual.save(tmp_path / "a.png")
    result = compare_png(tmp_path / "a.png", png(tmp_path / "b.png"), tmp_path / "diff.png")
    assert result.ok, result


def test_a_changed_region_fails_and_saves_a_diff_image(tmp_path: Path) -> None:
    actual = Image.new("RGB", (100, 100), WHITE)
    for x in range(30):
        for y in range(30):
            actual.putpixel((x, y), (200, 0, 0))
    actual.save(tmp_path / "a.png")
    diff = tmp_path / "out" / "diff.png"
    result = compare_png(tmp_path / "a.png", png(tmp_path / "b.png"), diff)
    assert not result.ok
    assert result.changed == 900
    assert diff.is_file()
    assert "900" in result.describe()


def test_a_different_size_fails(tmp_path: Path) -> None:
    result = compare_png(
        png(tmp_path / "a.png", (100, 120)), png(tmp_path / "b.png"), tmp_path / "diff.png"
    )
    assert not result.ok
    assert "size" in result.describe()


@pytest.mark.parametrize(
    ("env", "mode"),
    [
        ({}, "off"),
        ({"CI": "true"}, "compare"),
        ({"MPMV_VISUAL": "compare"}, "compare"),
        ({"MPMV_VISUAL": "update"}, "update"),
        ({"CI": "true", "MPMV_VISUAL": "off"}, "off"),
    ],
)
def test_the_mode_comes_from_the_environment(env: dict[str, str], mode: str) -> None:
    assert visual_mode(env) == mode


def test_an_unknown_mode_is_an_error() -> None:
    with pytest.raises(ValueError, match="MPMV_VISUAL"):
        visual_mode({"MPMV_VISUAL": "sometimes"})
