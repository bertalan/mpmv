"""THM.13 part 1 of 3: the Bootstrap 5.3 foundation of `ride` (spec 14 §14.5).

The vendored dist files are unchanged and hash-checked; a theme declares
`foundation = "bootstrap-5.3"`; the build wraps the CSS in `@layer bootstrap`, adds the
drops `base`'s reset, and replaces every `data:` URI with a same-origin SVG file. The token map,
per-theme budgets, the CSP lint and the collision test are part 2.
"""

import re
import shutil
from pathlib import Path

import pytest
from django.core import checks
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.themes.build import build_themes, read_manifest_sources, read_tokens
from apps.themes.checks import check_themes
from apps.themes.engine import (
    CSS_LAYERS,
    ThemeError,
    build_registry,
    parse_manifest,
    resolve_tokens,
)
from apps.themes.foundation import (
    BOOTSTRAP_DIR,
    foundation_problems,
)
from apps.themes.gates import run_gates
from apps.themes.tests.builders import SHIPPED_THEMES, write_theme
from apps.themes.vendored import MANIFEST, check_vendored, read_vendored

RIDE = SHIPPED_THEMES / "ride"
VENDORED = RIDE / "static" / "themes" / "ride" / BOOTSTRAP_DIR


def upstream_css() -> str:
    return (VENDORED / "bootstrap.min.css").read_text(encoding="utf-8")


def ride_css(tmp_path: Path) -> str:
    out = tmp_path / "build"
    build_themes(SHIPPED_THEMES, out)
    return (out / "themes" / "ride" / "theme.css").read_text(encoding="utf-8")


# --- Vendored files


def test_the_vendored_bootstrap_files_match_their_recorded_hashes() -> None:
    assert check_vendored(VENDORED) == []
    entries = {Path(e.path).name: e for e in read_vendored(VENDORED)}
    assert {"bootstrap.min.css", "bootstrap.bundle.min.js", "LICENSE"} <= set(entries)
    assert all(e.name == "bootstrap" and e.license == "MIT" for e in entries.values())
    assert entries["bootstrap.min.css"].version.startswith("5.3.")


def test_a_tampered_bootstrap_file_fails(tmp_path: Path) -> None:
    copy = tmp_path / "bootstrap"
    shutil.copytree(VENDORED, copy)
    with (copy / "bootstrap.min.css").open("a", encoding="utf-8") as handle:
        handle.write(".x{color:red}")
    errors = check_vendored(copy)
    assert any("bootstrap.min.css" in e and "SHA-256" in e for e in errors), errors


