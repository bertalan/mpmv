"""THM.34: the builder's report lists every pair with its ratio and offers a compliant shade."""

import pytest

from apps.themes.builder import build_report
from apps.themes.engine import ThemeError, contrast_ratio
from apps.themes.palettes import PRESETS, check_brand_palette, pair_report

RED = PRESETS["red"]
START = {k: v for k, v in RED.items() if k != "name"}


def test_every_predefined_palette_reports_no_failing_pair() -> None:
    for preset in PRESETS.values():
        base = {k: preset[k] for k in ("primary", "secondary", "band")}
        report = build_report(base, START)
        assert report.valid
        assert report.failing == []


def test_the_pair_list_has_the_ratio_of_every_pair_the_check_uses() -> None:
    results = pair_report(START)
    assert len(results) == 9 + 5
    assert {r.name for r in results if not r.passes} == {v.pair for v in check_brand_palette(START)}
    first = results[0]
    assert first.ratio == int(contrast_ratio(first.foreground, first.background) * 100) / 100


def test_a_failing_primary_names_the_pair_and_offers_a_shade_that_fixes_it() -> None:
    report = build_report({"primary": "#FFE600"}, START)
    assert not report.valid
    link = next(r for r in report.failing if r.result.name.startswith("primary (link)"))
    assert link.result.ratio < 4.5
    assert link.suggestion is not None
    assert link.suggestion.field == "primary"
    assert contrast_ratio(link.suggestion.colour, "#FFFFFF") >= 4.5


def test_using_every_suggestion_in_turn_ends_with_a_valid_palette() -> None:
    base = {"primary": "#FFE600", "secondary": "#FFE600", "band": "#CCCCCC"}
    for _ in range(6):
        report = build_report(base, START)
        offered = [r.suggestion for r in report.failing if r.suggestion is not None]
        if not offered:
            break
        base[offered[0].field] = offered[0].colour
    assert build_report(base, START).valid


def test_an_optional_colour_left_empty_takes_the_starting_palette() -> None:
    report = build_report({"primary": "#0B5C7A", "secondary": "", "band": ""}, START)
    assert report.palette["secondary"] == START["secondary"]
    assert report.palette["band"] == START["band"]


def test_a_value_that_is_not_a_colour_is_an_error() -> None:
    with pytest.raises(ThemeError):
        build_report({"primary": "not a colour"}, START)


def test_a_passing_pair_has_no_suggestion() -> None:
    assert all(r.suggestion is None for r in build_report({}, START).rows if r.result.passes)


def test_preview_css_scopes_every_palette_token_to_the_preview_container() -> None:
    from apps.themes.builder import preview_css

    css = preview_css(build_report({"primary": "#0B5C7A"}, START).palette)
    assert css.startswith(".palette-preview{")
    assert "--color-accent:#0B5C7A" in css
    assert "--color-link:#0B5C7A" in css
    assert ":root" not in css
    assert "<" not in css
    assert "@import" not in css
