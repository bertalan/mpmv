"""Hostile-input corpus and Hypothesis strategies shared by every suite (SEC.5, item 8).

What people paste into a field ("wild copy-paste") and what an attacker types on purpose. Tests
feed these values to forms, API schemas, import parsers and output renderers, and assert that the
value is validated or stored safely: no 500, no active markup on output, no formula in a CSV
cell, no CR/LF in an email header, no path in a stored filename.

- `CORPUS` maps a category to its values; `ALL` is every string value, deduplicated.
- `hostile_text()` is a Hypothesis strategy mixing the corpus with generated Unicode text, and
  `at_and_over(n)` gives strings of exactly `n` and `n + 1` characters for every `max_length`.
- `active_markup(html)` lists what would run in a browser (script, handler, `javascript:` URL);
  `formula_cell(cell)` tells whether a spreadsheet would read a CSV cell as a formula.
- `PROFILES`: Hypothesis profiles. `ci` (modest example count, a deadline, no example database)
  is the default; `HYPOTHESIS_PROFILE=dev` runs more examples locally.

Only fake values: no credential-like literals (gitleaks runs on every PR).
"""

import os
import re
from datetime import timedelta
from typing import Final

from hypothesis import HealthCheck, settings
from hypothesis import strategies as st

# --- Hypothesis profiles --------------------------------------------------------------------

PROFILES: Final = {
    "ci": settings(
        max_examples=30,
        deadline=timedelta(milliseconds=2000),
        database=None,
        derandomize=True,
        suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
    ),
    "dev": settings(
        max_examples=200,
        deadline=timedelta(milliseconds=5000),
        database=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
    ),
}
for _name, _profile in PROFILES.items():
    settings.register_profile(_name, _profile)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))

# --- Pasted code ----------------------------------------------------------------------------

HTML: Final = (
    "<b>bold</b> and <i>italic</i>",
    "<script>alert(1)</script>",
    "<SCRIPT SRC=//example.invalid/x.js></SCRIPT>",
    '"><script>alert(document.domain)</script>',
    "'><img src=x onerror=alert(1)>",
    "<img src=x onerror=alert(1)>",
    "<svg onload=alert(1)>",
    '<a href="javascript:alert(1)">click</a>',
    '<a href="JaVaScRiPt:alert(1)">click</a>',
    '<a href="java&#x09;script:alert(1)">click</a>',
    '<iframe src="https://example.invalid"></iframe>',
    "<style>body{display:none}</style>",
    '<div style="background:url(javascript:alert(1))">x</div>',
    "</textarea><script>alert(1)</script>",
    "</script><script>alert(1)</script>",
    "<!-- comment --><p>after</p>",
    '<p onclick="alert(1)">para</p>',
    '<form action="https://example.invalid"><input name=q></form>',
    '<meta http-equiv="refresh" content="0;url=https://example.invalid">',
    '<base href="https://example.invalid/">',
)

TEMPLATE_SYNTAX: Final = (
    "{{ settings.SECRET_KEY }}",
    "{{ request.user.email }}",
    "{% debug %}",
    "{% load static %}{% static 'x' %}",
    "{# comment #}",
    "{{ 7*7 }}",
    "${7*7}",
    "<%= 7*7 %>",
    "{{''.__class__.__mro__}}",
)

SQL: Final = (
    "' OR '1'='1",
    "'; DROP TABLE vendors_vendor; --",
    "1; SELECT pg_sleep(5)",
    '" OR ""="',
    "admin'--",
    "%' AND 1=1 --",
    "1 UNION SELECT NULL, NULL",
)

SHELL: Final = (
    "$(id)",
    "`id`",
    "; rm -rf / #",
    "| cat /etc/hostname",
    "&& curl https://example.invalid",
    "$HOME",
    "a\\b\\c",
)

MARKDOWN: Final = (
    "# Heading\n\n* item\n* item",
    "[click](javascript:alert(1))",
    "![img](https://example.invalid/pixel.png)",
    "```python\nprint('x')\n```",
    "**bold** _it_ `code`",
    "| a | b |\n|---|---|\n| 1 | 2 |",
)

#: Rich text as Word and Google Docs put it on the clipboard.
WORD_HTML: Final = (
    '<p class="MsoNormal" style="margin:0cm;mso-line-height-rule:exactly">'
    '<span lang="IT" style="font-family:&quot;Calibri&quot;,sans-serif">Testo</span>'
    "<o:p></o:p></p>",
    "<!--[if gte mso 9]><xml><w:WordDocument><w:View>Normal</w:View></w:WordDocument></xml>"
    "<![endif]--><p>after</p>",
    '<meta charset="utf-8"><b style="font-weight:normal;" id="docs-internal-guid-1234">'
    '<span style="font-size:11pt;font-family:Arial;color:#000000;">Google Docs text</span></b>',
    '<p dir="ltr" style="line-height:1.38;margin-top:0pt"><span style="font-weight:700">'
    "Bold</span></p>",
)

# --- Unicode --------------------------------------------------------------------------------

