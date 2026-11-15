"""THM.13 part 2 of 3: token map, budgets, CSP lint and selector collisions (§14.4, 14.5, 14.7).

The generated `bootstrap-map` block sets Bootstrap's colour variables from the semantic tokens;
the budgets are manifest keys; the CSP lint rejects the Bootstrap features that build markup from
strings; and the base CSS and Bootstrap never define the same class without a recorded reason.
"""

import re
from pathlib import Path

import pytest
from django.contrib.staticfiles.finders import get_finder
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.themes.bootstrap_map import bootstrap_map_css, bootstrap_variables
from apps.themes.build import (
    build_themes,
    read_css_sources,
    read_manifest_sources,
    read_tokens,
)
from apps.themes.collisions import (
    ALLOWED_COLLISIONS,
    class_names,
    colliding_classes,
)
from apps.themes.csp_lint import lint_template
from apps.themes.engine import ThemeError, build_registry, parse_manifest, resolve_tokens
from apps.themes.foundation import BOOTSTRAP_DIR
from apps.themes.gates import CSS_BUDGET, JS_BUDGET, compressed_size, run_gates
from apps.themes.tests.builders import SHIPPED_THEMES, write_theme
from apps.themes.tests.unit.test_bootstrap_foundation import (
    VENDORED,
    foundation_theme,
    ride_css,
    upstream_css,
)
from apps.themes.tests.unit.test_gates import incompressible_css

RIDE = SHIPPED_THEMES / "ride"
BASE = SHIPPED_THEMES / "base"
# The raw palette of Bootstrap (`--bs-blue` ... `--bs-gray-900`, black, white): components use
# the semantic families, not the palette, so the map does not have to replace it.
PALETTE = re.compile(
    r"^--bs-(?:blue|indigo|purple|pink|red|orange|yellow|green|teal|cyan|black|white"
    r"|gray(?:-dark|-\d00)?)(?:-rgb)?$"
)
COLOUR_VALUE = re.compile(r"^(?:#[0-9a-f]{3,8}|\d+,\d+,\d+|rgba?\([^)]*\))$", re.IGNORECASE)
# Upstream's value is also ours for these: a white page and the matching default are the same
# colour, which is the point, not a missed mapping.
EQUAL_BY_DESIGN = {"--bs-body-bg", "--bs-body-bg-rgb"}


def ride_tokens() -> dict[str, str]:
    return resolve_tokens(
        ("ride", "base"),
        {"ride": read_tokens(RIDE), "base": read_tokens(BASE)},
    )


def declarations(block: str) -> dict[str, str]:
    result = {}
    for part in re.split(r";(?![^(]*\))", block):
        name, _, value = part.partition(":")
        if name.strip().startswith("--"):
            result[name.strip()] = value.strip()
    return result


def upstream_defaults() -> dict[str, str]:
    match = re.search(r":root,\[data-bs-theme=light\]\{([^}]*)\}", upstream_css())
    assert match
    return declarations(match.group(1))


def built_map(css: str) -> dict[str, str]:
    """The `--bs-*` declarations of every `@layer tokens` block of a built theme.css."""
    found: dict[str, str] = {}
    for block in re.findall(r"@layer tokens\{:root\{([^}]*)\}\}", css):
        found.update({k: v for k, v in declarations(block).items() if k.startswith("--bs-")})
    return found


# --- 1. Token map


def test_the_map_is_a_tokens_layer_block_of_bootstrap_variables_only() -> None:
    css = bootstrap_map_css(ride_tokens())
    assert css.startswith("@layer tokens{:root{--bs-")
    assert set(re.findall(r"(--[\w-]+):", css)) <= set(upstream_defaults())
    assert bootstrap_map_css(ride_tokens()) == css  # deterministic


def test_the_built_ride_css_carries_the_map_after_the_tokens(tmp_path: Path) -> None:
    css = ride_css(tmp_path)
    assert built_map(css)
    assert css.index("--color-text:") < css.index("--bs-primary:")
    assert css.index("--bs-primary:") > css.index("@layer tokens{")


def test_every_bootstrap_colour_variable_is_set_by_the_map() -> None:
    mapped = bootstrap_variables(ride_tokens())
    colours = {
        name
        for name, value in upstream_defaults().items()
        if COLOUR_VALUE.match(value) and not PALETTE.match(name)
    }
    assert colours, "the upstream :root parse found no colour variable"
    assert sorted(colours - set(mapped)) == []


