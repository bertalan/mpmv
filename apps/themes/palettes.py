"""Site colour palettes (THM.31, spec 14 §14.12): roles, the three predefined palettes, and
`check_brand_palette`, the contrast check every stored or built palette must pass. Pure data and
functions, no Django: `apps.vendors` may import it later (THM.11, THM.32).

A palette is a mapping of role name to `#rrggbb`. Roles map to the token contract names of
spec 14 §14.4 through `ROLE_TOKENS`. The primary is also the link colour.
"""

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .bootstrap_map import bootstrap_variables
from .engine import ThemeError, contrast_ratio, css_name, minify_css, resolve_tokens

TEXT = 4.5  # WCAG 2.2 SC 1.4.3
UI = 3.0  # WCAG 2.2 SC 1.4.11
LIGHT_SURFACE = "#FFFFFF"
DARK_SURFACE = "#121417"  # the dark token set of spec 14 §14.4

# Role -> token. `primary` also sets `color.link`.
ROLE_TOKENS: dict[str, str] = {
    "primary": "color.accent",
    "primary_hover": "color.accent-hover",
    "primary_tint": "color.accent-tint",
    "on_primary": "color.on-accent",
    "primary_dark": "color.accent-dark",
    "secondary": "color.secondary",
    "secondary_hover": "color.secondary-hover",
    "secondary_text": "color.secondary-text",
    "on_secondary": "color.on-secondary",
    "band": "color.surface-inverse",
    "text_on_band": "color.text-inverse",
    "focus": "color.focus",
    "control_border": "color.border-control",
}
ROLES = tuple(ROLE_TOKENS)

DEFAULT_PALETTE = "red"
PALETTE_LAYER = "palette"  # name of the pseudo theme that carries the palette tokens

PRESETS: dict[str, dict[str, str]] = {
    "red": {
        "name": "Red",
        "primary": "#C40417",
        "primary_hover": "#9E0312",
        "primary_tint": "#FDECEE",
        "on_primary": "#FFFFFF",
        "primary_dark": "#FF6B6B",
        "secondary": "#2B3138",
        "secondary_hover": "#1B2026",
        "secondary_text": "#2B3138",
        "on_secondary": "#FFFFFF",
        "band": "#30373F",
        "text_on_band": "#FFFFFF",
        "focus": "#2F80ED",
        "control_border": "#8A8A8A",
    },
    "blue": {
        "name": "Blue",
        "primary": "#046BD2",
        "primary_hover": "#035AB1",
        "primary_tint": "#EEF6FF",
        "on_primary": "#FFFFFF",
        "primary_dark": "#0580FA",
        "secondary": "#FBA926",
        "secondary_hover": "#E89A1E",
        "secondary_text": "#BE5900",
        "on_secondary": "#1A1A1A",
        "band": "#30373F",
        "text_on_band": "#FFFFFF",
        "focus": "#2F80ED",
        "control_border": "#8A8A8A",
    },
    "petrol": {
        "name": "Petrol",
        "primary": "#0B5C7A",
        "primary_hover": "#08465D",
        "primary_tint": "#E6F2F6",
        "on_primary": "#FFFFFF",
        "primary_dark": "#7CC8E3",
        "secondary": "#C2410C",
        "secondary_hover": "#9A3412",
        "secondary_text": "#C2410C",
        "on_secondary": "#FFFFFF",
        "band": "#30373F",
        "text_on_band": "#FFFFFF",
        "focus": "#2F80ED",
        "control_border": "#8A8A8A",
    },
}


@dataclass(frozen=True)
class PaletteViolation:
    """One failing pair: `pair` names it (`on_primary on primary_hover`), `kind` is missing,
    invalid or contrast."""

    kind: str
    pair: str
    message: str

    def __str__(self) -> str:
        return f"{self.pair}: {self.message}"


def _pairs(
    palette: Mapping[str, str], surfaces: Iterable[str]
) -> list[tuple[str, str, str, float]]:
    """(name, foreground, background, minimum) for every pair of spec 14 §14.12."""
    p = palette
    pairs = [
        ("on_primary on primary", p["on_primary"], p["primary"], TEXT),
        ("on_primary on primary_hover", p["on_primary"], p["primary_hover"], TEXT),
        ("primary on primary_tint", p["primary"], p["primary_tint"], TEXT),
        ("on_secondary on secondary", p["on_secondary"], p["secondary"], TEXT),
        ("on_secondary on secondary_hover", p["on_secondary"], p["secondary_hover"], TEXT),
        ("text_on_band on band", p["text_on_band"], p["band"], TEXT),
        (f"primary_dark on {DARK_SURFACE}", p["primary_dark"], DARK_SURFACE, TEXT),
        ("primary (UI) on primary_tint", p["primary"], p["primary_tint"], UI),
        ("focus on band", p["focus"], p["band"], UI),
    ]
    for surface in surfaces:
        pairs += [
            (f"primary (link) on surface {surface}", p["primary"], surface, TEXT),
            (f"secondary_text on surface {surface}", p["secondary_text"], surface, TEXT),
            (f"primary (UI) on surface {surface}", p["primary"], surface, UI),
            (f"focus on surface {surface}", p["focus"], surface, UI),
            (f"control_border on surface {surface}", p["control_border"], surface, UI),
        ]
    return pairs


