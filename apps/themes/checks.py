"""System checks of the themes (THM.1, spec 14 §14.3), registered in `ThemesConfig.ready`.

- themes.E001: a theme directory has an invalid or missing `theme.toml`, or a chain that does
  not end in `base` (unknown parent, cycle).
- themes.E002: `MPMV_THEME_DEFAULT` names no registered theme.
- themes.E003 (database): a site's `ThemeSettings` names a theme that is not registered (for
  example one removed since it was chosen). Run by `migrate` and `check --database default`.
- themes.E004: the `base` theme is missing or invalid.
"""

from collections.abc import Iterable, Sequence
from typing import Any

from django.apps import AppConfig
from django.core import checks
from django.db import connections

from .engine import MISSING_BASE
from .services import default_theme, get_registry, themes_dir


def check_themes(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    registry = get_registry()
    messages: list[checks.CheckMessage] = []
    for error in registry.errors:
        if error == MISSING_BASE:
            messages.append(
                checks.Error(
                    f"No valid 'base' theme in {themes_dir()}.",
                    hint="Every theme descends from base (spec 14 §14.2).",
                    id="themes.E004",
                )
            )
        else:
            messages.append(
                checks.Error(
                    f"Invalid theme {error}",
                    hint="Fix theme.toml (spec 14 §14.2) or remove the directory.",
                    id="themes.E001",
                )
            )
    default = default_theme()
    if default not in registry:
        messages.append(
            checks.Error(
                f"MPMV_THEME_DEFAULT is {default!r}, which is not a registered theme.",
                hint=f"Use one of: {', '.join(registry.manifests) or 'none registered'}.",
                id="themes.E002",
            )
        )
    return messages


def check_site_themes(
    app_configs: Iterable[AppConfig] | None,
    databases: Sequence[str] | None = None,
    **kwargs: Any,
) -> list[checks.CheckMessage]:
    from .models import ThemeSettings

    registry = get_registry()
    messages: list[checks.CheckMessage] = []
    for alias in databases or ():
        if ThemeSettings._meta.db_table not in connections[alias].introspection.table_names():
            continue  # a partial migrate has not created the table yet
        stored = ThemeSettings.objects.using(alias).exclude(theme="").select_related("site")
        for setting in stored:
            if setting.theme not in registry:
                messages.append(
                    checks.Error(
                        f"Site {setting.site.hostname!r} uses theme {setting.theme!r}, "
                        "which is not registered.",
                        hint="Choose a registered theme for the site in the staff admin "
                        "(Themes > Site themes), or restore the theme's directory.",
                        id="themes.E003",
                    )
                )
    return messages
