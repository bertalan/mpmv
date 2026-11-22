"""THM.8: the Stripe Payment Element appearance is built from the theme tokens (spec 14 §14.9).

Stripe's `appearance` object takes literal values, never `var(--x)`, so the object is derived
from the resolved tokens of the active theme: colours, font family and radius.
"""

import json

import pytest

from apps.themes.appearance import active_payment_appearance, payment_appearance
from apps.themes.build import read_manifest_sources, read_tokens
from apps.themes.engine import build_registry, resolve_tokens
from apps.themes.services import use_theme
from apps.themes.tests.builders import SHIPPED_THEMES


def tokens_of(slug: str) -> dict[str, str]:
    registry = build_registry(read_manifest_sources(SHIPPED_THEMES))
    chain = registry.chain(slug)
    return resolve_tokens(chain, {s: read_tokens(SHIPPED_THEMES / s) for s in chain})


def test_the_appearance_uses_the_ride_colours_fonts_and_radius() -> None:
    tokens = tokens_of("ride")
    appearance = payment_appearance(tokens)
    variables = appearance["variables"]
    assert variables["colorPrimary"] == tokens["color.accent"] == "#C40417"
    assert variables["colorText"] == tokens["color.text"]
    assert variables["colorBackground"] == tokens["color.surface"]
    assert variables["colorDanger"] == tokens["color.danger"]
    assert variables["fontFamily"] == tokens["font.body"]
    assert "Barlow" in variables["fontFamily"]
    assert variables["borderRadius"] == tokens["radius.control"] == "4px"
    assert appearance["theme"] == "flat"


def test_the_appearance_is_literal_values_only() -> None:
    text = json.dumps(payment_appearance(tokens_of("ride")))
    assert "var(" not in text
    assert "{color." not in text


def test_the_base_theme_gets_its_own_values() -> None:
    base = payment_appearance(tokens_of("base"))["variables"]
    ride = payment_appearance(tokens_of("ride"))["variables"]
    assert base["colorPrimary"] != ride["colorPrimary"] or base["fontFamily"] != ride["fontFamily"]


def test_every_text_pair_of_the_appearance_meets_aa() -> None:
    from apps.themes.engine import contrast_ratio

    variables = payment_appearance(tokens_of("ride"))["variables"]
    assert contrast_ratio(variables["colorText"], variables["colorBackground"]) >= 4.5
    assert contrast_ratio(variables["colorDanger"], variables["colorBackground"]) >= 4.5


def test_the_active_theme_chooses_the_appearance() -> None:
    with use_theme("ride"):
        assert active_payment_appearance()["variables"]["colorPrimary"] == "#C40417"


# --- THM.31: the site palette reaches the payment iframe (spec 14 §14.12)


def palette_roles(slug: str) -> dict[str, str]:
    from apps.themes.palettes import PRESETS, ROLES

    return {role: PRESETS[slug][role] for role in ROLES}


@pytest.mark.parametrize("slug", ["red", "blue", "petrol"])
def test_the_appearance_follows_the_palette_primary_text_and_surface(slug: str) -> None:
    from apps.themes.palettes import PALETTE_LAYER, palette_tokens

    roles = palette_roles(slug)
    with use_theme("ride"):
        value = active_payment_appearance(roles)
    chain = ("ride", "base")
    expected = resolve_tokens(
        [PALETTE_LAYER, *chain],
        {
            **{s: read_tokens(SHIPPED_THEMES / s) for s in chain},
            PALETTE_LAYER: palette_tokens(roles),
        },
    )
    variables = value["variables"]
    assert variables["colorPrimary"] == roles["primary"] == expected["color.accent"]
    assert variables["colorText"] == expected["color.text"]
    assert variables["colorBackground"] == expected["color.surface"]
    assert value["rules"][".Input"]["border"] == f"1px solid {roles['control_border']}"
    assert value["rules"][".Input:focus"]["outline"] == f"2px solid {roles['focus']}"


def test_blue_and_petrol_leave_the_red_primary_behind() -> None:
    with use_theme("ride"):
        primaries = {
            slug: active_payment_appearance(palette_roles(slug))["variables"]["colorPrimary"]
            for slug in ("blue", "petrol")
        }
    assert "#C40417" not in primaries.values()


def test_no_palette_means_the_theme_defaults() -> None:
    with use_theme("ride"):
        assert active_payment_appearance(None) == active_payment_appearance()
        assert (
            active_payment_appearance()["variables"]
            == payment_appearance(tokens_of("ride"))["variables"]
        )


def test_clear_caches_forgets_the_cached_appearance() -> None:
    from apps.themes.appearance import _chain_appearance
    from apps.themes.services import clear_caches

    with use_theme("ride"):
        active_payment_appearance()
    assert _chain_appearance.cache_info().currsize
    clear_caches()
    assert not _chain_appearance.cache_info().currsize
