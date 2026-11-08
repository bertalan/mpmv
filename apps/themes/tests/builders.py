"""Test themes written to a temporary directory (THM.1).

Tests point `settings.MPMV_THEMES_DIR` at a `tmp_path` and write the themes they need with
`write_theme`, so they never depend on the themes shipped in `themes/`.
"""

from collections.abc import Mapping
from pathlib import Path

BASE = "base"


def write_theme(
    root: Path,
    slug: str,
    *,
    parent: str | None = BASE,
    templates: Mapping[str, str] | None = None,
    manifest: str | None = None,
) -> Path:
    """Write `root/<slug>/theme.toml` and its `templates/`; return the theme directory.

    `parent` defaults to `base` (and is left out for `base` itself); `manifest` replaces the
    generated `theme.toml` text, for invalid-manifest tests.
    """
    theme_dir = root / slug
    (theme_dir / "templates").mkdir(parents=True, exist_ok=True)
    if manifest is None:
        lines = [
            f'slug = "{slug}"',
            f'name = "{slug.title()} theme"',
            'version = "1.0.0"',
            'description = "A theme written by a test."',
            'direction = "ltr"',
        ]
        if parent is not None and slug != BASE:
            lines.append(f'parent = "{parent}"')
        manifest = "\n".join(lines) + "\n"
    (theme_dir / "theme.toml").write_text(manifest, encoding="utf-8")
    for name, source in (templates or {}).items():
        path = theme_dir / "templates" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return theme_dir
