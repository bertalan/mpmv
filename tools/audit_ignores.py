"""Turn the reviewed pip-audit ignore list into `--ignore-vuln` flags (SEC.5).

pip-audit reports no severity, so `make audit` treats every known vulnerability as blocking.
An advisory with no fixed release can be ignored only through tools/pip_audit_ignore.toml, and
only while its entry is complete and not expired: an expired entry fails the audit, so the
exception gets reviewed again instead of living forever.

Usage: python tools/audit_ignores.py [path]   # prints one flag or value per line, exit 1 if invalid
"""

import datetime as dt
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

DEFAULT = Path(__file__).resolve().parent / "pip_audit_ignore.toml"
REQUIRED = ("id", "package", "task", "reason", "expires")
ADVISORY_ID = re.compile(r"(GHSA|PYSEC|CVE|OSV)-[A-Za-z0-9-]+")
TASK_ID = re.compile(r"[A-Z]+\.\d+")
MAX_DAYS = 90


class IgnoreListError(ValueError):
    pass


def load(path: Path) -> list[dict[str, Any]]:
    entries = tomllib.loads(path.read_text(encoding="utf-8")).get("ignore", [])
    if not isinstance(entries, list):
        raise IgnoreListError(f"{path}: `ignore` must be an array of tables ([[ignore]])")
    return entries


def validate(entries: list[dict[str, Any]], *, today: dt.date) -> None:
    for entry in entries:
        label = entry.get("id", "<no id>")
        for field in REQUIRED:
            value = entry.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise IgnoreListError(f"{label}: missing {field}")
        if not ADVISORY_ID.fullmatch(entry["id"]):
            raise IgnoreListError(f"{label}: not a GHSA, PYSEC, CVE or OSV advisory id")
        if not TASK_ID.fullmatch(entry["task"]):
            raise IgnoreListError(f"{label}: task must be a backlog id such as SEC.5")
        expires = entry["expires"]
        if not isinstance(expires, dt.date) or isinstance(expires, dt.datetime):
            raise IgnoreListError(f"{label}: expires must be a TOML date (YYYY-MM-DD)")
        if expires <= today:
            raise IgnoreListError(f"{label}: expired on {expires}; review it again")
        if (expires - today).days > MAX_DAYS:
            raise IgnoreListError(f"{label}: expires more than {MAX_DAYS} days ahead")


def arguments(entries: list[dict[str, Any]]) -> list[str]:
    return [part for entry in entries for part in ("--ignore-vuln", entry["id"])]


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT
    try:
        entries = load(path)
        validate(entries, today=dt.date.today())
    except (IgnoreListError, tomllib.TOMLDecodeError) as exc:
        print(f"pip-audit ignore list: {exc}", file=sys.stderr)
        return 1
    for part in arguments(entries):
        print(part)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
