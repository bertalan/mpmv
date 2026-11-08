"""THM.5: theme quality gates over the registered themes (spec 14 §14.7, §14.8, §14.11).

`run_gates` checks every theme under a themes directory: the token contract (contrast pairs),
the CSP lint of the theme's own templates, the size budgets of the built CSS and the JavaScript
the pages load, the font budget, and `LICENSES.toml` for every font, icon and image. Done when a
theme with a 3:1 text pair, an inline style, an unlicensed font or a 50 KB CSS file fails, and
`base` passes. `python -m apps.themes.gates` runs the same gates in `make check` and CI.
"""

import gzip
import hashlib
import shutil
from pathlib import Path

import pytest

from apps.themes.build import read_manifest_sources
from apps.themes.engine import build_registry
from apps.themes.gates import (
    ALLOWED_ASSET_LICENCES,
    CSS_BUDGET,
    JS_BUDGET,
    LICENCES_FILE,
    MAX_FONT_FILES,
    compressed_size,
    main,
    run_gates,
)
from apps.themes.tests.builders import SHIPPED_THEMES, write_theme

REGISTERED = sorted(build_registry(read_manifest_sources(SHIPPED_THEMES)).manifests)
WOFF2 = b"wOF2" + bytes(64)  # a stand-in font file: only its name and licence matter here

FONT_LICENCE = """
[[asset]]
name = "Display Sans"
kind = "font"
source = "https://example.org/display-sans"
author = "Example Foundry"
license = "OFL-1.1"
files = ["fonts/display.woff2"]
"""


def incompressible_css(compressed_bytes: int) -> str:
    """A CSS source whose gzip size is at least `compressed_bytes`: hashed class names."""
    rules: list[str] = []
    digest = b"thm5"
    while compressed_size("".join(rules).encode()) < compressed_bytes:
        for _ in range(200):
            digest = hashlib.sha256(digest).digest()
            rules.append(f".x{digest.hex()}{{color:var(--color-text)}}\n")
    return "@layer components {\n" + "".join(rules) + "}\n"


@pytest.fixture
def themes(tmp_path: Path) -> Path:
    """A themes directory with a copy of the shipped `base`, ready for one more test theme."""
    root = tmp_path / "themes"
    root.mkdir()
    shutil.copytree(SHIPPED_THEMES / "base", root / "base")
    return root


def add_asset(theme_dir: Path, relative: str, data: bytes = WOFF2) -> None:
    path = theme_dir / "static" / "themes" / theme_dir.name / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def failures_of(root: Path, slug: str) -> list[str]:
    return [str(failure) for failure in run_gates(root) if failure.theme == slug]


# --- Every registered theme passes


@pytest.mark.parametrize("slug", REGISTERED)
def test_every_registered_theme_passes_the_gates(slug: str) -> None:
    failures = [failure for failure in run_gates(SHIPPED_THEMES) if failure.theme == slug]
    assert failures == [], "\n".join(map(str, failures))


def test_base_is_registered_and_gated() -> None:
    assert "base" in REGISTERED


def test_the_gates_command_passes_on_the_shipped_themes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--themes-dir", str(SHIPPED_THEMES)]) == 0
    assert "base" in capsys.readouterr().out


# --- The done-when: each fixture theme fails its gate, base still passes


def theme_with_a_3_to_1_text_pair(root: Path) -> None:
    # #949494 on the white surface is 3.03:1, below the 4.5:1 that text needs.
    write_theme(root, "pale", tokens='[color]\ntext-muted = "#949494"\n')


def theme_with_an_inline_style(root: Path) -> None:
    write_theme(
        root, "inline", templates={"components/hero.html": '<div style="color:red">x</div>'}
    )


def theme_with_an_unlicensed_font(root: Path) -> None:
    add_asset(write_theme(root, "unlicensed"), "fonts/display.woff2")


def theme_with_a_50_kb_css_file(root: Path) -> None:
    write_theme(root, "heavy", css={"50-heavy.css": incompressible_css(50 * 1024)})


@pytest.mark.parametrize(
    ("make_theme", "slug", "gate", "detail"),
    [
        (theme_with_a_3_to_1_text_pair, "pale", "tokens", "text-muted"),
        (theme_with_an_inline_style, "inline", "csp", "style attribute"),
        (theme_with_an_unlicensed_font, "unlicensed", "licences", "fonts/display.woff2"),
        (theme_with_a_50_kb_css_file, "heavy", "size", "theme.css"),
    ],
    ids=["3-to-1-text-pair", "inline-style", "unlicensed-font", "50-kb-css"],
)
def test_a_theme_that_breaks_a_gate_fails_and_base_still_passes(
    themes: Path, make_theme: object, slug: str, gate: str, detail: str
) -> None:
    assert callable(make_theme)
    make_theme(themes)
    failures = failures_of(themes, slug)
    assert any(f"[{gate}]" in failure and detail in failure for failure in failures), failures
    assert failures_of(themes, "base") == []
    assert main(["--themes-dir", str(themes)]) == 1


# --- Size budgets (§14.7)


def test_the_budgets_are_the_spec_values() -> None:
    assert CSS_BUDGET == 45 * 1024
    assert JS_BUDGET == 40 * 1024
    assert MAX_FONT_FILES == 4


