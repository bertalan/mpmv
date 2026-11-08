"""THM.2: the theme CSS build (spec 14 §14.5, ADR-0008) and the shipped token sets.

`python -m apps.themes.build` (`make assets`) resolves each theme's tokens along its chain,
checks them against the token contract, and writes `themes/<slug>/tokens.css` and
`themes/<slug>/theme.css` under the build directory. `ThemeAssetsFinder` serves that directory
to `collectstatic`, whose manifest storage hashes `theme.css`.
"""

import json
import re
from pathlib import Path

import pytest
from django.contrib.staticfiles.finders import get_finder
from django.core.management import call_command
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.themes.build import BuildError, build_themes, main, read_tokens
from apps.themes.contract import TOKEN_CONTRACT
from apps.themes.engine import ContrastPair, check_tokens, resolve_tokens
from apps.themes.finders import ThemeAssetsFinder
from apps.themes.tests.builders import SHIPPED_THEMES, shipped_tokens, write_theme

ROOT = Path(__file__).resolve().parents[4]
FINDER = "apps.themes.finders.ThemeAssetsFinder"

RESET = "/* reset */\n@layer reset {\n  *, *::before { box-sizing: border-box; }\n}\n"
HERO = "@layer components {\n  .hero { color: var(--color-accent); }\n}\n"


@pytest.fixture
def themes(tmp_path: Path) -> Path:
    root = tmp_path / "themes"
    write_theme(root, "base", tokens=shipped_tokens(), css={"reset.css": RESET})
    write_theme(
        root,
        "alt",
        tokens='[color]\naccent = "#046BD2"\naccent-hover = "#035AB1"\n',
        css={"hero.css": HERO},
    )
    write_theme(root, "calm")  # no tokens, no CSS: inherits everything from base
    return root


def built(out: Path, slug: str, name: str = "theme.css") -> str:
    return (out / "themes" / slug / name).read_text(encoding="utf-8")


# --- The contract and the shipped themes


def test_the_contract_holds_the_spec_pairs() -> None:
    pairs = {(p.foreground, p.background): p.minimum for p in TOKEN_CONTRACT.pairs}
    assert pairs[("color.text", "color.surface")] == 4.5
    assert pairs[("color.on-accent", "color.accent")] == 4.5
    assert pairs[("color.on-secondary", "color.secondary-hover")] == 4.5
    assert pairs[("color.text-inverse", "color.surface-inverse")] == 4.5
    assert pairs[("color.danger", "color.surface")] == 4.5
    assert pairs[("color.focus", "color.surface-inverse")] == 3.0
    assert pairs[("color.border-control", "color.surface")] == 3.0
    assert pairs[("color.accent", "color.surface")] == 3.0
    assert len(TOKEN_CONTRACT.pairs) == 15
    for pair in TOKEN_CONTRACT.pairs:
        assert pair.foreground in TOKEN_CONTRACT.required
        assert pair.background in TOKEN_CONTRACT.required
    for name in ("font.body", "step.0", "step.5", "space.xs", "space.3xl", "header.height"):
        assert name in TOKEN_CONTRACT.required


SHIPPED = sorted(p.name for p in SHIPPED_THEMES.iterdir() if (p / "theme.toml").is_file())


@pytest.mark.parametrize("slug", SHIPPED)
def test_every_shipped_theme_meets_the_token_contract(slug: str) -> None:
    chain = [slug] if slug == "base" else [slug, "base"]
    tokens = resolve_tokens(chain, {s: read_tokens(SHIPPED_THEMES / s) for s in chain})
    assert check_tokens(tokens, TOKEN_CONTRACT) == []


def test_the_contract_rejects_a_base_with_a_weak_pair() -> None:
    tokens = resolve_tokens(["base"], {"base": read_tokens(SHIPPED_THEMES / "base")})
    weak = {**tokens, "color.text-muted": "#999999"}  # 2.85:1 on white
    (violation,) = check_tokens(weak, TOKEN_CONTRACT)
    assert violation.token == "color.text-muted/color.surface"
    assert isinstance(TOKEN_CONTRACT.pairs[0], ContrastPair)


# --- build_themes


