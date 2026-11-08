"""The token contract (THM.2, spec 14 §14.4): what every theme's resolved tokens must hold.

Token names are dotted (`color.text-muted`) and become custom properties
(`--color-text-muted`). A child theme lists only the tokens it changes; the contract applies to
the tokens resolved along its chain. Pure data, no Django.
"""

from .engine import ContrastPair, TokenContract

TEXT = 4.5  # WCAG 2.2 SC 1.4.3, normal text
UI = 3.0  # WCAG 2.2 SC 1.4.11, non-text contrast

COLOURS = (
    "color.text",
    "color.text-muted",
    "color.text-inverse",
    "color.surface",
    "color.surface-raised",
    "color.surface-inverse",
    "color.accent",
    "color.on-accent",
    "color.accent-hover",
    "color.accent-tint",
    "color.secondary",
    "color.on-secondary",
    "color.secondary-hover",
    "color.secondary-text",
    "color.link",
    "color.focus",
    "color.danger",
    "color.info",
    "color.success",
    "color.warning",
    "color.border-control",
)

OTHER = (
    "font.body",
    "font.display",
    *(f"step.{n}" for n in range(6)),
    *(f"space.{size}" for size in ("xs", "s", "m", "l", "xl", "2xl", "3xl")),
    "radius.control",
    "radius.card",
    "shadow.raised",
    "duration.fast",
    "header.height",
)

PAIRS = (
    ContrastPair("color.text", "color.surface", TEXT),
    ContrastPair("color.text-muted", "color.surface", TEXT),
    ContrastPair("color.link", "color.surface", TEXT),
    ContrastPair("color.on-accent", "color.accent", TEXT),
    ContrastPair("color.on-accent", "color.accent-hover", TEXT),
    ContrastPair("color.accent", "color.accent-tint", TEXT),
    ContrastPair("color.on-secondary", "color.secondary", TEXT),
    ContrastPair("color.on-secondary", "color.secondary-hover", TEXT),
    ContrastPair("color.secondary-text", "color.surface", TEXT),
    ContrastPair("color.text-inverse", "color.surface-inverse", TEXT),
    ContrastPair("color.danger", "color.surface", TEXT),
    # The raised surface (alerts, footer) carries body text and links (THM.4).
    ContrastPair("color.text", "color.surface-raised", TEXT),
    ContrastPair("color.text-muted", "color.surface-raised", TEXT),
    ContrastPair("color.link", "color.surface-raised", TEXT),
    # Ghost buttons and the language switcher show link text on the accent tint when hovered.
    ContrastPair("color.link", "color.accent-tint", TEXT),
    ContrastPair("color.focus", "color.surface", UI),
    ContrastPair("color.focus", "color.surface-inverse", UI),
    ContrastPair("color.border-control", "color.surface", UI),
    ContrastPair("color.accent", "color.surface", UI),
    # The level bar of an alert: a UI component on the raised surface (THM.4).
    ContrastPair("color.info", "color.surface-raised", UI),
    ContrastPair("color.success", "color.surface-raised", UI),
    ContrastPair("color.warning", "color.surface-raised", UI),
    ContrastPair("color.danger", "color.surface-raised", UI),
    # The library's indicator bars on the plain surface: a cart line's problem, a toast (THM.4).
    ContrastPair("color.warning", "color.surface", UI),
    ContrastPair("color.success", "color.surface", UI),
)

TOKEN_CONTRACT = TokenContract(required=(*COLOURS, *OTHER), pairs=PAIRS)
