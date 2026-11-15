"""The Stripe Payment Element `appearance` object, built from theme tokens (THM.8, spec 14 §14.9).

Stripe's iframe cannot read our CSS custom properties, so the object holds literal values taken
from the resolved tokens of the active theme: colours, font family and radius. The payment page
passes it to Stripe.js as a data attribute (no inline script).
"""

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from .build import read_tokens
from .engine import resolve_tokens
from .services import current_chain, themes_dir

# Stripe appearance variable -> token it takes its value from.
VARIABLES = {
    "colorPrimary": "color.accent",
    "colorBackground": "color.surface",
    "colorText": "color.text",
    "colorTextSecondary": "color.text-muted",
    "colorDanger": "color.danger",
    "fontFamily": "font.body",
    "borderRadius": "radius.control",
}


def payment_appearance(tokens: Mapping[str, str]) -> dict[str, Any]:
    """The appearance object for resolved `tokens`; a token that is missing is left out."""
    variables = {name: tokens[token] for name, token in VARIABLES.items() if token in tokens}
    rules: dict[str, dict[str, str]] = {}
    if "color.border-control" in tokens:
        rules[".Input"] = {"border": f"1px solid {tokens['color.border-control']}"}
    if "color.focus" in tokens:
        rules[".Input:focus"] = {
            "outline": f"2px solid {tokens['color.focus']}",
            "outlineOffset": "2px",
        }
    return {"theme": "flat", "variables": variables, "rules": rules}


@lru_cache(maxsize=16)
def _chain_appearance(root: str, chain: tuple[str, ...]) -> str:
    tokens = resolve_tokens(chain, {slug: read_tokens(Path(root) / slug) for slug in chain})
    return json.dumps(payment_appearance(tokens), sort_keys=True)


def active_payment_appearance() -> dict[str, Any]:
    """The appearance of the theme the page is rendered with."""
    value: dict[str, Any] = json.loads(_chain_appearance(str(themes_dir()), tuple(current_chain())))
    return value


def clear_cache() -> None:
    _chain_appearance.cache_clear()
