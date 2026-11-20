# ruff: noqa: E501
"""SEC.5 ReDoS rewrites: `class_names` and `minify_css` give the same output as before on real theme CSS
(snippets of the base and ride themes, plus string, escape and at-rule cases)."""

import pytest

from apps.themes.collisions import class_names
from apps.themes.engine import minify_css

# Expected values were taken from the implementation before the SEC.5 ReDoS rewrite; the inputs are valid, real-looking text.

CSS = [
    '.button--danger {\n  --button-bg: transparent;\n  border-color: var(--color-danger);\n}\n\n.button--danger:hover:not(:disabled, [aria-disabled="true"]) {\n  color: var(--color-surface);\n}\n',
    "/* The navbar keeps the theme's own spacing, not Bootstrap's. */\n.site-header a,\n.site-header .nav-toggle,\n.site-header .subnav__toggle {\n  color: var(--color-text-inverse);\n}\n.site-header__inner {\n  --bs-navbar-padding-x: 0;\n  min-block-size: var(--header-height);\n  gap: var(--space-xs) var(--space-l);\n}\n",
    '@layer components {\n  input:not([type="checkbox"], [type="radio"], [type="hidden"]),\n  select {\n    transform: translateY(calc(-100% - var(--space-l)));\n  }\n  a:hover { color: red }\n  .card > .card__body + .card__footer { margin: 0 auto }\n}\n',
    '.quote::before { content: "\\201C"; }\n.quote::after { content: \'\\201D\'; }\n.sep::after { content: " > "; }\n.a\\:b, .md\\:w-1\\/2 { display: none; }\n@media (min-width: 64rem) { .nav-wide .nav__item { display: inline-block; } }\n',
    ".u-visually-hidden{position:absolute;clip:rect(0,0,0,0)}\n.has-icon{background:url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg'/>\") no-repeat}\n",
    "",
]
CLASS_NAMES = [
    ["button--danger"],
    ["nav-toggle", "site-header", "site-header__inner", "subnav__toggle"],
    ["card", "card__body", "card__footer"],
    ["a:b", "md:w-1/2", "nav-wide", "nav__item", "quote", "sep"],
    ["has-icon", "u-visually-hidden"],
    [],
]
MINIFIED = [
    '.button--danger{--button-bg:transparent;border-color:var(--color-danger)}.button--danger:hover:not(:disabled,[aria-disabled="true"]){color:var(--color-surface)}',
    ".site-header a,.site-header .nav-toggle,.site-header .subnav__toggle{color:var(--color-text-inverse)}.site-header__inner{--bs-navbar-padding-x:0;min-block-size:var(--header-height);gap:var(--space-xs) var(--space-l)}",
    '@layer components{input:not([type="checkbox"],[type="radio"],[type="hidden"]),select{transform:translateY(calc(-100% - var(--space-l)))}a:hover{color:red}.card>.card__body + .card__footer{margin:0 auto}}',
    '.quote::before{content:"\\201C"}.quote::after{content:\'\\201D\'}.sep::after{content:" > "}.a\\:b,.md\\:w-1\\/2{display:none}@media (min-width:64rem){.nav-wide .nav__item{display:inline-block}}',
    ".u-visually-hidden{position:absolute;clip:rect(0,0,0,0)}.has-icon{background:url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg'/>\") no-repeat}",
    "",
]


@pytest.mark.parametrize("index", range(len(CSS)))
def test_class_names_parity(index: int) -> None:
    assert sorted(class_names(CSS[index])) == CLASS_NAMES[index]


@pytest.mark.parametrize("index", range(len(CSS)))
def test_minify_css_parity(index: int) -> None:
    assert minify_css(CSS[index]) == MINIFIED[index]