# Exactly #RRGGBB, either case: no shorthand, no trailing newline, nothing else.
_ROLE_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")


def check_brand_palette(
    palette: Mapping[str, str], surfaces: Iterable[str] = ()
) -> list[PaletteViolation]:
    """Every role present and a valid colour, then every contrast pair of spec 14 §14.12.

    `surfaces` are the resolved `color.surface` of the registered themes; `#FFFFFF` is always
    checked. A failing pair is named with its measured ratio.
    """
    violations: list[PaletteViolation] = []
    for role in ROLES:
        if role not in palette:
            violations.append(PaletteViolation("missing", role, "required role"))
            continue
        if not isinstance(palette[role], str) or not _ROLE_HEX_RE.fullmatch(palette[role]):
            violations.append(
                PaletteViolation("invalid", role, f"{palette[role]!r} is not a #RRGGBB colour")
            )
            continue
        try:
            contrast_ratio(palette[role], LIGHT_SURFACE)
        except ThemeError as exc:
            violations.append(PaletteViolation("invalid", role, str(exc)))
    if violations:
        return violations
    ordered = dict.fromkeys([LIGHT_SURFACE, *(s.upper() for s in surfaces)])
    for name, foreground, background, minimum in _pairs(palette, ordered):
        ratio = contrast_ratio(foreground, background)
        if ratio < minimum:
            shown = int(ratio * 100) / 100  # floored: 4.499 must not read as 4.50
            violations.append(
                PaletteViolation(
                    "contrast",
                    name,
                    f"{foreground} on {background} is {shown:.2f}:1, "
                    f"below the {minimum:g}:1 minimum",
                )
            )
    return violations


def palette_tokens(palette: Mapping[str, str]) -> dict[str, str]:
    """The token overrides of a palette, `color.link` included."""
    tokens = {ROLE_TOKENS[role]: palette[role] for role in ROLES}
    tokens["color.link"] = palette["primary"]
    return tokens


def resolve_with_palette(
    chain: Sequence[str],
    tokens_by_theme: Mapping[str, Mapping[str, str]],
    palette: Mapping[str, str],
) -> dict[str, str]:
    """The resolved tokens of a theme (its `chain`, child first) with `palette` on top.

    The palette is one more theme above the chain, so a token derived from a palette token
    (`button.bg = "{color.accent}"`) follows it. Shared by the palette stylesheet and the Stripe
    appearance, so the payment iframe and the page always agree.
    """
    layered = {**tokens_by_theme, PALETTE_LAYER: palette_tokens(palette)}
    return resolve_tokens([PALETTE_LAYER, *chain], layered)


def render_palette_css(
    chain: Sequence[str],
    tokens_by_theme: Mapping[str, Mapping[str, str]],
    palette: Mapping[str, str],
    *,
    bootstrap: bool = False,
) -> str:
    """The stylesheet that turns a theme (its `chain`, child first) into `palette`.

    The palette is resolved as one more theme on top of the chain, so a token derived from a
    palette token (`button.bg = "{color.accent}"`) follows it. Only the custom properties that
    change are written, in one `@layer overrides{:root{...}}` rule (the last layer, so it beats
    the theme's tokens); with `bootstrap` the
    `--bs-*` variables of the Bootstrap map change too. Empty when the palette changes
    nothing (a theme already in that palette).
    """
    before = resolve_tokens(chain, tokens_by_theme)
    after = resolve_with_palette(chain, tokens_by_theme, palette)
    changed = {css_name(k): v for k, v in after.items() if before.get(k) != v}
    if bootstrap:
        try:
            old, new = bootstrap_variables(before), bootstrap_variables(after)
        except KeyError:
            old = new = {}
        changed.update({k: v for k, v in new.items() if old.get(k) != v})
    if not changed:
        return ""
    body = ";".join(f"{name}:{changed[name]}" for name in sorted(changed))
    return minify_css(f"@layer overrides{{:root{{{body}}}}}") + "\n"


def css_digest(css: str) -> str:
    """The 12 hex digits that name a palette stylesheet (its URL changes with its content)."""
    return hashlib.sha256(css.encode()).hexdigest()[:12]
