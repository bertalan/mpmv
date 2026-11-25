"""THM.24 (part 2/2): the tile scrim keeps the title readable on any picture.

The title is white on the scrim. The worst pictures are pure white and pure black, so the text
must reach 4.5:1 on the scrim laid over each of them, under every predefined palette, and the CSS
must keep the scrim at full strength behind the text block (no fade to transparent there).
"""

import re
from pathlib import Path

import pytest

from apps.themes.build import read_tokens
from apps.themes.engine import contrast_ratio
from apps.themes.palettes import PRESETS, ROLES, resolve_with_palette

THEMES = Path(__file__).resolve().parents[4] / "themes"
CHAIN = ("ride", "base")
TOKENS = {slug: read_tokens(THEMES / slug) for slug in CHAIN}
CSS = THEMES / "ride/static/themes/ride/css/32-home-layout.css"


def _rgb(colour: str) -> tuple[float, float, float]:
    value = colour.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _over(scrim: str, alpha: float, picture: tuple[int, int, int]) -> str:
    mixed = [round(alpha * s + (1 - alpha) * p) for s, p in zip(_rgb(scrim), picture, strict=True)]
    return "#{:02x}{:02x}{:02x}".format(*mixed)


@pytest.mark.parametrize("palette", sorted(PRESETS))
@pytest.mark.parametrize("picture", [(255, 255, 255), (0, 0, 0)], ids=["white", "black"])
def test_the_title_reaches_4_5_on_the_scrim_over_any_picture(
    palette: str, picture: tuple[int, int, int]
) -> None:
    tokens = resolve_with_palette(CHAIN, TOKENS, {role: PRESETS[palette][role] for role in ROLES})
    under_text = _over(tokens["color.overlay"], float(tokens["scrim.opacity"]), picture)
    assert contrast_ratio(tokens["color.text-inverse"], under_text) >= 4.5


def test_the_scrim_stays_at_full_strength_behind_the_text_block() -> None:
    source = CSS.read_text()
    block = source[source.index(".tile__scrim {") :]
    gradient = block[: block.index(");")]
    stops = [int(n) for n in re.findall(r"(?:\)|transparent)\s+(\d+)%", gradient)]
    assert gradient.count("color-mix(") == 2  # strong at the start and again before the fade
    assert stops[0] == 0
    assert stops[-1] == 100
    assert stops[1] >= 55, "the strong scrim must reach past the text block"
