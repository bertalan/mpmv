"""Test themes written to a temporary directory (THM.1).

Tests point `settings.MPMV_THEMES_DIR` at a `tmp_path` and write the themes they need with
`write_theme`, so they never depend on the themes shipped in `themes/`.
"""

from collections.abc import Mapping
from pathlib import Path

BASE = "base"
SHIPPED_THEMES = Path(__file__).resolve().parents[3] / "themes"


def shipped_tokens(slug: str = BASE) -> str:
    """The `tokens.toml` text of a theme shipped in `themes/`: a set that meets the contract."""
    return (SHIPPED_THEMES / slug / "tokens.toml").read_text(encoding="utf-8")


def shipped_templates(slug: str = BASE) -> dict[str, str]:
    """Every template of a theme shipped in `themes/` (relative name -> source).

    A test `base` that renders real pages needs the layouts (THM.3):
    `write_theme(root, "base", templates={**shipped_templates(), ...})`.
    """
    root = SHIPPED_THEMES / slug / "templates"
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.html"))
    }


def write_theme(
    root: Path,
    slug: str,
    *,
    parent: str | None = BASE,
    templates: Mapping[str, str] | None = None,
    manifest: str | None = None,
    tokens: str | None = None,
    css: Mapping[str, str] | None = None,
) -> Path:
    """Write `root/<slug>/theme.toml` and its `templates/`; return the theme directory.

    `parent` defaults to `base` (and is left out for `base` itself); `manifest` replaces the
    generated `theme.toml` text, for invalid-manifest tests. `tokens` is the `tokens.toml` text
    and `css` maps file names to the sources under `static/themes/<slug>/css/` (THM.2).
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
    if tokens is not None:
        (theme_dir / "tokens.toml").write_text(tokens, encoding="utf-8")
    for name, source in (css or {}).items():
        path = theme_dir / "static" / "themes" / slug / "css" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return theme_dir
