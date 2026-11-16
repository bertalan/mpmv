"""The token to `--bs-*` map of a Bootstrap theme (THM.13, spec 14 §14.4).

Bootstrap 5.3 reads its colours, type, radii and shadows from custom properties. For a theme
with a foundation the build adds this block in `@layer tokens`, after the theme's own tokens,
so the semantic tokens stay the single source: the contract pairs of §14.4 are checked on them,
and a Bootstrap component can only show colours that passed the contract. The `-rgb`,
`-text-emphasis`, `-bg-subtle` and `-border-subtle` variants are derived the way Bootstrap's own
Sass does it: a shade of 60 % for emphasis text, tints of 80 % and 60 % for the subtle
backgrounds and borders.
"""

from collections.abc import Mapping

from .engine import Tokens, _channels

# Bootstrap colour family -> semantic token it takes its colour from.
FAMILIES: Mapping[str, str] = {
    "primary": "color.accent",
    "secondary": "color.secondary",
    "success": "color.success",
    "info": "color.info",
    "warning": "color.warning",
    "danger": "color.danger",
    "light": "color.surface-raised",
    "dark": "color.surface-inverse",
}
SHADE_EMPHASIS = 0.6
TINT_BACKGROUND = 0.8
TINT_BORDER = 0.6


def _hex(channels: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*channels)


def rgb(colour: str) -> str:
    """`#046BD2` -> `4,107,210`, the form of Bootstrap's `--bs-*-rgb` variables."""
    return ",".join(str(channel) for channel in _channels(colour))


def shade(colour: str, weight: float) -> str:
    """`colour` mixed with black (`weight` is the share of black)."""
    return _hex(tuple(round(c * (1 - weight)) for c in _channels(colour)))  # type: ignore[arg-type]


def tint(colour: str, weight: float) -> str:
    """`colour` mixed with white (`weight` is the share of white)."""
    return _hex(tuple(round(c * (1 - weight) + 255 * weight) for c in _channels(colour)))  # type: ignore[arg-type]


def bootstrap_variables(tokens: Tokens) -> dict[str, str]:
    """The `--bs-*` custom properties for the resolved tokens of a theme."""

    def colour(name: str, *fallbacks: str) -> str:
        for candidate in (name, *fallbacks):
            if candidate in tokens:
                return tokens[candidate]
        raise KeyError(name)

    out: dict[str, str] = {}

    def put_colour(name: str, value: str) -> None:
        out[f"--bs-{name}"] = value
        out[f"--bs-{name}-rgb"] = rgb(value)

    for family, token in FAMILIES.items():
        base = tokens[token]
        put_colour(family, base)
        out[f"--bs-{family}-text-emphasis"] = shade(base, SHADE_EMPHASIS)
        out[f"--bs-{family}-bg-subtle"] = tint(base, TINT_BACKGROUND)
        out[f"--bs-{family}-border-subtle"] = tint(base, TINT_BORDER)

    text = colour("color.text")
    muted = colour("color.text-muted")
    put_colour("body-color", text)
    put_colour("body-bg", colour("color.surface"))
    put_colour("emphasis-color", colour("color.heading", "color.text"))
    put_colour("secondary-color", muted)
    put_colour("secondary-bg", colour("color.surface-sunken", "color.surface-raised"))
    put_colour("tertiary-color", muted)
    put_colour("tertiary-bg", colour("color.surface-raised"))
    put_colour("link-color", colour("color.link"))
    put_colour("link-hover-color", colour("color.accent-hover"))
    out["--bs-heading-color"] = "var(--color-heading,var(--color-text))"
    out["--bs-code-color"] = colour("color.danger")
    out["--bs-highlight-color"] = text
    out["--bs-highlight-bg"] = colour("color.accent-tint")
    out["--bs-border-color"] = colour("color.border", "color.border-control")
    out["--bs-border-color-translucent"] = colour("color.border", "color.border-control")
    out["--bs-form-valid-color"] = colour("color.success")
    out["--bs-form-valid-border-color"] = colour("color.success")
    out["--bs-form-invalid-color"] = colour("color.danger")
    out["--bs-form-invalid-border-color"] = colour("color.danger")
    out["--bs-focus-ring-color"] = colour("color.focus")
    out["--bs-focus-ring-opacity"] = "1"
    out["--bs-focus-ring-width"] = "var(--focus-width,3px)"

    out["--bs-body-font-family"] = "var(--font-body)"
    out["--bs-body-font-size"] = "var(--step-0)"
    if "font.mono" in tokens:
        out["--bs-font-monospace"] = "var(--font-mono)"
    out["--bs-border-radius"] = "var(--radius-control)"
    out["--bs-border-radius-sm"] = "calc(var(--radius-control) * .67)"
    out["--bs-border-radius-lg"] = "var(--radius-card)"
    out["--bs-border-radius-xl"] = "calc(var(--radius-card) * 1.5)"
    out["--bs-border-radius-xxl"] = "calc(var(--radius-card) * 2)"
    out["--bs-border-radius-pill"] = "var(--radius-pill,50rem)"
    for suffix in ("", "-sm", "-lg"):
        out[f"--bs-box-shadow{suffix}"] = "var(--shadow-raised)"
    return out


def bootstrap_map_css(tokens: Tokens) -> str:
    """`@layer tokens{:root{--bs-…}}`, properties sorted by name."""
    variables = bootstrap_variables(tokens)
    body = ";".join(f"{name}:{variables[name]}" for name in sorted(variables))
    return f"@layer tokens{{:root{{{body}}}}}\n"
