"""CSP lint of the public templates and theme scripts (THM.3, spec 14 §14.5).

The public pages run under the strict Content-Security-Policy of SEC.1 (no `'unsafe-inline'`,
no `'unsafe-eval'`), so their templates must not contain what that policy blocks, and Alpine
directives may only name a registered component, a property or a method: the CSP build of
Alpine evaluates nothing else. `lint_template` is a pure function over a template's source;
`public_templates` lists the files to lint: every theme template and every themable app
template (the admin templates are never themed and keep their own rules).
"""

import re
from pathlib import Path

from .engine import is_themable

# An inline <script> (no src) runs under the CSP unless it is a JSON-LD data block: the browser
# never executes `type="application/ld+json"` and spec 14 allows structured data. Any other type
# (module, text/javascript, none) is inline code. Handlers on the tag are still caught below.
_INLINE_SCRIPT_RE = re.compile(r"<script\b(?![^>]*\ssrc\s*=)([^>]*)>", re.IGNORECASE)
_JSON_LD_TYPE_RE = re.compile(
    r"\stype\s*=\s*(?:\"application/ld\+json\"|'application/ld\+json'|application/ld\+json(?=[\s/]|$))",
    re.IGNORECASE,
)


def _bs_toggle(component: str) -> re.Pattern[str]:
    return re.compile(rf"\sdata-bs-toggle\s*=\s*[\"']?\s*{component}\b", re.IGNORECASE)


_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"<style\b", re.IGNORECASE), "<style> block"),
    (re.compile(r"\sstyle\s*=", re.IGNORECASE), "style attribute"),
    (re.compile(r"\son[a-z]+\s*=", re.IGNORECASE), "on… handler attribute"),
    (re.compile(r"javascript:", re.IGNORECASE), "javascript: URL"),
    (re.compile(r"\shx-on\b", re.IGNORECASE), "hx-on attribute"),
    (re.compile(r"\shx-[\w-]+\s*=\s*[\"']\s*js:", re.IGNORECASE), "js: value"),
    # Bootstrap components that build markup from a template string (spec 14 §14.5).
    (_bs_toggle("tooltip"), "Bootstrap tooltip"),
    (_bs_toggle("popover"), "Bootstrap popover"),
    (re.compile(r"\sdata-bs-html\b", re.IGNORECASE), "data-bs-html attribute"),
    (re.compile(r"\sdata-bs-template\b", re.IGNORECASE), "data-bs-template attribute"),
    # Bootstrap's helper classes set a fixed text colour with !important, and with the theme's
    # tokens these fail contrast (info 1.74:1, warning 4.18:1, secondary 1.94:1 on ride). Use the
    # theme's badge and alert variants instead (spec 14 §14.9). `-subtle` and `-emphasis`
    # variants are other classes and stay allowed.
    (
        re.compile(r"(?<![\w-])(?:text-bg|bg)-(?:info|warning|secondary)(?![\w-])"),
        "Bootstrap helper class with a fixed text colour",
    ),
)
# x-data="menu", @click='toggle', x-on:keydown.escape=close, :aria-expanded="expanded",
# x-bind:hidden="toggleHidden": the attribute and its value, double-quoted, single-quoted or
# unquoted (HTML allows all three, so the lint must too).
_ALPINE_RE = re.compile(
    r"\s(x-[\w-]+(?::[\w-]+)?(?:\.[\w-]+)*|@[\w-]+(?:\.[\w-]+)*|:[\w-]+)"
    r"\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'=<>`]+))"
)
_NAME_RE = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$")


def lint_template(source: str) -> list[str]:
    """`line <n>: <what>` for every pattern the strict CSP would block, in source order."""
    findings: list[tuple[int, str]] = [
        (match.start(), "inline <script>")
        for match in _INLINE_SCRIPT_RE.finditer(source)
        if not _JSON_LD_TYPE_RE.search(match.group(1))
    ]
    for pattern, what in _RULES:
        findings += [(match.start(), what) for match in pattern.finditer(source)]
    for match in _ALPINE_RE.finditer(source):
        value = next(group for group in match.groups()[1:] if group is not None)
        if not _NAME_RE.match(value.strip()):
            findings.append((match.start(), f"Alpine expression in {match.group(1)}"))
    return [
        f"line {source.count(chr(10), 0, offset) + 1}: {what}" for offset, what in sorted(findings)
    ]


# Bootstrap JS components that build markup from a template string (blocked by the CSP and
# the sanitizer settings): never constructed by a theme module.
_SCRIPT_RE = re.compile(
    r"\bbootstrap\s*\.\s*(Tooltip|Popover)\b(?=\s*(?:\(|\.\s*getOrCreateInstance\b))"
)


def lint_script(source: str) -> list[str]:
    """`line <n>: Bootstrap <Component>` for every tooltip or popover a script constructs."""
    return [
        f"line {source.count(chr(10), 0, match.start()) + 1}: Bootstrap {match.group(1)}"
        for match in _SCRIPT_RE.finditer(source)
    ]


def public_scripts(project_dir: Path) -> list[Path]:
    """Every theme JavaScript module, vendored libraries excluded."""
    return [
        path
        for path in sorted(project_dir.glob("themes/*/static/**/js/**/*.js"))
        if "vendor" not in path.parts
    ]


def public_templates(project_dir: Path) -> list[Path]:
    """Every template of every theme, then every themable template of every app."""
    paths = sorted(project_dir.glob("themes/*/templates/**/*.html"))
    for templates in sorted(project_dir.glob("apps/*/templates")):
        paths += [
            path
            for path in sorted(templates.rglob("*.html"))
            if is_themable(path.relative_to(templates).as_posix())
        ]
    return paths
