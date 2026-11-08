"""Theme CSS build (THM.2, spec 14 §14.5, ADR-0008): `python -m apps.themes.build`.

For every registered theme: resolve its tokens along the chain, check them against the token
contract, and write under the build directory (`MPMV_THEME_BUILD_DIR`, default `build/themes`)

    themes/<slug>/tokens.css   the resolved tokens in `@layer tokens`
    themes/<slug>/theme.css    layer order + tokens + the chain's CSS sources, minified
    themes/<slug>/js/...       the theme's ES modules (THM.3), copied as they are

CSS sources are `themes/<slug>/static/themes/<slug>/css/*.css`, in file-name order. Any invalid
manifest, invalid `tokens.toml` or contract violation stops the build before anything is
written. `ThemeAssetsFinder` serves the output to `collectstatic`, which hashes it.

This module does I/O but needs no Django settings, so it runs in the image build stage
(`make assets` locally) before `collectstatic`.
"""

import argparse
import shutil
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path

from .contract import TOKEN_CONTRACT
from .engine import (
    MANIFEST_FILE,
    TOKENS_FILE,
    ThemeError,
    TokenContract,
    build_registry,
    build_theme_css,
    check_tokens,
    parse_tokens,
    render_tokens_css,
    resolve_tokens,
)

PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_THEMES_DIR = PROJECT_DIR / "themes"
DEFAULT_OUT_DIR = PROJECT_DIR / "build" / "themes"  # = settings.MPMV_THEME_BUILD_DIR
OUTPUT_PREFIX = "themes"  # static path prefix: themes/<slug>/theme.css


class BuildError(Exception):
    """The themes cannot be built; `errors` lists every reason."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("\n".join(self.errors))


def read_manifest_sources(root: Path) -> dict[str, Mapping[str, object] | ThemeError]:
    """Each theme directory under `root` -> its decoded `theme.toml`, or why it is unreadable."""
    sources: dict[str, Mapping[str, object] | ThemeError] = {}
    if not root.is_dir():
        return sources
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.name.startswith((".", "_")):
            continue
        manifest = path / MANIFEST_FILE
        try:
            sources[path.name] = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except FileNotFoundError:
            sources[path.name] = ThemeError(f"{MANIFEST_FILE} is missing")
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            sources[path.name] = ThemeError(f"{MANIFEST_FILE} cannot be read: {exc}")
    return sources


def read_tokens(theme_dir: Path) -> dict[str, str]:
    """The tokens a theme sets (`{}` without a `tokens.toml`); ThemeError if it is invalid."""
    path = theme_dir / TOKENS_FILE
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ThemeError(f"{TOKENS_FILE} cannot be read: {exc}") from exc
    try:
        return parse_tokens(data)
    except ThemeError as exc:
        raise ThemeError(f"{TOKENS_FILE}: {exc}") from exc


def read_css_sources(theme_dir: Path, slug: str) -> list[str]:
    """The theme's CSS sources, in file-name order."""
    css_dir = theme_dir / "static" / OUTPUT_PREFIX / slug / "css"
    if not css_dir.is_dir():
        return []
    return [path.read_text(encoding="utf-8") for path in sorted(css_dir.glob("*.css"))]


def read_js_modules(theme_dir: Path, slug: str) -> dict[str, str]:
    """The theme's ES modules under `static/themes/<slug>/js/`, by path relative to `js/`."""
    js_dir = theme_dir / "static" / OUTPUT_PREFIX / slug / "js"
    if not js_dir.is_dir():
        return {}
    return {
        path.relative_to(js_dir).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(js_dir.rglob("*.js"))
    }


def render_themes(themes_root: Path, contract: TokenContract = TOKEN_CONTRACT) -> dict[str, str]:
    """Every output file (relative path -> CSS) of every theme; BuildError on any problem."""
    registry = build_registry(read_manifest_sources(themes_root))
    errors = list(registry.errors)
    tokens_by_theme: dict[str, dict[str, str]] = {}
    sources: dict[str, list[str]] = {}
    for slug in registry.manifests:
        try:
            tokens_by_theme[slug] = read_tokens(themes_root / slug)
        except ThemeError as exc:
            errors.append(f"themes/{slug}: {exc}")
        sources[slug] = read_css_sources(themes_root / slug, slug)
    outputs: dict[str, str] = {}
    for slug in sorted(registry.manifests):
        chain = registry.chain(slug)
        if any(s not in tokens_by_theme for s in chain):
            continue  # its tokens.toml (or a parent's) is already reported
        tokens = resolve_tokens(chain, tokens_by_theme)
        violations = check_tokens(tokens, contract)
        errors.extend(f"themes/{slug}: {violation}" for violation in violations)
        if violations:
            continue
        tokens_css = render_tokens_css(tokens)
        outputs[f"{OUTPUT_PREFIX}/{slug}/tokens.css"] = tokens_css
        outputs[f"{OUTPUT_PREFIX}/{slug}/theme.css"] = build_theme_css(
            chain, tokens_css=tokens_css, sources=sources
        )
        for relative, module in read_js_modules(themes_root / slug, slug).items():
            outputs[f"{OUTPUT_PREFIX}/{slug}/js/{relative}"] = module
    if errors:
        raise BuildError(errors)
    return outputs


def build_themes(
    themes_root: Path, out_root: Path, contract: TokenContract = TOKEN_CONTRACT
) -> list[str]:
    """Write every theme's `tokens.css`, `theme.css` and JS modules; return the built slugs.

    Replaces `out_root/themes/` as a whole, so a removed theme leaves no stale CSS; nothing is
    written when the build fails.
    """
    outputs = render_themes(themes_root, contract)
    target = out_root / OUTPUT_PREFIX
    if target.exists():
        shutil.rmtree(target)
    for relative, css in outputs.items():
        path = out_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(css, encoding="utf-8")
    return sorted({relative.split("/")[1] for relative in outputs})


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the CSS of every theme (THM.2).")
    parser.add_argument("--themes-dir", type=Path, default=DEFAULT_THEMES_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)
    try:
        slugs = build_themes(args.themes_dir, args.out)
    except BuildError as exc:
        print("Theme build failed:", file=sys.stderr)
        for error in exc.errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    print(f"Built {len(slugs)} theme(s) in {args.out}: {', '.join(slugs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
