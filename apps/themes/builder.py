"""The palette builder's logic without a request (THM.34, spec 14 §14.12).

The page, the no-JavaScript form and the live script all show the same thing: the palette
`derive_palette` makes from the base colours, every pair of the contrast table with its ratio, and
for a failing pair the nearest compliant shade of the base colour that can move.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .engine import derive_palette, nearest_compliant
from .palettes import PairResult, pair_report

BASE_ROLES = ("primary", "secondary", "band")


@dataclass(frozen=True)
class Suggestion:
    """ "Use this": `colour` replaces the base colour `field` and fixes (at least) `pair`."""

    field: str
    colour: str


@dataclass(frozen=True)
class PairRow:
    result: PairResult
    suggestion: Suggestion | None


@dataclass(frozen=True)
class BuilderReport:
    palette: dict[str, str]
    rows: list[PairRow]

    @property
    def failing(self) -> list[PairRow]:
        return [row for row in self.rows if not row.result.passes]

    @property
    def valid(self) -> bool:
        return not self.failing


def suggest(result: PairResult, palette: Mapping[str, str]) -> Suggestion | None:
    """The nearest compliant shade of the base colour in a failing pair (a derived role such as
    the focus ring has no base colour to move: the check reports it, the derivation fits it)."""
    if result.passes:
        return None
    for field, other in (
        (result.foreground_role, result.background),
        (result.background_role, result.foreground),
    ):
        if field in BASE_ROLES:
            colour = nearest_compliant(palette[field], other, result.minimum)
            if colour is not None and colour.upper() != palette[field].upper():
                return Suggestion(field, colour)
    return None


def build_report(
    base: Mapping[str, str], start: Mapping[str, str], surfaces: Sequence[str] = ()
) -> BuilderReport:
    """Derive every role from `base` (an empty optional colour takes `start`'s) and measure it.

    Raises ThemeError when a base colour is not a hex colour (from `derive_palette`)."""
    palette = derive_palette(base, start=start, surfaces=surfaces)
    rows = [PairRow(r, suggest(r, palette)) for r in pair_report(palette, surfaces)]
    return BuilderReport(palette, rows)