def test_the_upstream_licence_text_ships_next_to_the_files() -> None:
    text = (VENDORED / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in text
    assert "The Bootstrap Authors" in text


def test_there_are_no_source_maps_and_no_other_files() -> None:
    assert sorted(p.name for p in VENDORED.iterdir()) == [
        "LICENSE",
        MANIFEST,
        "bootstrap.bundle.min.js",
        "bootstrap.min.css",
    ]


def test_the_licences_file_lists_bootstrap_as_a_framework() -> None:
    import tomllib

    data = tomllib.loads((RIDE / "LICENSES.toml").read_text(encoding="utf-8"))
    entries = [a for a in data["asset"] if a["name"] == "Bootstrap"]
    assert len(entries) == 1
    assert entries[0]["kind"] == "framework"
    assert entries[0]["license"] == "MIT"


# --- Manifest key and system check


def manifest(**extra: object) -> dict[str, object]:
    return {"slug": "ride", "name": "Ride", "version": "1.0.0", "parent": "base", **extra}


def test_the_foundation_key_is_parsed() -> None:
    parsed = parse_manifest(manifest(foundation="bootstrap-5.3"), dirname="ride")
    assert parsed.foundation == "bootstrap-5.3"
    assert parse_manifest(manifest(), dirname="ride").foundation is None


@pytest.mark.parametrize(
    "extra",
    [
        {"foundation": "bootstrap-4"},
        {"foundation": ""},
        {"foundation": 5},
    ],
)
def test_invalid_foundation_values_are_rejected(extra: dict[str, object]) -> None:
    with pytest.raises(ThemeError):
        parse_manifest(manifest(**extra), dirname="ride")


def test_ride_declares_the_foundation_and_base_does_not() -> None:
    registry = build_registry(read_manifest_sources(SHIPPED_THEMES))
    assert registry["ride"].foundation == "bootstrap-5.3"
    assert registry["base"].foundation is None


def foundation_theme(root: Path, *, vendored: bool = True) -> None:
    write_theme(root, "base", tokens=(SHIPPED_THEMES / "base" / "tokens.toml").read_text("utf-8"))
    write_theme(
        root,
        "ride",
        manifest=(
            'slug = "ride"\nname = "Ride"\nversion = "1.0.0"\nparent = "base"\n'
            'foundation = "bootstrap-5.3"\n'
        ),
    )
    if vendored:
        shutil.copytree(VENDORED, root / "ride" / "static" / "themes" / "ride" / BOOTSTRAP_DIR)


def test_the_system_check_passes_for_the_shipped_themes() -> None:
    assert check_themes(None) == []


def test_the_system_check_fails_on_missing_or_tampered_vendored_files(
    tmp_path: Path, settings: SettingsWrapper
) -> None:
    root = tmp_path / "themes"
    foundation_theme(root, vendored=False)
    settings.MPMV_THEMES_DIR = root
    settings.MPMV_THEME_DEFAULT = "base"
    messages: list[checks.CheckMessage] = check_themes(None)
    assert [m.id for m in messages] == ["themes.E005"]
    assert "ride" in messages[0].msg
    foundation_theme_dir = root / "ride" / "static" / "themes" / "ride" / BOOTSTRAP_DIR
    shutil.copytree(VENDORED, foundation_theme_dir)
    assert check_themes(None) == []
    (foundation_theme_dir / "bootstrap.bundle.min.js").write_text("alert(1)", encoding="utf-8")
    assert [m.id for m in check_themes(None)] == ["themes.E005"]
    assert foundation_problems(root / "ride", "ride", "bootstrap-5.3")


# --- Build: layers, reset, data URIs


def test_ride_css_wraps_bootstrap_in_its_own_layer_with_the_spec_layer_order(
    tmp_path: Path,
) -> None:
    css = ride_css(tmp_path)
    assert css.startswith(
        "@layer reset,bootstrap,tokens,base,layout,components,utilities,overrides;"
    )
    assert "@layer bootstrap{" in css
    assert css.index("@layer bootstrap{") < css.index("@layer tokens{")
    assert "@charset" not in css


def test_ride_chain_drops_the_base_reset_layer(tmp_path: Path) -> None:
    css = ride_css(tmp_path)
    assert "@layer reset{" not in css
    assert "box-sizing:border-box;}" not in css.split("@layer bootstrap{")[0]


def test_the_built_ride_css_has_no_data_uri_and_uses_same_origin_svg_files(
    tmp_path: Path,
) -> None:
    css = ride_css(tmp_path)
    assert "url(data:" not in css
    assert 'url("data:' not in css
    assert "url('data:" not in css
    assert "data:image" not in css
    refs = set(re.findall(r'url\("?(svg/bs-[0-9a-f]+\.svg)"?\)', css))
    assert refs, "the navbar toggler, close button and other marks need files"
    for ref in refs:
        svg = tmp_path / "build" / "themes" / "ride" / ref
        text = svg.read_text(encoding="utf-8")
        assert text.startswith("<svg")
        assert "xmlns=" in text
        assert "data:" not in text


def test_base_output_is_unchanged_by_the_foundation_code(tmp_path: Path) -> None:
    from apps.themes.build import read_css_sources
    from apps.themes.engine import minify_css, render_tokens_css

    out = tmp_path / "build"
    build_themes(SHIPPED_THEMES, out)
    built = (out / "themes" / "base" / "theme.css").read_text(encoding="utf-8")
    tokens = resolve_tokens(("base",), {"base": read_tokens(SHIPPED_THEMES / "base")})
    parts = [f"@layer {', '.join(CSS_LAYERS)};", render_tokens_css(tokens)]
    parts.extend(read_css_sources(SHIPPED_THEMES / "base", "base"))
    assert built == minify_css("\n".join(parts)) + "\n"
    assert "bootstrap" not in built
    assert "--bs-" not in built
    assert not (out / "themes" / "base" / "svg").exists()
    assert not (out / "themes" / "base" / "vendor").exists()


def test_the_bundle_is_copied_to_the_build_for_ride_only(tmp_path: Path) -> None:
    out = tmp_path / "build"
    build_themes(SHIPPED_THEMES, out)
    copied = out / "themes" / "ride" / "vendor" / "bootstrap.bundle.min.js"
    upstream = (VENDORED / "bootstrap.bundle.min.js").read_text(encoding="utf-8")
    # The copy is upstream minus its sourceMappingURL comment (no .map is vendored).
    kept = [line for line in upstream.splitlines() if "sourceMappingURL" not in line]
    assert copied.read_text(encoding="utf-8").splitlines() == kept


def test_ride_passes_the_gates_with_bootstrap_inside() -> None:
    """The provisional Bootstrap-theme budgets of §14.7 (75 KB each) hold; part 2 makes them
    per-theme manifest keys."""
    assert [str(f) for f in run_gates(SHIPPED_THEMES) if f.theme == "ride"] == []


def test_the_vendored_framework_files_are_not_touched_by_the_commit_hooks() -> None:
    """Upstream's minified files end without a newline: a hook that adds one breaks the hash."""
    from pathlib import Path

    folder = Path(__file__).resolve().parents[4] / "themes/ride/static/themes/ride/vendor/bootstrap"
    for name in ("bootstrap.min.css", "bootstrap.bundle.min.js"):
        assert not (folder / name).read_bytes().endswith(b"\n"), name
    hooks = (Path(__file__).resolve().parents[4] / ".pre-commit-config.yaml").read_text()
    assert hooks.count("themes/[^/]+/static/themes/[^/]+/vendor") == 2


def test_the_built_framework_has_no_source_map_reference() -> None:
    """No `.map` is vendored, and the manifest storage stops collectstatic on a missing file."""
    from apps.themes.foundation import foundation_files

    root = Path(__file__).resolve().parents[4] / "themes" / "ride"
    css, js = foundation_files(root, "ride", "bootstrap-5.3")
    assert "sourceMappingURL" not in css
    assert "sourceMappingURL" not in js
