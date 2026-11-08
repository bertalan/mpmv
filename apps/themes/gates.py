"""Theme quality gates (THM.5, spec 14 §14.7, §14.8, §14.11): `python -m apps.themes.gates`.

Every registered theme under a themes directory must pass, each failure tagged with its gate:

- `manifest`: `theme.toml` is valid and the chain ends in `base` (THM.1);
- `tokens`: the resolved tokens meet the token contract, contrast pairs included (THM.2);
- `csp`: the theme's own templates pass the CSP lint (THM.3);
- `size`: the built `theme.css` and the JavaScript a page loads (vendored htmx and Alpine plus
  the chain's modules) stay within their compressed budgets;
- `fonts`: at most four font files, all woff2, and at most two families;
- `licences`: `LICENSES.toml` covers every font, icon and image with an allowed licence.

Sizes are gzip at level 9: the spec budgets are Brotli, which compresses CSS and JS a little
better, so gzip within the budget means Brotli within it too, without a new dependency. The
browser gates (axe-core, visual baselines) run in the e2e suite (tests/e2e/). This module does
I/O but needs no Django settings: `make check` and CI run it as a command, and a unit test runs
it over the shipped themes.
"""

import argparse
import gzip
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .build import (
    DEFAULT_THEMES_DIR,
    OUTPUT_PREFIX,
    read_css_sources,
    read_js_modules,
    read_manifest_sources,
    read_tokens,
)
from .contract import TOKEN_CONTRACT
from .csp_lint import lint_template
from .engine import (
    ThemeError,
    ThemeRegistry,
    build_registry,
    build_theme_css,
    check_tokens,
    render_tokens_css,
    resolve_tokens,
)
from .vendored import VENDOR_DIR

CSS_BUDGET = 45 * 1024  # theme.css, compressed (§14.7)
JS_BUDGET = 40 * 1024  # all JavaScript of a page, htmx and Alpine included, compressed (§14.7)
MAX_FONT_FILES = 4
MAX_FONT_FAMILIES = 2
FONT_SUFFIX = ".woff2"
LICENCES_FILE = "LICENSES.toml"
ALLOWED_ASSET_LICENCES = frozenset({"OFL-1.1", "Apache-2.0", "MIT", "ISC", "CC0-1.0"})
ASSET_KINDS = frozenset({"font", "icons", "image"})
# Files under static/themes/<slug>/ that are code, not assets needing a licence entry.
SOURCE_SUFFIXES = frozenset({".css", ".js"})


@dataclass(frozen=True)
class GateFailure:
    theme: str
    gate: str
    message: str

    def __str__(self) -> str:
        return f"themes/{self.theme} [{self.gate}]: {self.message}"


def compressed_size(data: bytes) -> int:
    """The gzip size of `data` at the highest level, with a fixed header (deterministic)."""
    return len(gzip.compress(data, compresslevel=9, mtime=0))


def _kb(size: int) -> str:
    return f"{size / 1024:.1f} KB"


def check_sizes(slug: str, css: str, scripts: Mapping[str, bytes]) -> list[GateFailure]:
    """The CSS and JavaScript budgets of one theme (§14.7)."""
    failures = []
    css_size = compressed_size(css.encode("utf-8"))
    if css_size > CSS_BUDGET:
        failures.append(
            GateFailure(
                slug,
                "size",
                f"theme.css is {_kb(css_size)} compressed, over the {_kb(CSS_BUDGET)} budget",
            )
        )
    js_size = sum(compressed_size(data) for data in scripts.values())
    if js_size > JS_BUDGET:
        failures.append(
            GateFailure(
                slug,
                "size",
                f"JavaScript is {_kb(js_size)} compressed ({', '.join(sorted(scripts))}), "
                f"over the {_kb(JS_BUDGET)} budget",
            )
        )
    return failures


def theme_assets(theme_dir: Path, slug: str) -> list[str]:
    """Every file under `static/themes/<slug>/` that is not CSS or JS, relative to it."""
    root = theme_dir / "static" / OUTPUT_PREFIX / slug
    if not root.is_dir():
        return []
    return [
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix not in SOURCE_SUFFIXES
    ]


def _entry_problems(entry: Mapping[str, object]) -> list[str]:
    problems = []
    for key in ("name", "source", "author", "license"):
        if not isinstance(entry.get(key), str) or not str(entry[key]).strip():
            problems.append(f"no {key}")
    source = entry.get("source")
    if isinstance(source, str) and source and not source.startswith("https://"):
        problems.append("source must be an https URL")
    licence = entry.get("license")
    if isinstance(licence, str) and licence and licence not in ALLOWED_ASSET_LICENCES:
        problems.append(f"licence {licence!r} is not allowed")
    if entry.get("kind") not in ASSET_KINDS:
        problems.append(f"kind must be one of {', '.join(sorted(ASSET_KINDS))}")
    files = entry.get("files")
    if not isinstance(files, list) or not files or not all(isinstance(f, str) for f in files):
        problems.append("files must list at least one path")
    return problems


