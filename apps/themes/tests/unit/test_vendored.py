"""THM.3: vendored JavaScript (spec 14 §14.5): htmx and the Alpine CSP build, self-hosted.

Every file under `apps/themes/static/vendor/` is listed in `VENDORED.toml` with an exact
version, its https source URL, its SHA-256 and an allowed licence, and the file on disk matches
the recorded hash. Nothing is loaded from a CDN at runtime.
"""

import hashlib
from pathlib import Path

import pytest

from apps.themes.vendored import MANIFEST, VENDOR_DIR, check_vendored, read_vendored

HTMX = b"/* pretend htmx */\n"
GOOD = """
[[file]]
path = "htmx.min.js"
name = "htmx"
version = "2.0.4"
source = "https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js"
sha256 = "{sha}"
license = "0BSD"
"""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def vendor_dir(tmp_path: Path, manifest: str, files: dict[str, bytes]) -> Path:
    root = tmp_path / "vendor"
    root.mkdir()
    (root / MANIFEST).write_text(manifest, encoding="utf-8")
    for name, data in files.items():
        (root / name).write_bytes(data)
    return root


def test_the_shipped_vendored_files_match_their_recorded_hashes() -> None:
    assert check_vendored(VENDOR_DIR) == []


def test_htmx_and_the_alpine_csp_build_are_vendored_with_exact_versions() -> None:
    entries = {entry.name: entry for entry in read_vendored(VENDOR_DIR)}
    assert set(entries) >= {"htmx", "alpinejs-csp"}
    assert entries["htmx"].version.startswith("2.")
    assert "@alpinejs/csp@" in entries["alpinejs-csp"].source
    for entry in entries.values():
        assert entry.version in entry.source
        assert entry.source.startswith("https://")


def test_a_matching_directory_passes(tmp_path: Path) -> None:
    root = vendor_dir(tmp_path, GOOD.format(sha=sha(HTMX)), {"htmx.min.js": HTMX})
    assert check_vendored(root) == []


def test_a_changed_file_fails_the_hash_check(tmp_path: Path) -> None:
    root = vendor_dir(tmp_path, GOOD.format(sha=sha(HTMX)), {"htmx.min.js": HTMX + b"x"})
    assert check_vendored(root) == ["htmx.min.js: SHA-256 does not match VENDORED.toml"]


def test_a_listed_file_that_is_missing_fails(tmp_path: Path) -> None:
    root = vendor_dir(tmp_path, GOOD.format(sha=sha(HTMX)), {})
    assert check_vendored(root) == ["htmx.min.js: listed in VENDORED.toml but missing"]


def test_an_unlisted_file_fails(tmp_path: Path) -> None:
    files = {"htmx.min.js": HTMX, "extra.js": b"1"}
    root = vendor_dir(tmp_path, GOOD.format(sha=sha(HTMX)), files)
    assert check_vendored(root) == ["extra.js: not listed in VENDORED.toml"]


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (('version = "2.0.4"', 'version = "^2.0"'), "version '^2.0' is not an exact x.y.z"),
        (('license = "0BSD"', 'license = "GPL-3.0"'), "licence 'GPL-3.0' is not allowed"),
        (("https://unpkg.com", "http://unpkg.com"), "source must be an https URL"),
        (("htmx.org@2.0.4", "htmx.org@latest"), "source does not pin version 2.0.4"),
    ],
)
def test_entries_must_pin_an_exact_version_from_https_with_an_allowed_licence(
    tmp_path: Path, change: tuple[str, str], error: str
) -> None:
    manifest = GOOD.format(sha=sha(HTMX)).replace(*change)
    root = vendor_dir(tmp_path, manifest, {"htmx.min.js": HTMX})
    assert check_vendored(root) == [f"htmx.min.js: {error}"]


def test_an_unreadable_manifest_fails(tmp_path: Path) -> None:
    root = vendor_dir(tmp_path, "[[file]\n", {})
    [error] = check_vendored(root)
    assert error.startswith("VENDORED.toml cannot be read")
