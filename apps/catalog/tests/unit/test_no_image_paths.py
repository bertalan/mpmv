"""ARC.15 guard (CAT.4): catalog code never reads an image's file path.

Only Django storage APIs (`file.open()`, `file.url`) and Wagtail renditions may touch image
files, so that ARC.15 can swap the storage backend without a code change. A local filesystem
path (`image.file.path`, `storage.path(...)`, `MEDIA_ROOT`) would silently break on object storage.
"""

import re
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[2]
FORBIDDEN = re.compile(
    r"\.(?:file|image|upload|photo|picture)\.path\b"  # image.file.path, upload.path ...
    r"|storage\.path\("  # default_storage.path(...)
    r"|\bMEDIA_ROOT\b"
    r"|\bos\.path\b[^\n]*\b(?:file|image)\b"
)


def sources() -> list[Path]:
    return [
        p for p in CATALOG.rglob("*.py") if "migrations" not in p.parts and "tests" not in p.parts
    ]


def test_catalog_code_never_reads_image_file_paths() -> None:
    found = [
        f"{p.relative_to(CATALOG)}:{n}: {line.strip()}"
        for p in sources()
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if FORBIDDEN.search(line)
    ]
    assert not found, "use storage APIs or renditions, never file paths (ARC.15): " + "; ".join(
        found
    )


def test_the_guard_matches_what_it_forbids() -> None:
    for bad in ("x = image.file.path", "default_storage.path(name)", "settings.MEDIA_ROOT"):
        assert FORBIDDEN.search(bad), bad
    for good in ("category.path", "image.file.url", "image.file.open('rb')"):
        assert not FORBIDDEN.search(good), good
