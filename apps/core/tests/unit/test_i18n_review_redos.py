"""SEC.5 ReDoS: `placeholders` of the i18n review runs in linear time on hostile translations."""

import time
from collections import Counter

import pytest

from apps.core.i18n_review.engine import placeholders

BUDGET = 2.0  # seconds; the quadratic versions need minutes on these inputs


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Hello {{ name }}, {% if x %}yes{% endif %}{# note #}",
            {"tpl:{{ name }}", "tpl:{% if x %}", "tpl:{% endif %}", "tpl:{# note #}"},
        ),
        ("%s and %d%%", {"printf:%s", "printf:%d", "printf:%%"}),
        ("%(user)s has %(count)05.2f", {"printf:%(user)s", "printf:%(count)05.2f"}),
        (
            "%-10s|%+d|%#x|%*d|%.*f|%ld",
            {"printf:%-10s", "printf:%+d", "printf:%#x", "printf:%*d", "printf:%.*f", "printf:%ld"},
        ),
        ("%00d %0d %007s", {"printf:%00d", "printf:%0d", "printf:%007s"}),
        ("100% sure, {unclosed {{ and %(open", {"printf:% s"}),
        ("100%, {unclosed {{ and %(open", set()),
        ("{{ a", set()),
        ("%(a%(b)s", {"printf:%(a%(b)s"}),
    ],
)
def test_placeholders_keep_their_output(text: str, expected: set[str]) -> None:
    assert (
        set(placeholders(text)) - {k for k in placeholders(text) if k.startswith("field:")}
        == expected
    )


def test_placeholders_count_repeats() -> None:
    assert placeholders("{{ a }} {{ a }} %s %s") == Counter({"tpl:{{ a }}": 2, "printf:%s": 2})


HOSTILE = [
    "{{" * 50_000,
    "{%" * 50_000,
    "{#" * 50_000,
    "{{ a }" * 20_000,
    "%(" * 50_000,
    "%(" * 50_000 + ")!",
    "%" + "0" * 100_000 + "!",
    "%" * 100_000,
    "%(a)" * 25_000 + "!",
]


@pytest.mark.parametrize("text", HOSTILE)
def test_placeholders_are_linear_on_hostile_text(text: str) -> None:
    started = time.perf_counter()
    placeholders(text)
    assert time.perf_counter() - started < BUDGET
