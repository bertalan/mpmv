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
    tokens.toml       design tokens (THM.2)
    static/themes/base/css/   CSS sources, one cascade layer per rule set (THM.2)
  <slug>/             a vertical theme, `parent = "base"` (or another theme descending from base)
```

THM.2 adds `tokens.toml` and `static/themes/<slug>/css/` (below), THM.3 the layouts, THM.5
`LICENSES.toml`.

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
| `engine.py` | Pure functions: `parse_manifest`, `theme_chain`, `build_registry` / `ThemeRegistry`, `template_candidates`, `is_themable`, `pick_theme`; tokens: `parse_tokens`, `resolve_tokens`, `contrast_ratio`, `check_tokens`, `render_tokens_css`, `build_theme_css` |
| `services.py` | `get_registry()` (manifests read once per process), `active_theme(request)`, `use_theme(...)`, `current_chain()`, `site_theme_choice(site)`, `set_site_theme(...)` (audited, `dry_run`) |
| `contract.py` | `TOKEN_CONTRACT`: required tokens and contrast pairs |
| `build.py` | The CSS build (`python -m apps.themes.build`), no Django settings needed |
| `finders.py` | `ThemeAssetsFinder`: serves `build/themes/` as static files |
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

`ThemeLoader` caches compiled templates per `(theme, name)`. Its `get_dirs()` returns the themes
directory, so the dev autoreloader watches it and clears the cache when a theme template
changes. Never wrap it in Django's `cached.Loader`: that cache is keyed by name only and would
serve one site's theme to another. The Wagtail and Django admin templates are never themed: the
prefixes in `engine.UNTHEMED_PREFIXES` (`admin/`, `wagtailadmin/` and the other Wagtail admin
apps, `wagtailcore/shared/`, `otp*/`, `procrastinate/`) and any template in a directory whose
name starts with `admin` (`accounts/admin/login.html`, the Django admin's login page). Do not put
public templates in such a directory.

Outside a request (emails, management commands) templates render with `MPMV_THEME_DEFAULT`.
Use `with use_theme("ride"):` to render with another theme, for example in a test.

## Choosing a site's theme

Selection order: the Wagtail Site's `ThemeSettings.theme`, then `MPMV_THEME_DEFAULT` (env, default
`base`), then `base`. Staff choose it in the staff admin under *Themes > Site themes*; each change
writes `themes.theme_settings.configured` to the audit log. "Environment default" clears the
choice. There is no per-request switch (it would split the page cache, SEO.6).

Never read the setting with Wagtail's `ThemeSettings.for_site` or `for_request`: they create a row
on first access, outside the audit trail. Use `site_theme_choice(site)` or `active_theme(request)`.

## Design tokens and the CSS build (THM.2)

`themes/<slug>/tokens.toml` sets the theme's tokens as nested TOML tables; a dotted name becomes a
custom property (`[color] text-muted = "#475569"` is `color.text-muted`, `--color-text-muted`).
Values are strings or numbers; a value may refer to another token with `"{color.blue.700}"`, also
inside a longer value (`"0 1px 3px {color.neutral.900}"`). Characters that could break out of a
CSS declaration (`; { } < > \`, comments, control characters) are rejected.

Three tiers, all in `tokens.toml`: primitives (`color.blue.700`, set by the theme), semantic
tokens (the contract below) and component tokens (`button.bg = "{color.accent}"`, derived in
`base`, overridable).

Resolution along the chain (`engine.resolve_tokens`): the tokens of `base`, then each child's
on top (child values win), then references are resolved. So a child that changes a primitive
changes every token derived from it. A child lists only the tokens it changes.

### Token contract

`apps/themes/contract.py` (`TOKEN_CONTRACT`) lists the required semantic tokens and the contrast
pairs of spec 14 §14.4: 4.5:1 for text pairs, 3:1 for focus, control borders and the accent as
a UI colour. `engine.check_tokens` reports missing tokens, unresolved references, pair tokens
that are not `#rgb`/`#rrggbb` colours, and pairs below their minimum (with the measured ratio,
truncated). `engine.contrast_ratio` is the WCAG 2.x formula. A unit test runs the contract over
every theme shipped in `themes/`.

### Building

```sh
make assets            # = uv run python -m apps.themes.build
```

For every registered theme it writes, under `build/themes/` (`MPMV_THEME_BUILD_DIR`,
git-ignored):

| File | Content |
|---|---|
| `themes/<slug>/tokens.css` | `@layer tokens{:root{--…}}`, the resolved tokens |
| `themes/<slug>/theme.css` | the layer order statement, the tokens, then the CSS sources of the chain from `base` down, minified |

CSS sources are `themes/<slug>/static/themes/<slug>/css/*.css`, concatenated in file-name order
(prefix them `00-`, `10-`, …). Put every rule inside one of the layers
`reset, tokens, base, layout, components, utilities, overrides`; the build declares that order
first, so the cascade does not depend on the file order. Use only `var(--…)` for colours.

Any invalid manifest, invalid `tokens.toml` or contract violation fails the build (exit 1, every
reason listed) and nothing is written. `make run` builds first. In the image the build runs in
the build stage before `collectstatic`; `apps.themes.finders.ThemeAssetsFinder` serves
`build/themes/` to `runserver` and `collectstatic`, which hashes `theme.css`. Link it with
`{% static "themes/<slug>/theme.css" %}` (THM.3 adds it to the layouts).

The build module needs no Django settings: it reuses the pure functions of `engine.py` and the
manifest reader that `services.get_registry()` also uses.

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
`apps.themes.tests.builders.write_theme` (its `tokens=` and `css=` write `tokens.toml` and CSS
sources; `shipped_tokens()` is a token set that meets the contract); changing a `MPMV_THEME*`
setting clears the registry cache. `SiteFactory` and `ThemeSettingsFactory` are in `apps.themes.tests.factories`.
