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
    static/themes/base/js/    ES modules registering Alpine components (THM.3)
  <slug>/             a vertical theme, `parent = "base"` (or another theme descending from base)
```

THM.2 adds `tokens.toml` and `static/themes/<slug>/css/` (below), THM.3 the layouts and the
JS modules, THM.5 `LICENSES.toml`.

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
| `gates.py` | The theme quality gates (`python -m apps.themes.gates`, THM.5), no Django settings needed |
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
CSS declaration (`; { } < > \`, comments, control characters) and unbalanced quotes are rejected.
`check_tokens` checks each value again once references are resolved, since two harmless parts
can join into something that is not (`"x/"` and `"{a}*y"` make a comment).

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
`{% static "themes/<slug>/theme.css" %}` (`layouts/base.html` does). The build also copies
each theme's `static/themes/<slug>/js/*.js` modules to `themes/<slug>/js/`, as they are.

The build module needs no Django settings: it reuses the pure functions of `engine.py` and the
manifest reader that `services.get_registry()` also uses.

## Layouts and the page shell (THM.3)

Every public page extends a layout of `base` and fills its `content` block:

| Layout | For | Notes |
|---|---|---|
| `layouts/base.html` | CMS pages and every public page | `title` and `description` default to the CMS page's SEO fields; blocks `head`, `header`, `content`, `footer` |
| `layouts/account.html` | Customer and vendor areas, sign-in steps (MFA pages) | Extends `base.html`; pages fill `account_content`; the language switcher returns to the same path |
| `layouts/content.html` | Standard, legal and contact pages, articles, error pages | Extends `base.html`; one column at `--measure-text`; pages fill `page_content`. No sidebar variant (spec 14 lists none) |
| `layouts/error.html` | `400.html`, `403.html`, `403_csrf.html`, `404.html`, `500.html` | Extends `content.html`; pages fill `title`, `heading`, `message`; adds `noindex` |

The shell, in order: the skip link (`components/skip_link.html`, the first focusable element,
shown on focus, target `<main id="main" tabindex="-1">`), the sticky header
(`components/header.html`: site name, main navigation as a disclosure, help link, language
switcher), `<main>`, then the footer (`components/footer.html`: the help link again, the
footer menu of CMS.1, a `legal_links` slot for the CMS.5 legal pages, the copyright line). THM.4
adds the component library.

- **Error pages.** Django renders 403, 404 and the CSRF failure page with the request context,
  so they keep the full header and footer. `handler400` and `handler500` point to
  `apps/themes/views.py`, which renders `400.html` and `500.html` with a plain context
  (`minimal_chrome`: no CMS menus, no language switcher, no CSRF token) and the theme the
  request already resolved or the default one (`services.resolved_theme`): no database query,
  because a 500 is often a database failure and a 400 often a bad `Host` that the site lookup
  would raise on again. If even that fails, a plain text response.
- **Form fields.** `components/field.html` renders a bound field: a visible label tied to the
  control, a "(required)" marker next to the `required` attribute, the help text and the inline
  error list. Django sets `aria-describedby` (help text and errors) and `aria-invalid`.
- **Buttons and alerts** are base-layer styles: `button`/`.button`, `.button--secondary`,
  `.button--ghost`; `.alert` with `.alert--info|success|warning|error` (a level bar in
  `--color-info|success|warning|danger`, the text in `--color-text`; say the level in words).

- **Disclosure navigation.** Without JavaScript the links are shown and the menu button stays
  `hidden`. The `disclosure` Alpine component (`js/base.js`) shows the button; below 48rem the
  CSS collapses the list until the button is pressed. Enter or Space toggles, Escape closes and
  returns focus to the button, `aria-expanded` follows.
- **Help link** (WCAG 3.2.6): `components/help_link.html`, in the header and the footer of every
  page, pointing to `MPMV_HELP_URL` (env; empty renders no link until a contact page exists).
  The `theme` context processor exposes it as `help_url`.
- **Language switcher**: `components/language_switcher.html`, one POST form per language to
  `switch_language`; works without JavaScript.
- **Component library** (THM.4, spec 14 §14.9): one file per component in
  `templates/components/`, its context documented in the comment at its top, included with
  `only`. `apps/themes/styleguide.py` `COMPONENTS` maps every §14.9 component to its styleguide
  section, template and a marker of its markup; a unit test compares the names with the spec and
  an integration test checks each marker is rendered in its section. Controls that need
  JavaScript (dialog openers, carousel buttons, quantity buttons, toast buttons) are rendered
  `hidden` and revealed by `js/base.js`, so without JavaScript no button does nothing; dialogs
  are native `<dialog>` elements opened with `showModal()` and closed by a
  `<form method="dialog">`. The image credit is CMS.8's `core/components/image_credit.html`.
- **Accessibility rules in `theme.css`**: `:focus-visible` outline of `--focus-width` (3 px) in
  `--color-focus`, never removed; `scroll-padding-top: var(--header-height)` for the sticky
  header, which below 48rem (where it wraps taller than that) is not sticky; header and footer
  links at least `--target-min` (24 px), buttons and fields `--target-primary` (44 px) high;
  transitions only inside `prefers-reduced-motion: no-preference`, and none under `reduce`.

### JavaScript under the strict CSP

The layout loads, in this order and all deferred: the theme module
(`themes/base/js/base.js`, `type="module"`), vendored htmx, vendored Alpine CSP build. The module
registers its components on `alpine:init`, so they exist before Alpine starts. htmx reads
`<meta name="htmx-config">`: `allowEval`, `allowScriptTags` and `includeIndicatorStyles` off,
`selfRequestsOnly` on.

Alpine directives name only a registered component, a property or a method (`x-data="disclosure"`,
`@click="toggle"`, `:aria-expanded="expanded"`); the CSP build evaluates nothing else, and the
CSP lint rejects any other expression. Values a template binds are methods of the component.

### CSP lint

`apps/themes/csp_lint.py` (`lint_template`, `public_templates`) lints every theme template and
every themable app template: no inline `<script>`, no `<style>`, no `style=`, no `on…=`
handlers, no `javascript:` URLs, no `hx-on` or `js:` values, Alpine expressions as above. A unit
test runs it over the repository.

### Vendored JavaScript

htmx and the Alpine CSP build live in `apps/themes/static/vendor/` (static path `vendor/`), byte
for byte as downloaded. `VENDORED.toml` lists each file: exact `version`, https `source` URL
pinning that version, `sha256`, SPDX `license` (allow list in `apps/themes/vendored.py`). The
unit test `test_vendored.py` fails on a hash mismatch, a missing or unlisted file, a range
version or a disallowed licence. The pre-commit whitespace fixers and git's line-ending
conversion skip that directory, so the hashes stay valid. To update a library, download the
new version from its pinned URL, replace the file and update its entry.

## Quality gates (THM.5)

Every registered theme must pass these gates (spec 14 §14.11). A failing theme fails `make check`
and CI.

| Gate | Where | Fails on |
|---|---|---|
| `manifest`, `tokens` | `python -m apps.themes.gates` (`make theme-gates`, part of `make check` and the CI static job) | An invalid `theme.toml`, a missing token, a contrast pair below its minimum (a 3:1 text pair) |
| `csp` | same | A CSP lint finding in the theme's own templates (an inline style) |
| `size` | same | `theme.css` over 45 KB or the page's JavaScript (vendored htmx and Alpine plus the chain's modules) over 40 KB, compressed |
| `fonts` | same | A font file that is not woff2, more than 4 font files, more than 2 families |
| `licences` | same | A font, icon or image under `static/themes/<slug>/` without an entry in the theme's `LICENSES.toml`, an entry for a missing file, a licence its kind does not allow (`font`: `OFL-1.1` or `Apache-2.0`; `icons`: `MIT` or `ISC`; `image`: `CC0-1.0`), or an entry without name, kind, https source or author |
| axe-core | `tests/e2e/test_theme_quality_gates.py` | Any violation with the tags `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa` on `/_styleguide/` rendered with each theme |
| visual | same, through `tests/e2e/visual.py` | A styleguide screenshot at 360, 768 or 1280 px that differs from its baseline |

Sizes are measured with gzip level 9. The spec budgets are Brotli, which compresses CSS and JS
a little better, so a theme within the gzip budget is within the Brotli one too. The format of
`LICENSES.toml` is documented in `themes/base/LICENSES.toml`. `apps/themes/tests/unit/test_gates.py`
runs the gates over the shipped themes and over fixture themes that each break one gate.

### axe-core

The e2e suite injects a pinned copy of axe-core from `tests/e2e/vendor/` (its own
`VENDORED.toml`, hash checked by `tests/unit/test_e2e_vendored.py`, never served by the site and
never loaded from a CDN). axe-core is MPL-2.0, which is allowed for test tooling only. Use
`tests.e2e.axe.run_axe(page)` in a new e2e test to check another page.

### Visual baselines

Baselines live in `tests/e2e/baselines/<theme>/<page>-<width>.png`. Fonts render differently on
each platform, so baselines come only from the CI runner (`ubuntu-24.04` with Playwright's
Chromium), and the comparison runs only there. `MPMV_VISUAL` sets the mode:

| Value | Effect |
|---|---|
| unset | `compare` in CI, `off` elsewhere (the tests are skipped with a message) |
| `compare` | Compare with the baselines; meaningful only on the same platform as the CI runner |
| `update` | Write the screenshots as the new baselines (`make e2e-baselines`) |
| `off` | Skip |

A page without a baseline is skipped in CI, and its screenshot is uploaded as the `e2e-visual`
artifact (`build/e2e/baselines/`). On a mismatch the screenshot goes to the same place (the
candidate new baseline) and the diff image (changed pixels in magenta) to `build/e2e/diffs/`, in
the same artifact. The tolerance is 0.1% of the pixels, with a per-channel threshold for
anti-aliasing.

To add or update baselines after a reviewed, intentional change:

1. Take the PNGs from the `e2e-visual` artifact of the failing CI run (`build/e2e/baselines/`),
   or run the **Visual baselines** workflow on your branch
   (`gh workflow run visual-baselines.yml --ref <branch>`): it runs `make e2e-baselines`'s
   command on the CI runner and uploads every baseline as the `visual-baselines` artifact.
2. Commit them under `tests/e2e/baselines/<theme>/` and review them in the pull request like code.

The first baselines for `base` and `ride` are committed by THM.10.

## The `ride` theme (THM.6)

`themes/ride/` is the first vertical theme (`parent = "base"`). It was written clean-room from the
public spec and a brief: no file derives from a reference template (spec 14 §14.8).

- **Tokens** (`tokens.toml`): `color.accent` is the accent as text, link and UI colour
  (`#C40417`), `color.accent-fill` the red fill for buttons and large surfaces (`#E3051A`, white
  text 4.88:1), `color.surface-inverse` the dark header and footer. Extra tokens: `color.heading`,
  `color.border-strong`, `color.overlay`, `scrim.opacity`, `step.display`, `space.section`,
  `radius.pill`, `radius.round`, `shadow.button-hover`. Every text pair is tested in
  `apps/themes/tests/unit/test_ride_theme.py`. CSS uses `var(--…)` only, so the operator palette
  of spec 14 §14.12 overrides the defaults with custom properties.
- **Fonts**: Barlow 400 and 700, Barlow Condensed 600 and 700, woff2 subset to Latin and Latin
  Extended-A (the gate allows 4 files), `font-display: swap`, one preload in `layouts/base.html`.
  Upstream commit and licence are in `LICENSES.toml`.
- **Icons**: `components/icon.html` draws from `static/themes/ride/icons/sprite.svg`, a
  same-origin sprite of the 13 Lucide icons the components use (ISC).
- **Assets in the build**: `python -m apps.themes.build` copies every non-CSS, non-JS file under
  `static/themes/<slug>/` to `build/themes/<slug>/`, so `theme.css` reaches fonts and images with
  a relative `url()` and `{% static "themes/ride/icons/sprite.svg" %}` resolves.
- **Templates**: `components/header.html` (dark bar, disclosure sub-menus for links with
  `children`, search and cart icons when `search_url` and `cart_url` are given, burger below
  64rem), `components/footer.html` (three columns), `components/hero.html` (scrim over the
  image), `components/product_tile.html` (title link plus a small button, not one giant link),
  `components/icon.html`, `cms/blocks/hero.html`, `cms/blocks/cta.html` and `cms/home_page.html`
  (the first hero block carries the page's one `<h1>`).
- **Not yet**: catalog, cart, checkout, account and content pages are THM.7 to THM.9. Pages that
  override the `head` block without `{{ block.super }}` lose the font preload only.

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
sources; `shipped_tokens()` is a token set that meets the contract; `shipped_templates()` gives
the shipped `base` templates, which a test `base` needs to render real pages); changing a `MPMV_THEME*`
setting clears the registry cache. `SiteFactory` and `ThemeSettingsFactory` are in `apps.themes.tests.factories`.
