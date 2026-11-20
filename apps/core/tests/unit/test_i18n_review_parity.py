# ruff: noqa: E501
"""SEC.5 ReDoS rewrite: `placeholders` gives the same output as before on real message strings (printf
width, precision and flags, `%(name)s`, `%%`, template tags, str.format fields)."""

import pytest

from apps.core.i18n_review.engine import placeholders

# Expected values were taken from the implementation before the SEC.5 ReDoS rewrite; the inputs are valid, real-looking text.

CASES = [
    ("Hello %s, you have %d new messages", {"printf:%d": 1, "printf:%s": 1}),
    ("Price: %5.2f EUR", {"printf:%5.2f": 1}),
    ("Name: %-10s|", {"printf:%-10s": 1}),
    ("%(name)s ordered %(count)d items", {"printf:%(count)d": 1, "printf:%(name)s": 1}),
    ("Discount 50%% off", {"printf:%%": 1}),
    (
        "{{ user.name }} bought {% trans 'x' %}",
        {"tpl:{% trans 'x' %}": 1, "tpl:{{ user.name }}": 1},
    ),
    (
        "Total %(total).2f%% of {{ cart.total }}",
        {"printf:%%": 1, "printf:%(total).2f": 1, "tpl:{{ cart.total }}": 1},
    ),
    ("%05d items", {"printf:%05d": 1}),
    ("%+.3e", {"printf:%+.3e": 1}),
    ("%*d and %.*f", {"printf:%*d": 1, "printf:%.*f": 1}),
    ("%ld %hd %lu", {"printf:%hd": 1, "printf:%ld": 1, "printf:%lu": 1}),
    ("Hello {name}, you are {age:>3}", {"field:age!:>3": 1, "field:name!:": 1}),
    ("{0} then {1!r:>8}", {"field:0!:": 1, "field:1!r:>8": 1}),
    (
        "{# a comment #} text {% if x %}y{% endif %}",
        {"tpl:{# a comment #}": 1, "tpl:{% endif %}": 1, "tpl:{% if x %}": 1},
    ),
    ("100% sure", {"printf:% s": 1}),
    ("%(a)s %(b)s %(a)s", {"printf:%(a)s": 2, "printf:%(b)s": 1}),
    ("%#x and %#o", {"printf:%#o": 1, "printf:%#x": 1}),
    ("% d spaces", {"printf:% d": 1}),
    ("%s%%s", {"printf:%%": 1, "printf:%s": 1}),
    ("{{ unclosed", {}),
    ("Plain text", {}),
]


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_placeholders_parity(text: str, expected: dict[str, int]) -> None:
    assert dict(sorted(placeholders(text).items())) == expected
