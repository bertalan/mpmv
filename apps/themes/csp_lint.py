"""CSP lint of the public templates (THM.3, spec 14 §14.5).

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

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # A script with `src`, or a data block (JSON-LD, JSON), which the browser never runs and
    # the CSP does not govern, is allowed; any other inline script is not.
    (
        re.compile(
            r"<script\b(?![^>]*\ssrc\s*=)"
            r"(?![^>]*\stype\s*=\s*[\"']?application/(?:ld\+)?json[\"'\s>])[^>]*>",
            re.IGNORECASE,
        ),
        "inline <script>",
    ),
    (re.compile(r"<style\b", re.IGNORECASE), "<style> block"),
    (re.compile(r"\sstyle\s*=", re.IGNORECASE), "style attribute"),
    (re.compile(r"\son[a-z]+\s*=", re.IGNORECASE), "on… handler attribute"),
    (re.compile(r"javascript:", re.IGNORECASE), "javascript: URL"),
    (re.compile(r"\shx-on\b", re.IGNORECASE), "hx-on attribute"),
    (re.compile(r"\shx-[\w-]+\s*=\s*[\"']\s*js:", re.IGNORECASE), "js: value"),
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
    findings: list[tuple[int, str]] = []
    for pattern, what in _RULES:
        findings += [(match.start(), what) for match in pattern.finditer(source)]
    for match in _ALPINE_RE.finditer(source):
        value = next(group for group in match.groups()[1:] if group is not None)
        if not _NAME_RE.match(value.strip()):
            findings.append((match.start(), f"Alpine expression in {match.group(1)}"))
    return [
        f"line {source.count(chr(10), 0, offset) + 1}: {what}" for offset, what in sorted(findings)
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
