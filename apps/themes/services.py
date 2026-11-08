"""Theme registry, active theme and per-site selection (THM.1, spec 14 §14.3).

- `get_registry()` reads every `<MPMV_THEMES_DIR>/<slug>/theme.toml` once per process (cached
  per directory; changing a `MPMV_THEME*` setting clears it).
- `active_theme(request)`: the Wagtail Site's `ThemeSettings`, then `MPMV_THEME_DEFAULT`, then
  `base`; resolved once per request.
- `use_theme(request_or_slug)` sets the theme the template loader uses in this context;
  `ThemeMiddleware` wraps every request in it. Outside a request the default theme applies.
- `set_site_theme(...)` is the only writer of `ThemeSettings` (audited, dry-run).
"""

import contextlib
import functools
import tomllib
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from django.conf import settings
from django.http import HttpRequest
from wagtail.models import Site

from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import save_audited

from .engine import (
    MANIFEST_FILE,
    ThemeError,
    ThemeManifest,
    ThemeRegistry,
    build_registry,
    pick_theme,
)
from .models import ThemeSettings

THEME_SETTINGS_MODEL = "themes.themesettings"
THEME_CONFIGURED = "themes.theme_settings.configured"

_REQUEST_ATTR = "_mpmv_theme"

# What the template loader renders for: a request (theme resolved lazily, on the first themed
# template), an explicit theme slug, or None (the environment default).
_active: ContextVar[HttpRequest | str | None] = ContextVar("mpmv_theme", default=None)


# --- Registry


def themes_dir() -> Path:
    return Path(settings.MPMV_THEMES_DIR)


def _read_sources(root: Path) -> dict[str, Mapping[str, object] | ThemeError]:
    sources: dict[str, Mapping[str, object] | ThemeError] = {}
    if not root.is_dir():
        return sources
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.name.startswith((".", "_")):
            continue
        manifest = path / MANIFEST_FILE
        try:
            sources[path.name] = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except FileNotFoundError:
            sources[path.name] = ThemeError(f"{MANIFEST_FILE} is missing")
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            sources[path.name] = ThemeError(f"{MANIFEST_FILE} cannot be read: {exc}")
    return sources


@functools.cache
def load_registry(root: Path) -> ThemeRegistry:
    """The themes under `root`, read once per process (see `clear_caches`)."""
    return build_registry(_read_sources(root))


def get_registry() -> ThemeRegistry:
    return load_registry(themes_dir())


def clear_caches() -> None:
    """Forget the manifests read from disk (tests, settings changes)."""
    load_registry.cache_clear()


def default_theme() -> str:
    """`MPMV_THEME_DEFAULT`: the theme of a site with no setting (checked by themes.E002)."""
    return str(settings.MPMV_THEME_DEFAULT)


# --- Selection


def site_theme_choice(site: Site) -> str:
    """The theme stored for `site`, or "" (environment default). Never creates a row."""
    choice = ThemeSettings.objects.filter(site=site).values_list("theme", flat=True).first()
    return choice or ""


def active_theme(request: HttpRequest) -> ThemeManifest:
    """The request's theme: Wagtail Site setting, then `MPMV_THEME_DEFAULT`, then `base`.

    Resolved once per request. A stored theme that is no longer registered falls back to the
    default (and fails the themes.E003 check).
    """
    cached: ThemeManifest | None = getattr(request, _REQUEST_ATTR, None)
    if cached is not None:
        return cached
    registry = get_registry()
    site = Site.find_for_request(request)
    choice = site_theme_choice(site) if site is not None else ""
    manifest = registry[pick_theme(choice, default_theme(), registry)]
    setattr(request, _REQUEST_ATTR, manifest)
    return manifest


@contextlib.contextmanager
def use_theme(target: HttpRequest | str) -> Iterator[None]:
    """Render templates with the theme of `target` (a request, or a registered theme slug)."""
    if isinstance(target, str):
        get_registry().chain(target)  # LookupError for an unknown theme
    token = _active.set(target)
    try:
        yield
    finally:
        _active.reset(token)


def current_theme() -> str:
    """The slug of the theme templates are rendered with in this context."""
    target = _active.get()
    if isinstance(target, str):
        return target
    if target is not None:
        return active_theme(target).slug
    return pick_theme("", default_theme(), get_registry())


def current_chain() -> tuple[str, ...]:
    """The active theme and its parents up to `base`, where the loader looks for templates."""
    return get_registry().chain(current_theme())


# --- Audited writes


def theme_snapshot(setting: ThemeSettings) -> dict[str, Any]:
    return {"site": setting.site.hostname, "theme": setting.theme}


def set_site_theme(
    site: Site, theme: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Choose the theme of a Wagtail Site ("" for the environment default), audited.

    Raises ThemeError for a theme that is not registered.
    """
    if theme and theme not in get_registry():
        raise ThemeError(f"unknown theme {theme!r}")
    setting = ThemeSettings.objects.filter(site=site).first() or ThemeSettings(site=site)
    setting.theme = theme
    return save_audited(
        setting,
        theme_snapshot,
        key=str(site.pk),
        ctx=ctx,
        created=THEME_CONFIGURED,
        updated=THEME_CONFIGURED,
        dry_run=dry_run,
    )