CONTROL: Final = (
    "nul\x00inside",
    "\x00",
    "bell\x07 and backspace\x08",
    "escape \x1b[31mred\x1b[0m",
    "vertical\x0btab and form\x0cfeed",
    "del\x7f",
    "c1 \x85 next line",
    "line\u2028separator\u2029paragraph",
)

ZERO_WIDTH: Final = (
    "zero\u200bwidth",
    "joiner\u200dnon\u200cjoiner",
    "\ufeffbom at start",
    "word\u2060joiner",
    "\u00adsoft\u00adhyphen",
)

BIDI: Final = (
    "invoice\u202egpj.exe",
    "\u202dforced ltr\u202c",
    "\u2066isolate\u2069",
    "\u200fright-to-left mark",
)

COMBINING: Final = (
    "e\u0301",
    "Z\u0351\u0352\u0353\u0354\u0355\u0356\u0357\u0358\u0359\u035a",
    "a" + "\u0300" * 50,
)

EMOJI: Final = (
    "\U0001f600",
    "\U0001f469\u200d\U0001f4bb",
    "\U0001f468\u200d\U0001f469\u200d\U0001f467\u200d\U0001f466",
    "\U0001f1ee\U0001f1f9",
    "\U0001f44d\U0001f3fd",
)

HOMOGLYPHS: Final = (
    "\u0430dmin",  # Cyrillic a
    "p\u0430yp\u0430l",
    "\u0391\u0392\u0395",  # Greek capitals that look Latin
    "\uff21\uff22\uff23",  # full-width ABC
    "\u0131stanbul",  # dotless i
)

#: A lone surrogate is a valid Python str but not encodable UTF-8; JSON can carry one (`\ud800`).
LONE_SURROGATES: Final = ("\ud800", "a\udfffb", "\udc00\ud800")

#: Not in NFC: decomposed accents. The owner wants NFC stored (SEC.5 item 8).
NON_NFC: Final = ("Citta\u0300", "Jose\u0301", "e\u0301\u0301", "\u212b")  # U+212B ANGSTROM SIGN

WHITESPACE: Final = ("", " ", "   ", "\t", "\n", "\r\n", "\u00a0", "\u3000", " padded ")

# --- Malformed data -------------------------------------------------------------------------

PRICES: Final = (
    "1.234,50 €",
    "1,234.50",
    "1 234,50",
    "€ 12",
    "12,5",
    "12.5.0",
    "-1",
    "-0.00",
    "0.001",
    "1e3",
    "1E-7",
    "NaN",
    "Infinity",
    "-Infinity",
    "9" * 40,
    "0x1A",
    "١٢٣",  # Arabic-Indic digits
    "12\u00a0€",
)

QUANTITIES: Final = (-1, -(2**31), 2**31, 2**63, 10**30, 0)

DATES: Final = (
    "31/12/2026",
    "12/31/2026",
    "2026-02-30",
    "2026-13-01",
    "2026-1-1",
    "0000-00-00",
    "9999-12-31T25:61:61",
    "yesterday",
    "2026-11-09T10:00:00+25:00",
    "1e10",
)

VAT_NUMBERS: Final = (
    "it 0123456789 0",
    "IT.01234567890",
    "it01234567890",
    "IT 012.345.678.90",
    "DE 123 456 789",
    "  ",
)

IBANS: Final = (
    "it60 x054 2811 1010 0000 0123 456",
    "IT60.X054.2811.1010.0000.0123.456",
    "IT60-X054-2811-1010-0000-0123-456",
    "de89370400440532013000",
    "GB82 WEST 1234 5698 7654 32 ",
)

POSTCODES: Final = ("00 184", "00.184", "sw1a 1aa", "SW1A-1AA", "1234 ab", "00184\n", "-00184")

#: Malformed or abusive JSON text, for JSON fields and JSON bodies.
JSON_TEXT: Final = (
    "{",
    "{'single': 'quotes'}",
    '{"a": 1,}',
    "[1, 2",
    "NaN",
    '{"a": NaN}',
    '{"a": Infinity}',
    '"just a string"',
    "null",
    "[]",
    '{"__proto__": {"admin": true}}',
    '{"a": 1, "a": 2}',
    "[" * 100_000 + "]" * 100_000,  # past the json module's recursion limit
    '{"a":' * 500 + "1" + "}" * 500,
    '{"\\ud800": "\\udfff"}',
)

# --- Injection into outputs -----------------------------------------------------------------

#: A spreadsheet runs these as formulas when they start a cell (OWASP "CSV injection").
CSV_FORMULAS: Final = (
    "=1+1",
    '=HYPERLINK("https://example.invalid","click")',
    "+1+1",
    "-1+1",
    "@SUM(A1:A2)",
    "\t=1+1",
    "\r=1+1",
    "=cmd|' /C calc'!A0",
)

HEADER_INJECTION: Final = (
    "subject\r\nBcc: someone@example.invalid",
    "subject\nX-Injected: 1",
    "subject\rX-Injected: 1",
    "user@example.com\r\nBcc: someone@example.invalid",
    "user@example.com\nCc: someone@example.invalid",
)

