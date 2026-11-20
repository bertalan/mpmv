"""SEC.5 ReDoS: the CSS scans of `collisions.class_names` and `engine.minify_css` run in linear
time on hostile stylesheets, and keep their output on valid ones."""

import time

import pytest

from apps.themes.collisions import class_names
from apps.themes.engine import minify_css

BUDGET = 2.0  # seconds; the quadratic versions need minutes on these inputs


def test_class_names_keeps_its_output() -> None:
    css = (
        '/* .gone { } */ .a, .b > .c:hover { content: "x { .nope }"; }\n'
        "@media (min-width: 1px) { .d .e { color: red } }\n"
        ".f\\:g { content: 'it\\'s .no' }\n"
        '.h::after { content: "unclosed }\n'
        ".i { margin: 0 }"
    )
    assert class_names(css) == {"a", "b", "c", "d", "e", "f:g", "h", "i"}


def test_minify_css_keeps_its_output() -> None:
    css = ".a  ,\n.b  >  .c {\n  color:  red ;\n  margin: 0 auto ;\n}\n"
    css += "  @media x { .d { top: 1px } }\n"
    assert minify_css(css) == ".a,.b>.c{color:red;margin:0 auto}@media x{.d{top:1px}}"


HOSTILE = [
    '"a' * 50_000,
    "'a" * 50_000,
    '"\\' * 50_000,
    "a" * 100_000,
    " " * 100_000,
    "a " * 50_000,
    ".a" * 50_000,
]


@pytest.mark.parametrize("css", HOSTILE)
def test_class_names_is_linear_on_hostile_css(css: str) -> None:
    started = time.perf_counter()
    class_names(css)
    assert time.perf_counter() - started < BUDGET


@pytest.mark.parametrize("css", [" " * 100_000, "a" + " " * 100_000 + "!", ". " * 50_000])
def test_minify_css_is_linear_on_hostile_css(css: str) -> None:
    started = time.perf_counter()
    minify_css(css)
    assert time.perf_counter() - started < BUDGET
