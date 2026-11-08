"""Static files finder for the built theme CSS (THM.2, spec 14 §14.5).

Serves `settings.MPMV_THEME_BUILD_DIR` (written by `python -m apps.themes.build`), so
`{% static "themes/<slug>/theme.css" %}` works under `runserver` and `collectstatic` copies and
hashes the files. Unlike a `STATICFILES_DIRS` entry, a directory that does not exist yet (before
the first `make assets`) is not a system check warning: it simply holds no files.
"""

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Literal, overload

from django.conf import settings
from django.contrib.staticfiles import utils
from django.contrib.staticfiles.finders import BaseFinder
from django.core.checks import CheckMessage
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.storage import FileSystemStorage, Storage
from django.utils._os import safe_join


def build_dir() -> Path:
    return Path(settings.MPMV_THEME_BUILD_DIR)


class ThemeAssetsFinder(BaseFinder):
    def check(self, **kwargs: Any) -> list[CheckMessage]:
        return []

    @overload
    def find(self, path: str, find_all: Literal[False] = False) -> str | None: ...
    @overload
    def find(self, path: str, find_all: Literal[True]) -> list[str]: ...
    def find(self, path: str, find_all: bool = False) -> str | list[str] | None:
        root = build_dir()
        try:
            candidate = Path(safe_join(str(root), path))
        except SuspiciousFileOperation:  # outside the build directory
            candidate = None
        if candidate is not None and candidate.is_file():
            return [str(candidate)] if find_all else str(candidate)
        return [] if find_all else None

    def list(self, ignore_patterns: Iterable[str] | None) -> Iterator[tuple[str, Storage]]:
        root = build_dir()
        if not root.is_dir():
            return
        storage = FileSystemStorage(location=str(root))
        for path in utils.get_files(storage, list(ignore_patterns or [])):
            yield path, storage