def test_every_theme_gets_tokens_css_and_theme_css(themes: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert build_themes(themes, out) == ["alt", "base", "calm"]
    for slug in ("alt", "base", "calm"):
        assert built(out, slug, "tokens.css").startswith("@layer tokens{:root{")
        css = built(out, slug)
        assert css.startswith("@layer reset,tokens,base,layout,components,utilities,overrides;")
        assert "box-sizing:border-box" in css  # base sources reach every theme
        assert "/* reset */" not in css  # minified
    assert "--color-accent:#046BD2;" in built(out, "alt")
    assert ".hero{color:var(--color-accent)}" in built(out, "alt")
    assert ".hero" not in built(out, "calm")
    assert built(out, "calm", "tokens.css") == built(out, "base", "tokens.css")


def test_a_theme_with_a_failing_pair_stops_the_build(themes: Path, tmp_path: Path) -> None:
    (themes / "alt" / "tokens.toml").write_text('[color]\naccent = "#FBA926"\n', encoding="utf-8")
    out = tmp_path / "out"
    with pytest.raises(BuildError) as raised:
        build_themes(themes, out)
    message = str(raised.value)
    assert "alt" in message
    assert "color.on-accent/color.accent" in message
    assert not (out / "themes").exists()


def test_an_invalid_tokens_file_stops_the_build(themes: Path, tmp_path: Path) -> None:
    (themes / "calm" / "tokens.toml").write_text("[color\n", encoding="utf-8")
    with pytest.raises(BuildError, match="calm"):
        build_themes(themes, tmp_path / "out")


def test_an_invalid_manifest_stops_the_build(themes: Path, tmp_path: Path) -> None:
    write_theme(themes, "orphan", parent="nowhere")
    with pytest.raises(BuildError, match="orphan"):
        build_themes(themes, tmp_path / "out")


def test_theme_js_modules_are_copied_next_to_the_css(themes: Path, tmp_path: Path) -> None:
    # THM.3: theme behaviour lives in ES modules under static/themes/<slug>/js/.
    module = themes / "alt" / "static" / "themes" / "alt" / "js" / "menu.js"
    module.parent.mkdir(parents=True)
    module.write_text('document.addEventListener("alpine:init", () => {});\n', encoding="utf-8")
    out = tmp_path / "out"
    build_themes(themes, out)
    assert built(out, "alt", "js/menu.js") == module.read_text(encoding="utf-8")
    assert not (out / "themes" / "base" / "js" / "menu.js").exists()


def test_stale_output_of_a_removed_theme_is_deleted(themes: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    stale = out / "themes" / "gone" / "theme.css"
    stale.parent.mkdir(parents=True)
    stale.write_text("old", encoding="utf-8")
    keep = out / "other.txt"
    keep.write_text("kept", encoding="utf-8")
    build_themes(themes, out)
    assert not stale.exists()
    assert keep.exists()


def test_main_reports_success_and_failure(
    themes: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    assert main(["--themes-dir", str(themes), "--out", str(out)]) == 0
    assert "base" in capsys.readouterr().out
    (themes / "alt" / "tokens.toml").write_text('[color]\ntext = "#BBBBBB"\n', encoding="utf-8")
    assert main(["--themes-dir", str(themes), "--out", str(out)]) == 1
    assert "color.text/color.surface" in capsys.readouterr().err


# --- Static files: the finder and the hashed output


def test_the_finder_serves_the_build_directory(
    themes: Path, tmp_path: Path, settings: SettingsWrapper
) -> None:
    out = tmp_path / "out"
    settings.MPMV_THEME_BUILD_DIR = out
    finder = ThemeAssetsFinder()
    assert finder.find("themes/base/theme.css") is None
    assert list(finder.list([])) == []
    assert finder.check() == []  # a missing build directory is not an error before the build
    build_themes(themes, out)
    assert finder.find("themes/base/theme.css") == str(out / "themes" / "base" / "theme.css")
    assert finder.find("themes/base/theme.css", find_all=True) == [
        str(out / "themes" / "base" / "theme.css")
    ]
    assert finder.find("../etc/passwd") is None
    listed = sorted(path for path, _ in finder.list([]))
    assert "themes/alt/theme.css" in listed
    assert "themes/alt/tokens.css" in listed


def test_the_project_uses_the_finder() -> None:
    from django.conf import settings

    assert FINDER in settings.STATICFILES_FINDERS
    assert isinstance(get_finder(FINDER), ThemeAssetsFinder)


def test_collectstatic_hashes_theme_css_for_every_theme(
    themes: Path, tmp_path: Path, settings: SettingsWrapper
) -> None:
    out = tmp_path / "out"
    build_themes(themes, out)
    settings.MPMV_THEME_BUILD_DIR = out
    settings.STATIC_ROOT = tmp_path / "static"
    settings.STATICFILES_FINDERS = [FINDER]
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "apps.cms.staticfiles.StaticFilesStorage"},
    }
    call_command("collectstatic", "--noinput", verbosity=0)
    manifest = json.loads((tmp_path / "static" / "staticfiles.json").read_text())["paths"]
    for slug in ("alt", "base", "calm"):
        hashed = manifest[f"themes/{slug}/theme.css"]
        assert re.fullmatch(rf"themes/{slug}/theme\.[0-9a-f]{{12}}\.css", hashed)
        assert (tmp_path / "static" / hashed).is_file()


# --- Wiring: make assets and the image build


def test_make_assets_runs_the_build() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^assets:.*##", makefile, re.MULTILINE)
    assert "python -m apps.themes.build" in makefile


def test_the_image_builds_the_themes_before_collectstatic() -> None:
    containerfile = (ROOT / "Containerfile").read_text(encoding="utf-8")
    build_stage = containerfile.split(" AS runtime", 1)[0]
    assert "-m apps.themes.build" in build_stage
    assert build_stage.index("-m apps.themes.build") < build_stage.index("manage.py collectstatic")
