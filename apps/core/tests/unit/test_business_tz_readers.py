"""ARC.27 (D-31): `business_tz()` next to `current_operator()` is the only reader of the business
time zone. The setting `BUSINESS_TIME_ZONE` (from `MPMV_BUSINESS_TIME_ZONE`, UTC when unset) stays a
bootstrap value: only the operator field's default, the helper's fallback and the start-up check
may read it; the job scheduler reads `business_tz()` too, once per worker process."""

import ast
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
ALLOWED = {
    "apps/core/models.py",  # configured_time_zone_name(): the operator field's default
    "apps/core/checks.py",  # check_business_time_zone(): core.E931 on an unknown name
}
# Calendar days read in UTC on purpose (F4 of the PR #1107 review): file -> why.
UTC_DAYS = {
    "apps/vendors/services.py": "a once-a-day idempotency key for an application e-mail",
}


def _sources() -> Iterator[tuple[str, str]]:
    for path in sorted((ROOT / "apps").rglob("*.py")):
        if "tests" in path.parts or "migrations" in path.parts:
            continue
        yield str(path.relative_to(ROOT)), path.read_text(encoding="utf-8")


def _reads_the_setting(tree: ast.AST) -> bool:
    """`settings.BUSINESS_TIME_ZONE` or `getattr(settings, "BUSINESS_TIME_ZONE")`; prose that
    names the environment variable is not a read."""
    return any(
        (isinstance(node, ast.Attribute) and node.attr == "BUSINESS_TIME_ZONE")
        or (isinstance(node, ast.Constant) and node.value == "BUSINESS_TIME_ZONE")
        for node in ast.walk(tree)
    )


def test_no_code_reads_the_setting_directly() -> None:
    readers = [name for name, text in _sources() if _reads_the_setting(ast.parse(text))]
    assert [reader for reader in readers if reader not in ALLOWED] == []


def _utc_day_reads(tree: ast.AST) -> Iterator[int]:
    """`localdate()` without a zone (the active zone is UTC) and `.now().date()`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        zoned = len(node.args) >= 2 or any(k.arg == "timezone" for k in node.keywords)
        if name == "localdate" and not zoned:
            yield node.lineno
        if (
            name == "date"
            and isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Call)
            and getattr(func.value.func, "attr", "") == "now"
            and not func.value.args
        ):
            yield node.lineno


def test_every_calendar_day_is_read_in_the_business_time_zone() -> None:
    """F4: a day (today, a document date) is the business day, `business_tz()`, never UTC's."""
    found = [
        f"{name}:{line}"
        for name, text in _sources()
        if name not in UTC_DAYS
        for line in _utc_day_reads(ast.parse(text))
    ]
    assert found == []


def test_the_scans_catch_what_they_forbid() -> None:
    assert _reads_the_setting(ast.parse("x = settings.BUSINESS_TIME_ZONE"))
    assert not _reads_the_setting(ast.parse('"""Set MPMV_BUSINESS_TIME_ZONE."""'))
    utc = "timezone.localdate()\ntimezone.localdate(moment)\ntimezone.now().date()\n"
    assert list(_utc_day_reads(ast.parse(utc))) == [1, 2, 3]
    zoned = "timezone.localdate(timezone=zone)\ntimezone.localdate(moment, zone)\n"
    assert list(_utc_day_reads(ast.parse(zoned))) == []
