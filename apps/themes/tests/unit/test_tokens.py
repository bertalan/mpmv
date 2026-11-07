"""THM.2: the pure design-token functions (spec 14 §14.4, §14.5).

`parse_tokens` flattens a decoded `tokens.toml`, `resolve_tokens` merges a theme chain (child
values win) and resolves `{token}` references, `contrast_ratio` is the WCAG 2.x formula,
`check_tokens` applies the token contract and `render_tokens_css` / `build_theme_css` write CSS.
"""

import pytest

from apps.themes.engine import (
    CSS_LAYERS,
    ContrastPair,
    ThemeError,
    TokenContract,
    build_theme_css,
    check_tokens,
    contrast_ratio,
    css_name,
    minify_css,
    parse_tokens,
    relative_luminance,
    render_tokens_css,
    resolve_tokens,
)

# --- parse_tokens


def test_nested_tables_become_dotted_names() -> None:
    data = {
        "color": {"red": {"700": "#B3261E"}, "text": "{color.red.700}"},
        "step": {"0": "1rem"},
        "space": {"2xl": "3rem"},
    }
    assert parse_tokens(data) == {
        "color.red.700": "#B3261E",
        "color.text": "{color.red.700}",
        "step.0": "1rem",
        "space.2xl": "3rem",
    }


def test_numbers_become_strings() -> None:
    assert parse_tokens({"line": {"height": 1.5}, "z": {"menu": 10}}) == {
        "line.height": "1.5",
        "z.menu": "10",
    }


@pytest.mark.parametrize(
    "data",
    [
        {"color": {"text": True}},
        {"color": {"text": ["#000"]}},
        {"color": {"text": ""}},
        {"Color": {"text": "#000"}},
        {"color": {"te xt": "#000"}},
        {"color": {"text": "#000; background: url(x)"}},
        {"color": {"text": "#000}body{color:red"}},
        {"color": {"text": "</style><script>"}},
        {"color": {"text": "#000\\9"}},
        {"color": {"text": "#000\nx"}},
        {"color": {"text": "{color.Bad}"}},
        {"text": "#000"},
    ],
)
def test_invalid_tokens_are_rejected(data: dict[str, object]) -> None:
    with pytest.raises(ThemeError):
        parse_tokens(data)


# --- resolve_tokens


def test_child_values_override_parent_values() -> None:
    tokens = resolve_tokens(
        ["ride", "base"],
        {
            "base": {"color.accent": "#1D4ED8", "space.m": "1rem"},
            "ride": {"color.accent": "#046BD2"},
        },
    )
    assert tokens == {"color.accent": "#046BD2", "space.m": "1rem"}


def test_references_are_resolved_after_the_merge() -> None:
    tokens = resolve_tokens(
        ["ride", "base"],
        {
            "base": {
                "color.blue.700": "#1D4ED8",
                "color.accent": "{color.blue.700}",
                "color.link": "{color.accent}",
                "button.bg": "{color.accent}",
                "shadow.raised": "0 1px 3px {color.blue.700}",
            },
            "ride": {"color.blue.700": "#046BD2"},
        },
    )
    assert tokens["color.accent"] == "#046BD2"
    assert tokens["color.link"] == "#046BD2"
    assert tokens["button.bg"] == "#046BD2"
    assert tokens["shadow.raised"] == "0 1px 3px #046BD2"


def test_a_theme_without_tokens_inherits_everything() -> None:
    assert resolve_tokens(["calm", "base"], {"base": {"space.m": "1rem"}}) == {"space.m": "1rem"}


def test_unknown_and_cyclic_references_stay_unresolved() -> None:
    tokens = resolve_tokens(
        ["base"],
        {"base": {"color.text": "{color.nope}", "a.x": "{a.y}", "a.y": "{a.x}"}},
    )
    assert tokens["color.text"] == "{color.nope}"
    assert "{" in tokens["a.x"]
    assert "{" in tokens["a.y"]


# --- contrast_ratio (WCAG 2.x relative luminance)


def test_luminance_of_black_and_white() -> None:
    assert relative_luminance("#000000") == 0
    assert relative_luminance("#FFFFFF") == pytest.approx(1)


