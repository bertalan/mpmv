"""CSS framework foundation of a theme (THM.13, spec 14 §14.5, "Bootstrap in `ride`").

A theme that declares `foundation = "bootstrap-5.3"` ships the official Bootstrap dist files,
unchanged, under `static/themes/<slug>/vendor/bootstrap/`, with a `VENDORED.toml` in the format
of `apps/themes/vendored.py`. `foundation_files` returns them after the hash check, so a missing
or tampered file stops the build, the gates and `manage.py check`.
"""

import re
from pathlib import Path

from .engine import ThemeError
from .vendored import check_vendored

BOOTSTRAP_DIR = "vendor/bootstrap"
BOOTSTRAP_CSS = "bootstrap.min.css"
BOOTSTRAP_JS = "bootstrap.bundle.min.js"
BUNDLE_OUTPUT = "vendor/bootstrap.bundle.min.js"  # path of the copied bundle under the build


def foundation_dir(theme_dir: Path, slug: str) -> Path:
    return theme_dir / "static" / "themes" / slug / BOOTSTRAP_DIR


def foundation_problems(theme_dir: Path, slug: str, foundation: str) -> list[str]:
    """Why the vendored framework of `slug` cannot be used (empty when it is intact)."""
    directory = foundation_dir(theme_dir, slug)
    if not directory.is_dir():
        return [f"foundation {foundation!r}: {directory.relative_to(theme_dir)} is missing"]
    problems = check_vendored(directory)
    for name in (BOOTSTRAP_CSS, BOOTSTRAP_JS):
        if not (directory / name).is_file():
            problems.append(f"{name}: missing")
    return problems


def foundation_files(theme_dir: Path, slug: str, foundation: str) -> tuple[str, str]:
    """The (CSS, JavaScript) text of the framework; ThemeError when it is missing or altered."""
    problems = foundation_problems(theme_dir, slug, foundation)
    if problems:
        raise ThemeError("; ".join(problems))
    directory = foundation_dir(theme_dir, slug)
    return (
        _without_source_map((directory / BOOTSTRAP_CSS).read_text(encoding="utf-8")),
        _without_source_map((directory / BOOTSTRAP_JS).read_text(encoding="utf-8")),
    )


SOURCE_MAP_COMMENT = re.compile(
    r"^\s*(?://|/\*)# sourceMappingURL=\S+(?:\s*\*/)?\s*$", re.MULTILINE
)


def _without_source_map(text: str) -> str:
    """The vendored text without its `sourceMappingURL` comment: no `.map` file is vendored, and
    the manifest storage of `collectstatic` stops the image build on a reference to a missing
    file. The vendored file itself stays byte-identical; only the built copy drops the line."""
    return SOURCE_MAP_COMMENT.sub("", text).rstrip("\n") + "\n"
