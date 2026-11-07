from typing import Any

from django.apps import AppConfig
from django.core import checks
from django.core.signals import setting_changed


def _settings_changed(*, setting: str, **kwargs: Any) -> None:
    if setting.startswith("MPMV_THEME"):
        from .services import clear_caches

        clear_caches()


class ThemesConfig(AppConfig):
    """Frontend themes (THM.1, spec 14): registry, template loader and per-site selection."""

    name = "apps.themes"
    label = "themes"

    # Audit policy (SEC.4, docs/dev/audit-trail.md).
    audited_models = ("themesettings",)
    unaudited_models: dict[str, str] = {}

    def ready(self) -> None:
        from .checks import check_site_themes, check_themes

        checks.register(check_themes)
        checks.register(check_site_themes, checks.Tags.database)
        setting_changed.connect(_settings_changed, dispatch_uid="themes.settings_changed")