@pytest.mark.parametrize(
    ("fg", "bg", "expected"),
    [
        ("#000000", "#FFFFFF", 21.0),
        ("#FFFFFF", "#000000", 21.0),
        ("#777777", "#777777", 1.0),
        ("#fff", "#000", 21.0),
        # Measured pairs of the operator palette (spec 14 §14.12).
        ("#FFFFFF", "#046BD2", 5.20),
        ("#FFFFFF", "#035AB1", 6.77),
        ("#1A1A1A", "#FBA926", 8.95),
        ("#BE5900", "#FFFFFF", 4.55),
        ("#C65D00", "#FFFFFF", 4.23),
        ("#FFFFFF", "#FBA926", 1.94),
        ("#0580FA", "#121417", 4.81),
    ],
)
def test_contrast_ratio(fg: str, bg: str, expected: float) -> None:
    assert contrast_ratio(fg, bg) == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("colour", ["red", "#12345", "#GGGGGG", "rgb(0 0 0)", ""])
def test_contrast_ratio_rejects_what_is_not_a_hex_colour(colour: str) -> None:
    with pytest.raises(ThemeError):
        contrast_ratio(colour, "#FFFFFF")


# --- check_tokens

CONTRACT = TokenContract(
    required=("color.text", "color.surface", "color.focus"),
    pairs=(
        ContrastPair("color.text", "color.surface", 4.5),
        ContrastPair("color.focus", "color.surface", 3.0),
    ),
)


def test_valid_tokens_have_no_violations() -> None:
    tokens = {"color.text": "#1A1A1A", "color.surface": "#FFFFFF", "color.focus": "#2563EB"}
    assert check_tokens(tokens, CONTRACT) == []


def test_a_text_pair_below_4_5_fails() -> None:
    # #767676 on white is 4.54:1 (passes); #777777 is 4.48:1 (fails).
    passing = {"color.text": "#767676", "color.surface": "#FFFFFF", "color.focus": "#2563EB"}
    assert check_tokens(passing, CONTRACT) == []
    failing = {**passing, "color.text": "#777777"}
    (violation,) = check_tokens(failing, CONTRACT)
    assert violation.kind == "contrast"
    assert violation.token == "color.text/color.surface"
    assert "4.48:1" in violation.message
    assert "4.5:1" in violation.message


def test_a_ui_pair_below_3_fails() -> None:
    tokens = {"color.text": "#000000", "color.surface": "#FFFFFF", "color.focus": "#A0A0A0"}
    (violation,) = check_tokens(tokens, CONTRACT)
    assert violation.kind == "contrast"
    assert violation.token == "color.focus/color.surface"


def test_missing_required_tokens_are_reported_once() -> None:
    violations = check_tokens({"color.surface": "#FFFFFF", "color.focus": "#2563EB"}, CONTRACT)
    assert [(v.kind, v.token) for v in violations] == [("missing", "color.text")]


def test_unresolved_references_are_reported() -> None:
    tokens = {
        "color.text": "#000000",
        "color.surface": "#FFFFFF",
        "color.focus": "#2563EB",
        "button.bg": "{color.nope}",
    }
    assert [(v.kind, v.token) for v in check_tokens(tokens, CONTRACT)] == [
        ("unresolved", "button.bg")
    ]


def test_a_pair_token_that_is_not_a_colour_is_reported() -> None:
    tokens = {"color.text": "black", "color.surface": "#FFFFFF", "color.focus": "#2563EB"}
    assert [(v.kind, v.token) for v in check_tokens(tokens, CONTRACT)] == [
        ("invalid", "color.text")
    ]


# --- CSS


def test_css_names() -> None:
    assert css_name("color.text-muted") == "--color-text-muted"
    assert css_name("step.0") == "--step-0"
    assert css_name("color.red.700") == "--color-red-700"


def test_render_tokens_css_sorts_the_custom_properties_in_the_tokens_layer() -> None:
    css = render_tokens_css({"space.m": "1rem", "color.text": "#1A1A1A"})
    assert css == "@layer tokens{:root{--color-text:#1A1A1A;--space-m:1rem}}\n"


def test_minify_css_drops_comments_and_spaces_but_keeps_descendant_selectors() -> None:
    css = """
    /* a comment */
    .card :hover  >  a ,
    .x {
        margin : calc(1rem + 2px) ;
    }
    """
    assert minify_css(css) == ".card :hover>a,.x{margin :calc(1rem + 2px)}"


def test_build_theme_css_declares_the_layers_first_and_concatenates_base_first() -> None:
    css = build_theme_css(
        ["ride", "base"],
        tokens_css="@layer tokens{:root{--a:1}}",
        sources={
            "base": ["@layer base{body{margin:0}}"],
            "ride": ["@layer components{.hero{color:var(--a)}}"],
        },
    )
    assert css.startswith(f"@layer {','.join(CSS_LAYERS)};")
    assert CSS_LAYERS == (
        "reset",
        "tokens",
        "base",
        "layout",
        "components",
        "utilities",
        "overrides",
    )
    assert css.index("--a:1") < css.index("body{margin:0}") < css.index(".hero")