def test_compressed_size_is_deterministic_gzip() -> None:
    data = b"a{color:red}" * 100
    assert compressed_size(data) == compressed_size(data)
    assert compressed_size(data) <= len(gzip.compress(data, compresslevel=1))
    assert compressed_size(data) < len(data)


def test_a_css_file_just_under_the_budget_passes(themes: Path) -> None:
    write_theme(themes, "light", css={"50-light.css": incompressible_css(30 * 1024)})
    assert [f for f in failures_of(themes, "light") if "[size]" in f] == []


def test_javascript_over_the_budget_fails(themes: Path) -> None:
    theme_dir = write_theme(themes, "scripted")
    js = theme_dir / "static" / "themes" / "scripted" / "js" / "big.js"
    js.parent.mkdir(parents=True)
    js.write_text(incompressible_css(JS_BUDGET), encoding="utf-8")
    failures = failures_of(themes, "scripted")
    assert any("[size]" in f and "JavaScript" in f for f in failures), failures


def test_more_than_four_font_files_fail(themes: Path) -> None:
    theme_dir = write_theme(themes, "fonty")
    names = [f"fonts/display-{weight}.woff2" for weight in (300, 400, 500, 600, 700)]
    for name in names:
        add_asset(theme_dir, name)
    licence = FONT_LICENCE.replace('["fonts/display.woff2"]', repr(names).replace("'", '"'))
    (theme_dir / LICENCES_FILE).write_text(licence, encoding="utf-8")
    failures = failures_of(themes, "fonty")
    assert any("[fonts]" in f and "woff2" in f for f in failures), failures


def test_a_font_that_is_not_woff2_fails(themes: Path) -> None:
    theme_dir = write_theme(themes, "ttf")
    add_asset(theme_dir, "fonts/display.ttf")
    licence = FONT_LICENCE.replace("display.woff2", "display.ttf")
    (theme_dir / LICENCES_FILE).write_text(licence, encoding="utf-8")
    failures = failures_of(themes, "ttf")
    assert any("[fonts]" in f and "display.ttf" in f for f in failures), failures


def test_more_than_two_font_families_fail(themes: Path) -> None:
    theme_dir = write_theme(themes, "families")
    entries = []
    for family in ("one", "two", "three"):
        add_asset(theme_dir, f"fonts/{family}.woff2")
        entries.append(
            FONT_LICENCE.replace("Display Sans", family).replace("display.woff2", f"{family}.woff2")
        )
    (theme_dir / LICENCES_FILE).write_text("".join(entries), encoding="utf-8")
    failures = failures_of(themes, "families")
    assert any("[fonts]" in f and "families" in f for f in failures), failures


# --- LICENSES.toml (§14.8)


def test_a_licensed_font_passes(themes: Path) -> None:
    theme_dir = write_theme(themes, "licensed")
    add_asset(theme_dir, "fonts/display.woff2")
    (theme_dir / LICENCES_FILE).write_text(FONT_LICENCE, encoding="utf-8")
    assert failures_of(themes, "licensed") == []


@pytest.mark.parametrize(
    ("licence", "asset", "detail"),
    [
        (
            FONT_LICENCE.replace("OFL-1.1", "LicenseRef-Commercial"),
            "fonts/display.woff2",
            "licence",
        ),
        (FONT_LICENCE.replace("display.woff2", "other.woff2"), "fonts/display.woff2", "no entry"),
        (FONT_LICENCE, None, "missing"),
        (
            FONT_LICENCE.replace('source = "https://example.org/display-sans"\n', ""),
            "fonts/display.woff2",
            "source",
        ),
        (FONT_LICENCE.replace('author = "Example Foundry"\n', ""), "fonts/display.woff2", "author"),
        ("[[asset]\n", "fonts/display.woff2", "cannot be read"),
    ],
    ids=[
        "licence-not-allowed",
        "file-not-covered",
        "listed-but-missing",
        "no-source",
        "no-author",
        "invalid-toml",
    ],
)
def test_a_licence_problem_fails(
    themes: Path, licence: str, asset: str | None, detail: str
) -> None:
    theme_dir = write_theme(themes, "assets")
    if asset is not None:
        add_asset(theme_dir, asset)
    (theme_dir / LICENCES_FILE).write_text(licence, encoding="utf-8")
    failures = failures_of(themes, "assets")
    assert any("[licences]" in f and detail in f for f in failures), failures


@pytest.mark.parametrize("relative", ["icons/sprite.svg", "img/placeholder.webp"])
def test_icons_and_images_need_a_licence_entry_too(themes: Path, relative: str) -> None:
    add_asset(write_theme(themes, "pictures"), relative, b"<svg/>")
    failures = failures_of(themes, "pictures")
    assert any("[licences]" in f and relative in f for f in failures), failures


def test_the_allowed_licences_are_the_spec_list() -> None:
    assert frozenset({"OFL-1.1", "Apache-2.0", "MIT", "ISC", "CC0-1.0"}) == ALLOWED_ASSET_LICENCES


def test_css_and_js_sources_are_not_assets(themes: Path) -> None:
    write_theme(
        themes, "plain", css={"50-plain.css": "@layer components{.a{color:var(--color-text)}}"}
    )
    assert failures_of(themes, "plain") == []


# --- An invalid theme is reported, not skipped


def test_an_invalid_manifest_fails(themes: Path) -> None:
    write_theme(themes, "broken", manifest='slug = "broken"\n')
    assert any("broken" in str(failure) for failure in run_gates(themes))
