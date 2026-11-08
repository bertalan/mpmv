"""Visual comparison harness of the e2e suite (THM.5, spec 14 §14.10).

Baselines are `tests/e2e/baselines/<theme>/<page>-<width>.png`. Font rendering differs between
platforms, so baselines are produced only on the CI runner (Linux Chromium) and the comparison
runs only there. `MPMV_VISUAL` selects the mode:

- unset: `compare` in CI (`CI` is set by GitHub Actions), `off` elsewhere (the test is skipped
  with a message);
- `compare`: compare even outside CI, for a run inside the same Linux container;
- `update`: write the screenshots as the new baselines (`make e2e-baselines`); the new PNGs are
  reviewed in the pull request like code;
- `off`: skip.

In `compare` mode a page without a baseline is skipped, and its screenshot is saved under
`build/e2e/baselines/` so CI uploads it as an artifact: THM.10 reviews and commits the first
baselines. On a mismatch the screenshot is saved there too (the candidate new baseline for an
intentional change) and a diff image is saved under `build/e2e/diffs/`. The `Visual baselines`
workflow (manual dispatch) runs `MPMV_VISUAL=update` on the CI runner and uploads every PNG.
"""

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image, ImageChops
from playwright.sync_api import Page

ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = ROOT / "tests" / "e2e" / "baselines"
CANDIDATE_DIR = ROOT / "build" / "e2e" / "baselines"  # uploaded by CI as an artifact
ACTUAL_DIR = ROOT / "build" / "e2e" / "visual"
DIFF_DIR = ROOT / "build" / "e2e" / "diffs"
WIDTHS = (360, 768, 1280)
CHANNEL_THRESHOLD = 32  # a pixel counts as changed when a channel moves more than this (0-255)
TOLERANCE = 0.001  # share of changed pixels that still matches (anti-aliasing noise)
DIFF_COLOUR = (255, 0, 255)

Mode = Literal["off", "compare", "update"]
_MODES: tuple[Mode, ...] = ("off", "compare", "update")


def visual_mode(env: Mapping[str, str] | None = None) -> Mode:
    """The mode from `MPMV_VISUAL`, defaulting to `compare` in CI and `off` elsewhere."""
    env = os.environ if env is None else env
    value = env.get("MPMV_VISUAL", "").strip().lower()
    if not value:
        return "compare" if env.get("CI") else "off"
    for mode in _MODES:
        if value == mode:
            return mode
    raise ValueError(f"MPMV_VISUAL must be one of {', '.join(_MODES)}, not {value!r}")


def require_visual_mode() -> Mode:
    """Skip the calling test when the comparison is off; return the mode otherwise."""
    mode = visual_mode()
    if mode == "off":
        pytest.skip(
            "Visual comparison runs only in CI (Linux Chromium baselines, spec 14 §14.10). "
            "Set MPMV_VISUAL=compare to run it on a Linux Chromium like the CI runner."
        )
    return mode


def baseline_path(theme: str, name: str, width: int) -> Path:
    return BASELINE_DIR / theme / f"{name}-{width}.png"


@dataclass(frozen=True)
class Comparison:
    changed: int
    total: int
    size_mismatch: str = ""
    tolerance: float = TOLERANCE

    @property
    def ok(self) -> bool:
        return not self.size_mismatch and self.changed <= self.total * self.tolerance

    def describe(self) -> str:
        if self.size_mismatch:
            return f"image size differs: {self.size_mismatch}"
        share = self.changed / self.total if self.total else 0.0
        return f"{self.changed} of {self.total} pixels changed ({share:.3%})"


def compare_png(
    actual: Path,
    expected: Path,
    diff: Path,
    *,
    threshold: int = CHANNEL_THRESHOLD,
    tolerance: float = TOLERANCE,
) -> Comparison:
    """Compare two PNGs; on a mismatch write `diff` (changed pixels in magenta over the actual)."""
    with Image.open(actual) as a, Image.open(expected) as e:
        got, want = a.convert("RGB"), e.convert("RGB")
    if got.size != want.size:
        return Comparison(0, 0, size_mismatch=f"{got.size} instead of {want.size}")
    delta = ImageChops.difference(got, want).convert("L")
    # The greyscale difference weighs the channels (luminance): a third of the per-channel limit.
    mask = delta.point(lambda value: 255 if value > threshold / 3 else 0)
    histogram = mask.histogram()
    result = Comparison(changed=histogram[255], total=got.width * got.height, tolerance=tolerance)
    if not result.ok:
        diff.parent.mkdir(parents=True, exist_ok=True)
        overlay = got.copy()
        overlay.paste(Image.new("RGB", got.size, DIFF_COLOUR), mask=mask)
        overlay.save(diff)
    return result


Status = Literal["updated", "missing", "match", "mismatch"]


@dataclass(frozen=True)
class Outcome:
    status: Status
    comparison: Comparison | None = None

    def describe(self) -> str:
        return self.comparison.describe() if self.comparison else self.status


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def check_screenshot(
    actual: Path, baseline: Path, candidate: Path, diff: Path, *, mode: Mode
) -> Outcome:
    """Settle a screenshot against its baseline.

    `update` copies it over the baseline. In `compare` mode a missing baseline or a mismatch
    copies the screenshot to `candidate` (uploaded by CI), so a reviewed change can commit the
    CI-rendered PNG as the new baseline; a mismatch also writes the diff image.
    """
    if mode == "update":
        _copy(actual, baseline)
        return Outcome("updated")
    if not baseline.is_file():
        _copy(actual, candidate)
        return Outcome("missing")
    result = compare_png(actual, baseline, diff)
    if result.ok:
        return Outcome("match", result)
    _copy(actual, candidate)
    return Outcome("mismatch", result)


def assert_matches_baseline(page: Page, *, theme: str, name: str, width: int) -> None:
    """Screenshot the full page at `width` and compare it with its baseline (see the module)."""
    mode = require_visual_mode()
    page.set_viewport_size({"width": width, "height": 900})
    page.wait_for_timeout(100)  # the layout settles after the resize
    page.evaluate("() => document.fonts.ready")
    actual = ACTUAL_DIR / theme / f"{name}-{width}.png"
    actual.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=str(actual),
        full_page=True,
        animations="disabled",
        caret="hide",
        # Dynamic regions: the launch countdown depends on the current time.
        mask=[page.locator("[data-countdown]")],
    )
    baseline = baseline_path(theme, name, width)
    candidate = CANDIDATE_DIR / theme / baseline.name
    diff = DIFF_DIR / theme / baseline.name
    outcome = check_screenshot(actual, baseline, candidate, diff, mode=mode)
    if outcome.status == "missing":
        pytest.skip(
            f"No baseline {baseline.relative_to(ROOT)} yet; the CI screenshot is saved as "
            f"{candidate.relative_to(ROOT)} (CI artifact) for review (THM.10)."
        )
    assert outcome.status != "mismatch", (
        f"{theme}/{name} at {width} px differs from its baseline: {outcome.describe()}; "
        f"diff image: {diff.relative_to(ROOT)}; if the change is intended, the new baseline "
        f"is {candidate.relative_to(ROOT)} (CI artifact)"
    )
