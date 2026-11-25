"""ARC.27 (D-31): `business_tz()` next to `current_operator()` is the only reader of the business
time zone. The setting `BUSINESS_TIME_ZONE` (from `MPMV_BUSINESS_TIME_ZONE`, UTC when unset) stays a
bootstrap value: only the operator field's default and the helper's fallback may read it; the
job scheduler reads `business_tz()` too, once per worker process."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
ALLOWED = {
    "apps/core/models.py",  # default_business_time_zone(): a new operator row starts there
    "apps/core/services.py",  # business_tz(): the fallback before an operator exists
}


def test_no_code_reads_the_setting_directly() -> None:
    readers = sorted(
        str(path.relative_to(ROOT))
        for path in (ROOT / "apps").rglob("*.py")
        if "tests" not in path.parts
        and "migrations" not in path.parts
        and "BUSINESS_TIME_ZONE" in path.read_text(encoding="utf-8")
    )
    assert [reader for reader in readers if reader not in ALLOWED] == []
