"""ARC.7 done-when: no "IT" constant in the application code.

The platform's country is configuration (`PlatformOperator`, read with
`apps.core.services.current_operator()`), never a literal: a relocation of the operator must be a
new configuration row, not a code change (spec 04 §4.4 case 7, spec 05). Tests and migrations
are excluded: test data and data migrations may name any country.

A line marked `# country-literal: <reason>` is allowed: it names a country as a *jurisdiction*
whose rules the code implements (e.g. the Italian VAT and e-invoicing rules for vendors
established in Italy, VEN.12), not as the platform's own country.
"""

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("apps", "mpmv")
EXCLUDED_DIRS = frozenset({"tests", "migrations"})
# The current platform country. Language codes are lower case ("it") and are not flagged.
FORBIDDEN = frozenset({"IT"})
JURISDICTION_PRAGMA = re.compile(r"#\s*country-literal:\s*\S")


def country_literals(source: str) -> list[int]:
    """Line numbers of string constants equal to a forbidden platform-country code."""
    lines = source.splitlines()
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.strip() in FORBIDDEN
        and not JURISDICTION_PRAGMA.search(lines[node.lineno - 1])
    ]


def source_files() -> Iterator[Path]:
    for top in SCANNED:
        for path in sorted((ROOT / top).rglob("*.py")):
            if not EXCLUDED_DIRS.intersection(path.relative_to(ROOT).parts):
                yield path


@pytest.mark.parametrize(
    "source",
    [
        'PLATFORM_COUNTRY = "IT"',
        "if vendor_country == 'IT':\n    pass",
        'rate = rates.get(country=" IT ")',
        'f"IT{suffix}"',
        'COUNTRY = "IT"  # country-literal:',  # a pragma needs a reason
    ],
)
def test_scanner_flags_the_platform_country_as_a_literal(source: str) -> None:
    assert country_literals(source) != []


@pytest.mark.parametrize(
    "source",
    [
        'LANGUAGES = [("it", "Italiano")]',
        'country = operator.country  # e.g. "IT" in a comment',
        '"""Docstring mentioning IT is fine."""',
        'territory = "IT-LIVIGNO"',
        'ITALY = "IT"  # country-literal: Italian VAT rules for vendors established in Italy',
        "x = 1",
    ],
)
def test_scanner_ignores_language_codes_comments_and_longer_strings(source: str) -> None:
    assert country_literals(source) == []


def test_scan_covers_the_application_and_skips_tests_and_migrations() -> None:
    files = [p.relative_to(ROOT).as_posix() for p in source_files()]
    assert "apps/core/models.py" in files
    assert "mpmv/settings/base.py" in files
    assert not any("/tests/" in f or "/migrations/" in f for f in files)


def test_no_platform_country_literal_in_application_code() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}:{line}"
        for path in source_files()
        for line in country_literals(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        "Hard-coded platform country found; read it from current_operator() instead: "
        + ", ".join(offenders)
    )