def check_licences(
    slug: str, assets: Sequence[str], licences: Mapping[str, object] | ThemeError | None
) -> list[GateFailure]:
    """`LICENSES.toml` (decoded, None when absent) against the theme's asset files (§14.8)."""
    if isinstance(licences, ThemeError):
        return [GateFailure(slug, "licences", str(licences))]
    entries = (licences or {}).get("asset", [])
    if not isinstance(entries, list):
        return [GateFailure(slug, "licences", f"{LICENCES_FILE}: [[asset]] must be a list")]
    failures = []
    covered: dict[str, str] = {}
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            failures.append(GateFailure(slug, "licences", f"entry {index} is not a table"))
            continue
        label = entry.get("name") or f"entry {index}"
        for problem in _entry_problems(entry):
            failures.append(GateFailure(slug, "licences", f"{label}: {problem}"))
        files = entry.get("files")
        for name in files if isinstance(files, list) else ():
            if isinstance(name, str):
                covered[name] = str(entry.get("kind", ""))
                if name not in assets:
                    failures.append(
                        GateFailure(slug, "licences", f"{label}: {name} is listed but missing")
                    )
    for asset in assets:
        if asset not in covered:
            failures.append(
                GateFailure(slug, "licences", f"{asset} has no entry in {LICENCES_FILE}")
            )
    return failures


def check_fonts(
    slug: str, assets: Sequence[str], licences: Mapping[str, object] | ThemeError | None
) -> list[GateFailure]:
    """At most four woff2 font files in `fonts/` and at most two font families (§14.7)."""
    fonts = [asset for asset in assets if asset.startswith("fonts/")]
    failures = [
        GateFailure(slug, "fonts", f"{font} is not a {FONT_SUFFIX} file")
        for font in fonts
        if not font.endswith(FONT_SUFFIX)
    ]
    woff2 = [font for font in fonts if font.endswith(FONT_SUFFIX)]
    if len(woff2) > MAX_FONT_FILES:
        failures.append(
            GateFailure(
                slug,
                "fonts",
                f"{len(woff2)} woff2 files, over the budget of {MAX_FONT_FILES}",
            )
        )
    entries = [] if isinstance(licences, ThemeError) else (licences or {}).get("asset", [])
    families = sorted(
        {
            str(entry.get("name"))
            for entry in (entries if isinstance(entries, list) else [])
            if isinstance(entry, dict) and entry.get("kind") == "font"
        }
    )
    if len(families) > MAX_FONT_FAMILIES:
        failures.append(
            GateFailure(
                slug,
                "fonts",
                f"{len(families)} font families ({', '.join(families)}), "
                f"over the budget of {MAX_FONT_FAMILIES}",
            )
        )
    return failures


def read_licences(theme_dir: Path) -> Mapping[str, object] | ThemeError | None:
    path = theme_dir / LICENCES_FILE
    if not path.is_file():
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return ThemeError(f"{LICENCES_FILE} cannot be read: {exc}")


def theme_templates(theme_dir: Path) -> dict[str, str]:
    root = theme_dir / "templates"
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.html"))
    }


def vendored_scripts(vendor_dir: Path) -> dict[str, bytes]:
    return {f"vendor/{path.name}": path.read_bytes() for path in sorted(vendor_dir.glob("*.js"))}


def _check_theme(
    root: Path, slug: str, registry: ThemeRegistry, vendor: Mapping[str, bytes]
) -> list[GateFailure]:
    theme_dir = root / slug
    chain = registry.chain(slug)
    failures: list[GateFailure] = []
    for name, source in theme_templates(theme_dir).items():
        failures += [
            GateFailure(slug, "csp", f"templates/{name} {finding}")
            for finding in lint_template(source)
        ]
    try:
        tokens_by_theme = {s: read_tokens(root / s) for s in chain}
    except ThemeError as exc:
        failures.append(GateFailure(slug, "tokens", str(exc)))
        tokens_by_theme = None
    if tokens_by_theme is not None:
        tokens = resolve_tokens(chain, tokens_by_theme)
        violations = check_tokens(tokens, TOKEN_CONTRACT)
        failures += [GateFailure(slug, "tokens", str(v)) for v in violations]
        if not violations:
            css = build_theme_css(
                chain,
                tokens_css=render_tokens_css(tokens),
                sources={s: read_css_sources(root / s, s) for s in chain},
            )
            scripts = dict(vendor)
            for s in chain:
                for name, module in read_js_modules(root / s, s).items():
                    scripts[f"themes/{s}/js/{name}"] = module.encode("utf-8")
            failures += check_sizes(slug, css, scripts)
    assets = theme_assets(theme_dir, slug)
    licences = read_licences(theme_dir)
    failures += check_licences(slug, assets, licences)
    failures += check_fonts(slug, assets, licences)
    return failures


def run_gates(themes_root: Path, vendor_dir: Path = VENDOR_DIR) -> list[GateFailure]:
    """Every gate failure of every theme under `themes_root`; an empty list when all pass."""
    sources = read_manifest_sources(themes_root)
    registry = build_registry(sources)
    failures = [
        GateFailure(_slug_of(error), "manifest", error.split(": ", 1)[-1])
        for error in registry.errors
    ]
    vendor = vendored_scripts(vendor_dir)
    for slug in sorted(registry.manifests):
        failures += _check_theme(themes_root, slug, registry, vendor)
    return failures


def _slug_of(error: str) -> str:
    head = error.split(": ", 1)[0]
    return head.removeprefix("themes/") if head.startswith("themes/") else "base"


def _report(failures: Iterable[GateFailure]) -> None:
    for failure in failures:
        print(f"  {failure}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the theme quality gates (THM.5).")
    parser.add_argument("--themes-dir", type=Path, default=DEFAULT_THEMES_DIR)
    args = parser.parse_args(argv)
    failures = run_gates(args.themes_dir)
    if failures:
        print("Theme quality gates failed:", file=sys.stderr)
        _report(failures)
        return 1
    slugs = sorted(build_registry(read_manifest_sources(args.themes_dir)).manifests)
    print(f"Theme quality gates passed for {len(slugs)} theme(s): {', '.join(slugs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