def test_no_bootstrap_colour_variable_stays_at_its_upstream_default() -> None:
    mapped = bootstrap_variables(ride_tokens())
    same = []
    for name, value in upstream_defaults().items():
        if not COLOUR_VALUE.match(value) or PALETTE.match(name) or name in EQUAL_BY_DESIGN:
            continue
        if mapped[name].replace(" ", "").lower() == value.replace(" ", "").lower():
            same.append(name)
    assert same == []


def test_the_main_variables_come_from_the_tokens() -> None:
    tokens = ride_tokens()
    mapped = bootstrap_variables(tokens)
    assert mapped["--bs-primary"].lower() == tokens["color.accent"].lower()
    assert mapped["--bs-primary-rgb"] == "4,107,210"
    assert mapped["--bs-secondary"].lower() == tokens["color.secondary"].lower()
    assert mapped["--bs-body-color"].lower() == tokens["color.text"].lower()
    assert mapped["--bs-link-color"].lower() == tokens["color.link"].lower()
    assert mapped["--bs-danger"].lower() == tokens["color.danger"].lower()
    assert mapped["--bs-success"].lower() == tokens["color.success"].lower()
    assert mapped["--bs-body-font-family"] == "var(--font-body)"
    assert mapped["--bs-border-radius"] == "var(--radius-control)"
    assert "--bs-focus-ring-color" in mapped
    assert "--bs-box-shadow" in mapped


def test_the_map_follows_a_changed_token() -> None:
    tokens = ride_tokens()
    changed = {**tokens, "color.accent": "#0B6E4F"}
    mapped = bootstrap_variables(changed)
    assert mapped["--bs-primary"] == "#0B6E4F"
    assert mapped["--bs-primary-rgb"] == "11,110,79"
    assert mapped != bootstrap_variables(tokens)


def test_subtle_variants_are_pale_and_emphasis_variants_are_dark() -> None:
    from apps.themes.engine import contrast_ratio

    mapped = bootstrap_variables(ride_tokens())
    for family in ("primary", "secondary", "success", "info", "warning", "danger"):
        emphasis = mapped[f"--bs-{family}-text-emphasis"]
        subtle = mapped[f"--bs-{family}-bg-subtle"]
        assert contrast_ratio(emphasis, subtle) >= 4.5, family


def test_base_output_is_unchanged_and_has_no_map(tmp_path: Path) -> None:
    out = tmp_path / "build"
    build_themes(SHIPPED_THEMES, out)
    base = (out / "themes" / "base" / "theme.css").read_text(encoding="utf-8")
    assert "--bs-" not in base
    assert read_css_sources(BASE, "base")  # sanity: the sources were read


# --- 2. Per-theme budgets


def manifest(**extra: object) -> dict[str, object]:
    return {"slug": "ride", "name": "Ride", "version": "1.0.0", "parent": "base", **extra}


def test_budget_keys_are_parsed_as_kilobytes() -> None:
    parsed = parse_manifest(manifest(css_budget_kb=70, js_budget_kb=60), dirname="ride")
    assert (parsed.css_budget_kb, parsed.js_budget_kb) == (70, 60)
    plain = parse_manifest(manifest(), dirname="ride")
    assert (plain.css_budget_kb, plain.js_budget_kb) == (None, None)


@pytest.mark.parametrize("value", [0, -5, "70", 70.5, True, None])
@pytest.mark.parametrize("key", ["css_budget_kb", "js_budget_kb"])
def test_invalid_budget_values_are_rejected(key: str, value: object) -> None:
    with pytest.raises(ThemeError):
        parse_manifest(manifest(**{key: value}), dirname="ride")


def test_ride_declares_its_budgets_and_base_declares_none() -> None:
    registry = build_registry(read_manifest_sources(SHIPPED_THEMES))
    assert registry["ride"].css_budget_kb is not None
    assert registry["ride"].js_budget_kb is not None
    assert registry["base"].css_budget_kb is None
    assert registry["base"].js_budget_kb is None
    assert registry["ride"].css_budget_kb > CSS_BUDGET // 1024
    assert registry["ride"].js_budget_kb > JS_BUDGET // 1024


def with_budgets(root: Path, css: int | None, js: int | None) -> None:
    path = root / "ride" / "theme.toml"
    text = path.read_text(encoding="utf-8")
    if css is not None:
        text += f"css_budget_kb = {css}\n"
    if js is not None:
        text += f"js_budget_kb = {js}\n"
    path.write_text(text, encoding="utf-8")


def ride_failures(root: Path) -> list[str]:
    return [str(f) for f in run_gates(root) if f.theme == "ride"]


