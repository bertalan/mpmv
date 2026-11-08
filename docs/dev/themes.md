# Themes

How the frontend themes work in code (THM.1). The design is in
[spec 14](../spec/14-frontend-themes.md) and the toolchain decision in
[ADR-0008](../adr/0008-frontend-toolchain-and-themes.md).

## Layout

```
themes/
  base/               the root theme: complete on its own
    theme.toml        manifest
    templates/        template overrides (same relative paths as the templates they replace)
  <slug>/             a vertical theme, `parent = "base"` (or another theme descending from base)
```

THM.2 adds `tokens.toml` and the CSS sources, THM.3 the layouts, THM.5 `LICENSES.toml`.

### `theme.toml`

| Key | Required | Meaning |
|---|---|---|
| `slug` | yes | `[a-z][a-z0-9-]{0,31}`, equal to the directory name |
| `name` | yes | Name shown to staff |
| `version` | yes | Version string of the theme |
| `parent` | yes, except `base` | Slug of the parent theme; the chain must end in `base` |
| `description` | no | One line |
| `direction` | no | Only `ltr` |

Unknown keys are rejected, so a typo fails instead of being ignored.

## Code map (`apps/themes`)

| Module | What it holds |
|---|---|
| `engine.py` | Pure functions: `parse_manifest`, `theme_chain`, `build_registry` / `ThemeRegistry`, `template_candidates`, `is_themable`, `pick_theme`. THM.2 adds the token functions here. |
| `services.py` | `get_registry()` (manifests read once per process), `active_theme(request)`, `use_theme(...)`, `current_chain()`, `site_theme_choice(site)`, `set_site_theme(...)` (audited, `dry_run`) |
| `loaders.py` | `ThemeLoader`, the first template loader |
| `middleware.py` | `ThemeMiddleware`: each request renders with its site's theme |
| `context_processors.py` | `theme` in every template (`{{ theme.slug }}`, `{{ theme.name }}`) |
| `models.py` | `ThemeSettings`, a Wagtail site setting with `theme` |
| `checks.py` | `themes.E001` to `themes.E004` |

## Template resolution

A template name is looked up in the active theme, then each parent up to `base`, then the project
`templates/` directory and the app template directories. To override a template, put a file at
the same relative path under `themes/<slug>/templates/`. A theme template may extend its own name
(`{% extends "cms/home_page.html" %}`) to wrap the parent's version.

`ThemeLoader` caches compiled templates per `(theme, name)` and clears the cache on the dev
autoreload. Never wrap it in Django's `cached.Loader`: that cache is keyed by name only and would
serve one site's theme to another. The Wagtail and Django admin templates (`admin/`,
`wagtailadmin/`, the other `wagtail*/` admin prefixes in `engine.UNTHEMED_PREFIXES`) are never
themed.

Outside a request (emails, management commands) templates render with `MPMV_THEME_DEFAULT`.
Use `with use_theme("ride"):` to render with another theme, for example in a test.

## Choosing a site's theme

Selection order: the Wagtail Site's `ThemeSettings.theme`, then `MPMV_THEME_DEFAULT` (env, default
`base`), then `base`. Staff choose it in the staff admin under *Themes > Site themes*; each change
writes `themes.theme_settings.configured` to the audit log. "Environment default" clears the
choice. There is no per-request switch (it would split the page cache, SEO.6).

Never read the setting with Wagtail's `ThemeSettings.for_site` or `for_request`: they create a row
on first access, outside the audit trail. Use `site_theme_choice(site)` or `active_theme(request)`.

## System checks

| Id | Fails when |
|---|---|
| `themes.E001` | A theme directory has a missing or invalid `theme.toml`, or its chain does not end in `base` |
| `themes.E002` | `MPMV_THEME_DEFAULT` names no registered theme |
| `themes.E003` | A stored site setting names a theme that is not registered (database check: run by `migrate` and `manage.py check --database default`) |
| `themes.E004` | The `base` theme is missing or invalid |

## Imports

`apps.themes` imports only `apps.core` and Wagtail, and no app imports `apps.themes`; both rules
are import-linter contracts in `pyproject.toml`. Apps use the themes only through templates and
the `theme` context. THM.11 (vendor branding) will allow `apps.vendors` to import the pure palette
functions of `apps.themes.engine`.

## Tests

Point `settings.MPMV_THEMES_DIR` at a temporary directory and write the themes a test needs with
`apps.themes.tests.builders.write_theme`; changing a `MPMV_THEME*` setting clears the registry
cache. `SiteFactory` and `ThemeSettingsFactory` are in `apps.themes.tests.factories`.