FILENAMES: Final = (
    "../../etc/hostname",
    "..\\..\\windows\\win.ini",
    "/etc/hostname",
    "C:\\temp\\x.png",
    "CON",
    "NUL.txt",
    "aux.jpg",
    "photo.jpg\x00.exe",
    "invoice\u202egpj.exe",
    ".htaccess",
    "...",
    " .png",
    "a" * 300 + ".png",
)

CORPUS: Final[dict[str, tuple[str, ...]]] = {
    "html": HTML,
    "template": TEMPLATE_SYNTAX,
    "sql": SQL,
    "shell": SHELL,
    "markdown": MARKDOWN,
    "word_html": WORD_HTML,
    "control": CONTROL,
    "zero_width": ZERO_WIDTH,
    "bidi": BIDI,
    "combining": COMBINING,
    "emoji": EMOJI,
    "homoglyphs": HOMOGLYPHS,
    "lone_surrogates": LONE_SURROGATES,
    "non_nfc": NON_NFC,
    "whitespace": WHITESPACE,
    "prices": PRICES,
    "dates": DATES,
    "vat_numbers": VAT_NUMBERS,
    "ibans": IBANS,
    "postcodes": POSTCODES,
    "json_text": JSON_TEXT,
    "csv_formulas": CSV_FORMULAS,
    "header_injection": HEADER_INJECTION,
    "filenames": FILENAMES,
}

ALL: Final[tuple[str, ...]] = tuple(dict.fromkeys(v for values in CORPUS.values() for v in values))


def has_lone_surrogate(value: str) -> bool:
    return any("\ud800" <= char <= "\udfff" for char in value)


#: The corpus without what a database cannot store at all (NUL, lone surrogates), for tests
#: that check a stored value round-trips; those two are refused separately.
STORABLE: Final[tuple[str, ...]] = tuple(
    value for value in ALL if "\x00" not in value and not has_lone_surrogate(value)
)


def values(*categories: str) -> tuple[str, ...]:
    """The corpus values of `categories`, in order, deduplicated."""
    return tuple(dict.fromkeys(v for name in categories for v in CORPUS[name]))


def at_and_over(max_length: int, fill: str = "x") -> tuple[str, str]:
    """A string of exactly `max_length` characters and one of `max_length + 1`."""
    return fill * max_length, fill * (max_length + 1)


# --- Strategies -----------------------------------------------------------------------------


def hostile_text(*, max_size: int = 300) -> st.SearchStrategy[str]:
    """Corpus values, generated Unicode text (surrogates and controls included), and both
    glued together: what a paste can carry."""
    corpus = st.sampled_from(ALL)
    generated = st.text(alphabet=st.characters(codec=None), max_size=max_size)
    return st.one_of(corpus, generated, st.builds(lambda a, b: a + b, corpus, generated))


def storable_text(*, max_size: int = 300) -> st.SearchStrategy[str]:
    """Like `hostile_text`, without NUL and lone surrogates."""
    return hostile_text(max_size=max_size).filter(
        lambda v: "\x00" not in v and not has_lone_surrogate(v)
    )


def json_values(max_leaves: int = 20) -> st.SearchStrategy[object]:
    """Arbitrary JSON-like values with hostile strings, nested."""
    leaves = st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.floats(allow_nan=False, allow_infinity=False),
        hostile_text(max_size=40),
    )
    return st.recursive(
        leaves,
        lambda children: st.one_of(
            st.lists(children, max_size=4),
            st.dictionaries(hostile_text(max_size=20), children, max_size=4),
        ),
        max_leaves=max_leaves,
    )


# --- Detectors ------------------------------------------------------------------------------

_ACTIVE: Final = (
    ("script element", re.compile(r"<\s*script\b", re.IGNORECASE)),
    ("event handler", re.compile(r"<[^>]*\son[a-z]+\s*=", re.IGNORECASE)),
    (
        "javascript: URL",
        re.compile(r"""<[^>]*\s(?:href|src|action)\s*=\s*["']?\s*javascript:""", re.I),
    ),
    ("iframe", re.compile(r"<\s*iframe\b", re.IGNORECASE)),
    ("style element", re.compile(r"<\s*style\b", re.IGNORECASE)),
    ("meta element", re.compile(r"<\s*meta\b", re.IGNORECASE)),
    ("base element", re.compile(r"<\s*base\b", re.IGNORECASE)),
    ("form element", re.compile(r"<\s*form\b", re.IGNORECASE)),
)


def active_markup(html: str) -> list[str]:
    """What in `html` a browser would run or obey: empty when the text is inert."""
    return [name for name, pattern in _ACTIVE if pattern.search(html)]


#: Characters that make a spreadsheet read a cell as a formula when they come first.
FORMULA_STARTS: Final = ("=", "+", "-", "@", "\t", "\r")


def formula_cell(cell: str) -> bool:
    """True when a spreadsheet would evaluate `cell` (OWASP CSV injection)."""
    return cell.startswith(FORMULA_STARTS)


def header_safe(value: str) -> bool:
    """True when `value` can go in an email header: no CR, LF or other line break."""
    return not any(char in value for char in "\r\n\x0b\x0c\x85\u2028\u2029")
