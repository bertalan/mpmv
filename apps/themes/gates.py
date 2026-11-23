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

from .bootstrap_map import bootstrap_map_css
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
from .foundation import BUNDLE_OUTPUT, foundation_files
from .vendored import VENDOR_DIR

LAZY_JS_DIR = "lazy/"  # scripts under js/lazy/ are imported on demand: the JS budget skips them
# Each lazy script, compressed. They are outside the page budget, so this cap keeps one from growing
# unnoticed. Shipped ones are about 2 KB; 8 KB leaves room to grow and still flags a framework.
LAZY_JS_BUDGET = 8 * 1024
CSS_BUDGET = 45 * 1024  # theme.css, compressed (§14.7)
JS_BUDGET = 40 * 1024  # all JavaScript of a page, htmx and Alpine included, compressed (§14.7)
MAX_FONT_FILES = 4
MAX_FONT_FAMILIES = 2
FONT_SUFFIX = ".woff2"
LICENCES_FILE = "LICENSES.toml"
# §14.8: fonts OFL 1.1 or Apache 2.0, icon sets MIT or ISC, placeholder images CC0 (own work).
ALLOWED_LICENCES_BY_KIND: Mapping[str, frozenset[str]] = {
    "font": frozenset({"OFL-1.1", "Apache-2.0"}),
    "icons": frozenset({"MIT", "ISC"}),
    "image": frozenset({"CC0-1.0"}),
    "framework": frozenset({"MIT"}),
}
ALLOWED_ASSET_LICENCES = frozenset().union(*ALLOWED_LICENCES_BY_KIND.values())
ASSET_KINDS = frozenset(ALLOWED_LICENCES_BY_KIND)
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


def check_sizes(
    slug: str,
    css: str,
    scripts: Mapping[str, bytes],
    *,
    css_budget: int = CSS_BUDGET,
    js_budget: int = JS_BUDGET,
) -> list[GateFailure]:
    """The CSS and JavaScript budgets of one theme (§14.7)."""
    failures = []
    css_size = compressed_size(css.encode("utf-8"))
    if css_size > css_budget:
        failures.append(
            GateFailure(
                slug,
                "size",
                f"theme.css is {_kb(css_size)} compressed, over the {_kb(css_budget)} budget",
            )
        )
    js_size = sum(compressed_size(data) for data in scripts.values())
    if js_size > js_budget:
        failures.append(
            GateFailure(
                slug,
                "size",
                f"JavaScript is {_kb(js_size)} compressed ({', '.join(sorted(scripts))}), "
                f"over the {_kb(js_budget)} budget",
            )
        )
    return failures


def check_lazy_sizes(
    slug: str, lazy: Mapping[str, bytes], *, budget: int = LAZY_JS_BUDGET
) -> list[GateFailure]:
    """The per-file cap of the on-demand scripts under `js/lazy/` (§14.7)."""
    failures = []
    for name in sorted(lazy):
        size = compressed_size(lazy[name])
        if size > budget:
            failures.append(
                GateFailure(
                    slug,
                    "size",
                    f"lazy script {name} is {_kb(size)} compressed, over the {_kb(budget)} cap",
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
    kind = entry.get("kind")
    if isinstance(licence, str) and licence and licence not in ALLOWED_ASSET_LICENCES:
        problems.append(f"licence {licence!r} is not allowed")
    elif isinstance(licence, str) and isinstance(kind, str) and kind in ASSET_KINDS:
        allowed = ALLOWED_LICENCES_BY_KIND[kind]
        if licence not in allowed:
            problems.append(
                f"licence {licence!r} is not allowed for kind {kind!r} "
                f"(only {', '.join(sorted(allowed))})"
            )
    if kind not in ASSET_KINDS:
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
            foundation_css, bundle = None, None
            declared = registry.foundation(slug)
            if declared is not None:
                try:
                    foundation_css, bundle = foundation_files(
                        root / declared[0], declared[0], declared[1]
                    )
                except ThemeError as exc:
                    failures.append(GateFailure(slug, "foundation", str(exc)))
            css = build_theme_css(
                chain,
                tokens_css=render_tokens_css(tokens),
                sources={s: read_css_sources(root / s, s) for s in chain},
                foundation_css=foundation_css,
                bootstrap_map=bootstrap_map_css(tokens) if foundation_css is not None else None,
            )
            scripts = dict(vendor)
            if bundle is not None:
                scripts[BUNDLE_OUTPUT] = bundle.encode("utf-8")
            lazy: dict[str, bytes] = {}
            for s in chain:
                for name, module in read_js_modules(root / s, s).items():
                    if name.startswith(LAZY_JS_DIR):
                        lazy[name] = module.encode("utf-8")  # on demand: capped file by file
                        continue
                    scripts[f"themes/{s}/js/{name}"] = module.encode("utf-8")
            manifest = registry[slug]
            failures += check_sizes(
                slug,
                css,
                scripts,
                css_budget=(manifest.css_budget_kb or CSS_BUDGET // 1024) * 1024,
                js_budget=(manifest.js_budget_kb or JS_BUDGET // 1024) * 1024,
            )
            failures += check_lazy_sizes(
                slug,
                lazy,
                budget=(manifest.lazy_js_budget_kb or LAZY_JS_BUDGET // 1024) * 1024,
            )
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