def test_a_bootstrap_theme_over_its_css_limit_fails(tmp_path: Path) -> None:
    root = tmp_path / "themes"
    foundation_theme(root)
    with_budgets(root, 20, 200)
    failures = ride_failures(root)
    assert any("theme.css" in f and "20.0 KB budget" in f for f in failures), failures


def test_a_bootstrap_theme_over_its_js_limit_fails(tmp_path: Path) -> None:
    root = tmp_path / "themes"
    foundation_theme(root)
    with_budgets(root, 200, 30)
    failures = ride_failures(root)
    assert any("JavaScript" in f and "30.0 KB budget" in f for f in failures), failures


def test_a_bootstrap_theme_within_generous_limits_passes(tmp_path: Path) -> None:
    root = tmp_path / "themes"
    foundation_theme(root)
    with_budgets(root, 200, 200)
    assert ride_failures(root) == []


def test_a_bootstrap_theme_without_budgets_gets_the_base_limits(tmp_path: Path) -> None:
    root = tmp_path / "themes"
    foundation_theme(root)
    failures = ride_failures(root)
    assert any("JavaScript" in f and "40.0 KB budget" in f for f in failures), failures


def test_a_theme_without_budgets_still_fails_over_45_kb_of_css(tmp_path: Path) -> None:
    root = tmp_path / "themes"
    write_theme(
        root,
        "base",
        tokens=(BASE / "tokens.toml").read_text(encoding="utf-8"),
        css={"10-big.css": incompressible_css(CSS_BUDGET + 2048)},
    )
    failures = [str(f) for f in run_gates(root) if f.theme == "base"]
    assert any("theme.css" in f and "45.0 KB budget" in f for f in failures), failures


def test_the_manifest_budget_replaces_the_default_for_a_framework_free_theme(
    tmp_path: Path,
) -> None:
    root = tmp_path / "themes"
    write_theme(
        root,
        "base",
        tokens=(BASE / "tokens.toml").read_text(encoding="utf-8"),
        css={"10-big.css": incompressible_css(CSS_BUDGET + 2048)},
        manifest=(
            'slug = "base"\nname = "Base"\nversion = "1.0.0"\ndirection = "ltr"\n'
            "css_budget_kb = 80\n"
        ),
    )
    assert [str(f) for f in run_gates(root) if f.theme == "base"] == []


def test_ride_is_within_its_declared_limits_with_little_headroom(tmp_path: Path) -> None:
    assert ride_failures(SHIPPED_THEMES) == []
    registry = build_registry(read_manifest_sources(SHIPPED_THEMES))
    css = compressed_size(ride_css(tmp_path).encode("utf-8"))
    limit = registry["ride"].css_budget_kb
    assert limit is not None
    assert css <= limit * 1024
    assert limit * 1024 - css <= 12 * 1024, "the limit is a ceiling, not a target: lower it"


def test_the_spec_records_the_final_limits() -> None:
    spec = (Path(__file__).resolve().parents[4] / "docs/spec/14-frontend-themes.md").read_text(
        encoding="utf-8"
    )
    registry = build_registry(read_manifest_sources(SHIPPED_THEMES))
    ride = registry["ride"]
    row = next(line for line in spec.splitlines() if line.startswith("| CSS per theme"))
    assert f"≤ {ride.css_budget_kb} KB" in row
    row = next(line for line in spec.splitlines() if line.startswith("| JavaScript total"))
    assert f"≤ {ride.js_budget_kb} KB" in row
    assert "provisional" not in row


# --- 3. CSP lint


@pytest.mark.parametrize(
    ("source", "finding"),
    [
        ('<button data-bs-toggle="tooltip" title="x">', "Bootstrap tooltip"),
        ("<button data-bs-toggle='tooltip'>", "Bootstrap tooltip"),
        ("<button data-bs-toggle=tooltip>", "Bootstrap tooltip"),
        ('<button data-bs-toggle="popover" data-bs-content="x">', "Bootstrap popover"),
        ('<a data-bs-toggle = "popover">', "Bootstrap popover"),
        ('<span data-bs-html="true">', "data-bs-html"),
        ("<span data-bs-html=true>", "data-bs-html"),
        ('<span data-bs-template="<div></div>">', "data-bs-template"),
        ("<span data-bs-template='<div></div>'>", "data-bs-template"),
    ],
)
def test_the_lint_rejects_tooltip_popover_html_and_template(source: str, finding: str) -> None:
    result = lint_template(source)
    assert result, source
    assert all(finding in line for line in result), result


