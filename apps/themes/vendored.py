"""Vendored third-party JavaScript (THM.3, spec 14 §14.5).

htmx and the CSP build of Alpine.js are served from `'self'` under `static/vendor/`, never from a
CDN. `VENDORED.toml` in that directory records, for each file, the exact version, the https URL
it was downloaded from, its SHA-256 and its licence. `check_vendored` compares the directory with
the manifest; a unit test runs it, so `make test` and CI fail on any drift.
"""

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent / "static" / "vendor"
MANIFEST = "VENDORED.toml"
ALLOWED_LICENCES = frozenset({"0BSD", "MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0"})
_EXACT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class VendoredFile:
    path: str
    name: str
    version: str
    source: str
    sha256: str
    license: str


def read_vendored(root: Path = VENDOR_DIR) -> list[VendoredFile]:
    """The entries of `root/VENDORED.toml`; ValueError when it cannot be read."""
    try:
        data = tomllib.loads((root / MANIFEST).read_text(encoding="utf-8"))
        return [VendoredFile(**entry) for entry in data.get("file", [])]
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, TypeError) as exc:
        raise ValueError(f"{MANIFEST} cannot be read: {exc}") from exc


def _entry_errors(entry: VendoredFile, allowed: frozenset[str]) -> list[str]:
    errors = []
    if not _EXACT_VERSION_RE.match(entry.version):
        errors.append(f"version {entry.version!r} is not an exact x.y.z")
    if entry.license not in allowed:
        errors.append(f"licence {entry.license!r} is not allowed")
    if not entry.source.startswith("https://"):
        errors.append("source must be an https URL")
    elif _EXACT_VERSION_RE.match(entry.version) and entry.version not in entry.source:
        errors.append(f"source does not pin version {entry.version}")
    return errors


def check_vendored(
    root: Path = VENDOR_DIR, *, allowed: frozenset[str] = ALLOWED_LICENCES
) -> list[str]:
    """Every difference between `root` and its manifest, as `<file>: <problem>` lines.

    `allowed` is the licence allow list: the site's own (`ALLOWED_LICENCES`) by default; test
    tooling that is never served (THM.5, axe-core) passes a wider one.
    """
    try:
        entries = read_vendored(root)
    except ValueError as exc:
        return [str(exc)]
    errors: list[str] = []
    listed = set()
    for entry in entries:
        listed.add(entry.path)
        problems = _entry_errors(entry, allowed)
        path = root / entry.path
        if not path.is_file():
            problems.append("listed in VENDORED.toml but missing")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != entry.sha256:
            problems.append("SHA-256 does not match VENDORED.toml")
        errors.extend(f"{entry.path}: {problem}" for problem in problems)
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative != MANIFEST and relative not in listed:
            errors.append(f"{relative}: not listed in VENDORED.toml")
    return errors
