"""THM.3: the CSP lint of the public templates (spec 14 §14.5).

No inline `<script>` or `<style>`, no `style="…"`, no `on…=` handlers, no `javascript:` URLs, no
`hx-on` or `js:` values, and Alpine directives name only a registered component, property or
method (the CSP build evaluates nothing else). Every template of every theme is linted, plus the
public app templates that render inside the layouts.
"""

from pathlib import Path

import pytest

from apps.themes.csp_lint import lint_template, public_templates
from apps.themes.tests.builders import SHIPPED_THEMES

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize(
    ("source", "finding"),
    [
        ("<script>alert(1)</script>", "inline <script>"),
        ('<script nonce="{{ csp_nonce }}">go()</script>', "inline <script>"),
        ('<script type="text/javascript">go()</script>', "inline <script>"),
        ('<script type="module">go()</script>', "inline <script>"),
        ('<script type="application/javascript">go()</script>', "inline <script>"),
        ("<style>p{}</style>", "<style> block"),
        ('<p style="color:red">', "style attribute"),
        ('<button onclick="go()">', "on… handler attribute"),
        ('<a href="javascript:go()">', "javascript: URL"),
        ('<div hx-on:click="go()">', "hx-on attribute"),
        ('<div hx-vals="js:{a: 1}">', "js: value"),
        ('<div x-data="{ open: false }">', "Alpine expression"),
        ('<button @click="open = !open">', "Alpine expression"),
        ('<p x-show="open && ready">', "Alpine expression"),
        ("<p :class=\"open ? 'a' : 'b'\">", "Alpine expression"),
        ('<p x-init="start()">', "Alpine expression"),
    ],
)
def test_the_lint_finds_each_forbidden_pattern(source: str, finding: str) -> None:
    findings = lint_template(source)
    assert any(finding in f for f in findings), findings


@pytest.mark.parametrize(
    "source",
    [
        '<script src="{% static "vendor/htmx.min.js" %}" defer></script>',
        '<script type="module" src="/static/themes/base/js/base.js"></script>',
        '<div x-data="disclosure" @keydown.escape="close">',
        '<button x-on:click="toggle" :aria-expanded="expanded" x-bind:hidden="toggleHidden">',
        '<nav :class="navClass" aria-label="Main">',
        '<a href="/about/">About</a>',
        "{% include 'components/header.html' only %}",
        '<p class="note">Styles and scripts come from the theme.</p>',
    ],
)
def test_allowed_markup_passes(source: str) -> None:
    assert lint_template(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "<div x-data='{ open: false }'>",
        "<button @click='open = !open'>",
        "<p x-show='open && ready'>",
        '<p :class=\'open ? "a" : "b"\'>',
        "<button @click=toggle()>",
        "<p x-show=!open>",
        "<p x-init=start()>",
        "<div x-data={open:true}>",
    ],
)
def test_alpine_expressions_are_found_whatever_the_quoting(source: str) -> None:
    findings = lint_template(source)
    assert any("Alpine expression" in f for f in findings), findings


@pytest.mark.parametrize(
    "source",
    [
        "<div x-data='disclosure' @keydown.escape='close'>",
        "<button x-on:click='toggle' :aria-expanded='expanded'>",
        "<div x-data=disclosure @keydown.escape=close>",
        "<button x-on:click=toggle :aria-expanded=expanded>",
        "<div x-data=disclosure>",
        '<button @click=toggle class="nav-toggle">',
    ],
)
def test_alpine_names_pass_whatever_the_quoting(source: str) -> None:
    assert lint_template(source) == []


@pytest.mark.parametrize(
    "source",
    [
        '<script type="application/ld+json">{"@type": "FAQPage"}</script>',
        "<script type='application/ld+json'>{}</script>",
        "<script type=application/ld+json>{}</script>",
        '<script TYPE="Application/LD+JSON">{}</script>',
    ],
)
def test_a_json_ld_data_block_is_not_an_inline_script(source: str) -> None:
    assert lint_template(source) == []


@pytest.mark.parametrize(
    ("source", "finding"),
    [
        ('<script type="application/ld+json" onload="go()">{}</script>', "on… handler attribute"),
        ('<script type="module">go()</script>', "inline <script>"),
        ('<script type="text/javascript">go()</script>', "inline <script>"),
        ("<script>go()</script>", "inline <script>"),
        ('<script type="application/ld+jsonp">go()</script>', "inline <script>"),
        ('<script data-type="application/ld+json">go()</script>', "inline <script>"),
    ],
)
def test_any_other_inline_script_stays_forbidden(source: str, finding: str) -> None:
    findings = lint_template(source)
    assert any(finding in f for f in findings), findings


def test_findings_carry_the_line_number() -> None:
    assert lint_template('<p>\n<p style="x">') == ["line 2: style attribute"]


def test_the_public_templates_include_every_theme_template_and_the_moved_app_templates() -> None:
    names = {path.relative_to(ROOT).as_posix() for path in public_templates(ROOT)}
    assert "themes/base/templates/layouts/base.html" in names
    assert "themes/base/templates/layouts/account.html" in names
    assert "apps/cms/templates/cms/home_page.html" in names
    assert "apps/accounts/templates/accounts/mfa/verify.html" in names
    assert not any("/admin/" in name for name in names)


def test_every_public_template_passes_the_csp_lint() -> None:
    findings = {
        path.relative_to(ROOT).as_posix(): lint_template(path.read_text(encoding="utf-8"))
        for path in public_templates(ROOT)
    }
    assert {name: found for name, found in findings.items() if found} == {}


def test_the_shipped_themes_are_linted() -> None:
    assert any(path.is_relative_to(SHIPPED_THEMES) for path in public_templates(ROOT))