@pytest.mark.parametrize(
    "source",
    [
        '<button data-bs-toggle="dropdown" aria-expanded="false">',
        '<button data-bs-toggle="modal" data-bs-target="#m">',
        '<button data-bs-toggle="collapse" data-bs-target="#c">',
        '<button data-bs-toggle="offcanvas" data-bs-target="#o">',
        '<a data-bs-toggle="tab" href="#t">',
        '<div class="carousel" data-bs-ride="carousel">',
        '<button data-bs-dismiss="modal">',
        '<p data-bs-tooltip-note="x">',
    ],
)
def test_the_lint_accepts_the_components_the_spec_allows(source: str) -> None:
    assert lint_template(source) == []


def test_the_lint_reports_the_line_of_a_bootstrap_finding() -> None:
    result = lint_template('<p>\n<a data-bs-toggle="popover">\n</p>')
    assert result == ["line 2: Bootstrap popover"]


# --- 4. Selector collisions


def test_class_names_reads_every_class_of_every_selector() -> None:
    css = (
        '/* .gone */ .a, .b > .c:hover{color:red}@media (min-width:1px){.d::before{content:".s"}}'
        "@layer x{.e .f[type=x]{margin:0}}.g\\:h{top:0}"
    )
    assert class_names(css) == {"a", "b", "c", "d", "e", "f", "g:h"}


def test_class_names_ignores_declaration_values_and_numbers() -> None:
    assert class_names(".x{width:1.5rem;background:url(a.b.svg)}") == {"x"}


def test_colliding_classes_is_the_intersection() -> None:
    assert colliding_classes(".a .b{}", ".b,.c{}") == {"b"}


def ride_base_css() -> str:
    return "\n".join(read_css_sources(BASE, "base"))


def test_base_and_bootstrap_share_no_class_without_a_recorded_reason() -> None:
    overlap = colliding_classes(ride_base_css(), upstream_css())
    assert sorted(overlap - set(ALLOWED_COLLISIONS)) == []


def test_every_allowed_collision_is_real_and_has_a_reason() -> None:
    overlap = colliding_classes(ride_base_css(), upstream_css())
    assert sorted(set(ALLOWED_COLLISIONS) - overlap) == [], "stale entry: remove it"
    for name, reason in ALLOWED_COLLISIONS.items():
        assert len(reason.split()) >= 4, name


def test_the_vendored_css_is_the_one_the_collision_test_reads() -> None:
    assert (VENDORED / "bootstrap.min.css").is_file()
    assert BOOTSTRAP_DIR == "vendor/bootstrap"


# --- 5. The script tag and the served bundle


LAYOUT = RIDE / "templates" / "layouts" / "base.html"
BASE_LAYOUT = BASE / "templates" / "layouts" / "base.html"


def test_the_ride_layout_loads_the_bundle_as_a_deferred_classic_script() -> None:
    source = LAYOUT.read_text(encoding="utf-8")
    tags = re.findall(r"<script\b[^>]*bootstrap\.bundle[^>]*>", source)
    assert len(tags) == 1
    assert " defer" in tags[0]
    assert 'type="module"' not in tags[0]
    assert "{% static 'themes/ride/vendor/bootstrap.bundle.min.js' %}" in tags[0]
    assert "integrity" not in tags[0]
    assert "crossorigin" not in tags[0]
    assert lint_template(source) == []


def test_the_bundle_loads_before_the_theme_modules_and_base_has_no_bundle() -> None:
    base = BASE_LAYOUT.read_text(encoding="utf-8")
    assert "bootstrap" not in base
    assert "{% block foundation_scripts %}{% endblock %}" in base
    assert base.index("foundation_scripts") < base.index("themes/base/js/base.js")
    assert "{% block foundation_scripts %}" in LAYOUT.read_text(encoding="utf-8")


def test_the_bundle_is_served_and_within_the_js_budget(
    tmp_path: Path, settings: SettingsWrapper
) -> None:
    out = tmp_path / "build"
    build_themes(SHIPPED_THEMES, out)
    settings.MPMV_THEME_BUILD_DIR = out
    finder = get_finder("apps.themes.finders.ThemeAssetsFinder")
    found = finder.find("themes/ride/vendor/bootstrap.bundle.min.js")
    assert found
    registry = build_registry(read_manifest_sources(SHIPPED_THEMES))
    limit = registry["ride"].js_budget_kb
    assert limit is not None
    assert compressed_size(Path(str(found)).read_bytes()) < limit * 1024
