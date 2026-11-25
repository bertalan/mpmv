"""System checks of the themes (THM.1, spec 14 §14.3), registered in `ThemesConfig.ready`.

- themes.E001: a theme directory has an invalid or missing `theme.toml`, or a chain that does
  not end in `base` (unknown parent, cycle).
- themes.E002: `MPMV_THEME_DEFAULT` names no registered theme.
- themes.E003 (database): a site's `ThemeSettings` names a theme that is not registered (for
  example one removed since it was chosen). Run by `migrate` and `check --database default`.
- themes.E004: the `base` theme is missing or invalid.
- themes.W006 (database): a site's stored palette fails the contrast check against the registered
  themes (a theme was added since), so it is not applied.
- themes.E005: a theme's `foundation` files are missing or differ from their `VENDORED.toml`.
"""

from collections.abc import Iterable, Sequence
from typing import Any

from django.apps import AppConfig
from django.core import checks
from django.db import DatabaseError, connections, transaction

from apps.core.migration_safety import migrations_pending

from .engine import MISSING_BASE
from .foundation import foundation_problems
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
    for slug, manifest in registry.manifests.items():
        if manifest.foundation is None:
            continue
        for problem in foundation_problems(themes_dir() / slug, slug, manifest.foundation):
            messages.append(
                checks.Error(
                    f"Theme {slug!r}: {problem}",
                    hint="Restore the vendored framework files (spec 14 §14.5).",
                    id="themes.E005",
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


def _migrations_pending(alias: str) -> bool:
    """True while any `themes` migration is unapplied on this database."""
    return migrations_pending("themes", database=alias)


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
        if _migrations_pending(alias):
            continue  # `migrate` checks first: the table may lack columns the migration adds
        try:
            with transaction.atomic(using=alias):
                stored = list(ThemeSettings.objects.using(alias).select_related("site", "palette"))
        except DatabaseError:
            continue  # not migrated yet
        for setting in stored:
            if setting.palette is not None and (problems := setting.palette.violations()):
                messages.append(
                    checks.Warning(
                        f"Site {setting.site.hostname!r} has palette {setting.palette.slug!r}, "
                        f"which fails the contrast check and is not applied: {problems[0]}",
                        hint="Choose another palette, or fix this one (spec 14 §14.12). The "
                        "site renders in its theme's own colours meanwhile.",
                        id="themes.W006",
                    )
                )
            if setting.theme and setting.theme not in registry:
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
