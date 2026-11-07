# 14 — Frontend themes

The public frontend (storefront, customer area, vendor area, CMS pages) is rendered by **themes**.
A theme changes how the site looks, never what it does. MPMV is vertical-agnostic: the theme system
and the `base` theme suit any vertical, and a vertical theme (the first one targets a motorcycle
vertical) only adds a visual identity on top. This document replaces the single CMS.7 row of
[10](10-content-seo-ai.md) with the THM tasks below.

## 14.1 How it works today

- Legacy: a WordPress `storefront` parent theme plus a custom child theme and Elementor
  (see [10](10-content-seo-ai.md) §10.1). Styles, page builder markup and plugin CSS are mixed; there
  is no token system and no accessibility baseline.
- MPMV after CMS.1: `apps/cms/templates/cms/base.html` with a minimal `cms/site.css`, and a bare
  `core/base.html` for the account pages. No theme concept, no components, no design tokens.
- The owner has a commercial HTML template (Bootstrap 4, jQuery, 2019) for a motorcycle and bike
  rider vertical. It is kept in `private/` and is **only a visual reference** for the first vertical
  theme (§14.8). It is not reused as code: Bootstrap 4 and jQuery are end of life, the template
  loads three carousel libraries and third-party fonts and maps, uses inline scripts and styles that
  the CSP blocks, removes every focus outline, and fails several WCAG contrast and semantics checks.

## 14.2 What a theme is

A theme is a directory under `themes/<slug>/` (outside `apps/`, because it holds no Python
logic) with a fixed layout:

| Path | Content |
|---|---|
| `theme.toml` | Manifest: `slug`, display `name`, `version`, `parent` (another theme slug, or none for `base`), `description`, supported languages direction (`ltr` only) |
| `tokens.toml` | Design tokens (§14.4); a child theme lists only the tokens it changes |
| `templates/` | Template overrides, same relative paths as the templates they replace (`layouts/`, `components/`, or any app template such as `catalog/product_detail.html`) |
| `static/themes/<slug>/css/` | CSS sources in cascade layers (§14.5) |
| `static/themes/<slug>/js/` | Small ES modules registering Alpine components; no third-party code |
| `static/themes/<slug>/fonts/`, `icons/`, `img/` | Self-hosted woff2 fonts, an SVG icon sprite, placeholder imagery |
| `LICENSES.toml` | One entry per font, icon set and image: source URL, author, SPDX licence id, files covered |

Two layers of themes exist from go-live:

- **`base`**: the root of every theme. Vertical-neutral, accessible, system font stack, neutral
  palette. It provides every layout, every component and every page template that the apps need. A
  site with no other theme is complete and compliant with `base` alone.
- **Vertical themes** (first one: `ride`, a working name pending owner confirmation): `parent =
  "base"`. They change tokens, add fonts and icons, and override the few templates where the
  layout differs (home hero, header, product card). Every page they do not override comes from
  `base`.

Theming is presentation only. Text comes from gettext and the CMS, never from theme files, so a
theme adds no translation work (I18N.5 lints theme templates too). Structured data (JSON-LD),
canonical, hreflang and robots come from the `seo` app and are identical across themes.

## 14.3 Template resolution and theme selection

### Resolution

The `themes` app (`apps/themes/`) owns the registry, the template loader, the token engine and
the selection rules. It imports only `core` and Wagtail; no domain app imports it, they only use
its templates and context.

```python
# apps/themes/engine.py (pure functions, no ORM, no I/O)
def theme_chain(slug: str, registry: Mapping[str, ThemeManifest]) -> list[str]
    # ["ride", "base"]; unknown parent, cycles or a chain not ending in "base" raise ThemeError
def template_candidates(name: str, chain: list[str]) -> list[str]
    # themes/ride/templates/<name>, themes/base/templates/<name>; app directories come after
def resolve_tokens(chain: list[str], tokens_by_theme: Mapping[str, Tokens]) -> Tokens
    # child values override parent values; references like "{color.red.700}" are resolved
def contrast_ratio(fg: str, bg: str) -> float            # WCAG 2.x relative luminance formula
def check_tokens(tokens: Tokens, contract: TokenContract) -> list[TokenViolation]
    # missing required tokens, unresolved references, contrast pairs below their minimum
def render_tokens_css(tokens: Tokens) -> str             # ":root{--color-text:...}" in @layer tokens

# apps/themes/services.py
def active_theme(request) -> ThemeManifest
    # Wagtail Site setting -> settings.MPMV_THEME_DEFAULT -> "base"
```

- A template name is looked up in the active theme, then in each parent up to `base`, then in the
  app template directories. A child theme therefore overrides any template by placing a file at the
  same relative path.
- `ThemeLoader` is the first template loader. Its cache key is `(theme slug, template name)`;
  Django's `cached.Loader` is not wrapped around it, because that cache is keyed by name only and
  would serve one theme's template to another site.
- Pages extend `layouts/base.html` (public pages) or `layouts/account.html` (customer and vendor
  areas) and include components from `components/`. The existing CMS.1 templates move under this
  structure in THM.3.
- The Wagtail admin and the Django admin are never themed.

### Selection

| Level | Mechanism | Use |
|---|---|---|
| Wagtail Site | `ThemeSettings` (a Wagtail site setting) with `theme`, a choice among registered themes; changes are audited (`AuditLog`) | Normal choice: one theme per site, so two Wagtail sites can show two themes |
| Environment | `MPMV_THEME_DEFAULT` (env, default `base`) | Fallback when a site has no setting; lets STAGE try a theme without touching LIVE data |
| Request | none at go-live | No query-string or cookie switch: it would split the anonymous page cache (SEO.6) |

A system check fails when the default theme or a stored site setting names an unknown theme, or
when a manifest is invalid. Vendor-level theming is **out of scope**: a vendor storefront shows
the vendor's logo and banner image as content (VEN.10), never vendor colours or fonts, because each
colour pair would need its own contrast validation.

## 14.4 Design tokens

Tokens are the only place where a theme sets colours, fonts, sizes, spacing, radii, shadows and
motion. They are written in `tokens.toml`, validated by `check_tokens`, and rendered as CSS custom
properties in `@layer tokens`. Component CSS uses only `var(--…)`; a CSS lint rule rejects raw
colour values outside token files.

| Tier | Examples | Who sets it |
|---|---|---|
| Primitive | `color.red.700`, `color.slate.900`, `font.family.display`, `space.4`, `radius.2` | Theme |
| Semantic | `--color-text`, `--color-text-muted`, `--color-surface`, `--color-surface-inverse`, `--color-accent`, `--color-on-accent`, `--color-link`, `--color-focus`, `--color-danger`, `--font-body`, `--font-display`, `--step-0` … `--step-5` (fluid type with `clamp()`), `--space-xs` … `--space-3xl`, `--radius-control`, `--radius-card`, `--shadow-raised`, `--duration-fast`, `--header-height` | Theme (required set = token contract) |
| Component | `--button-bg`, `--card-padding`, `--price-sale-color` | `base` derives them from semantic tokens; a theme may override |

The **token contract** (`apps/themes/contract.py`) lists the required semantic tokens and the
contrast pairs that every theme must satisfy:

| Pair | Minimum |
|---|---|
| `text` / `surface`, `text-muted` / `surface`, `link` / `surface`, `on-accent` / `accent`, `text-inverse` / `surface-inverse`, `danger` / `surface` | 4.5:1 |
| `focus` / `surface`, `focus` / `surface-inverse`, `border-control` / `surface`, `accent` / `surface` (as a UI component) | 3:1 |

Dark mode is not required at go-live. The semantic tier allows it later as a second token set
under `prefers-color-scheme: dark`, validated with the same pairs.

## 14.5 Toolchain and CSP

Consistent with [01](01-stack-and-architecture.md) §1.2 (Django templates + HTMX + minimal
Alpine.js, CSS with a lightweight build). Recorded in an ADR in THM.1.

- **No Node toolchain, no npm.** CSS is written as plain modern CSS: custom properties, cascade
  layers (`@layer reset, tokens, base, layout, components, utilities, overrides`), native nesting,
  logical properties, container queries, `clamp()`. Target: browsers in Baseline "widely
  available".
- `tools/build_theme.py` (`make assets`): resolves tokens for each theme chain, writes
  `tokens.css`, concatenates the CSS sources of the chain in layer order, minifies, and writes one
  `theme.css` per theme. It runs in the Containerfile build stage before `collectstatic`; the
  output is git-ignored. `CompressedManifestStaticFilesStorage` hashes and compresses it.
- JavaScript: htmx 2 and the **CSP build** of Alpine.js, self-hosted under `static/vendor/`,
  listed in `static/vendor/VENDORED.toml` (version, source URL, SHA-256, licence); a unit test
  checks the hashes. Theme behaviour (disclosure menu, dialog, carousel buttons, quantity stepper,
  live-region toasts) lives in `Alpine.data(...)` components in theme ES modules, loaded with
  `type="module"`.
- Icons: one SVG sprite per theme, referenced with `<svg><use href="…#icon">` (same origin).
  Decorative icons get `aria-hidden="true"`; icon-only buttons get a visible-on-focus or
  `aria-label` text.

CSP rules for every theme (SEC.1 policy, no `unsafe-inline`, no `unsafe-eval`):

- No inline `<script>` or `<style>` blocks, no `style="…"` attributes, no `on…=` handler
  attributes, no `javascript:` URLs. Values that vary per item use classes, `<progress>`,
  `<meter>`, or SVG presentation attributes (for example a colour swatch's `fill`).
- htmx is configured through a `<meta name="htmx-config">` tag: `allowEval: false`,
  `allowScriptTags: false`, `includeIndicatorStyles: false` (the indicator CSS ships in `base`),
  `selfRequestsOnly: true`. No `hx-on` attributes and no `js:` values. The CSRF token is sent with
  `hx-headers` read from the page, because the CSRF cookie is HttpOnly (SEC.1).
- Alpine expressions are limited to what the CSP build allows: property and method names of
  registered components, no inline expressions.
- Fonts, icons and theme images are served from `'self'`. Product and CMS images come from the
  media origin added once to `img-src` (ARC.15, SEO.6). No Google Fonts, no icon CDN, no map or
  video embed without consent (SEO.5). Third-party UI that needs its own origin (Stripe Payment
  Element, Turnstile, the consent tool if D-10 picks an external one) is added by its own task, and
  the theme only styles it: the Payment Element through Stripe's Appearance API with values read
  from the active theme's tokens.
- A unit test lints every template of every theme for the patterns above. An e2e check fails on
  any `securitypolicyviolation` event on the key pages.

## 14.6 Accessibility: WCAG 2.2 AA

Target: **WCAG 2.2 level AA**, as required by **EN 301 549 V4.1.1** (see
[11](11-compliance.md) CMP.7). WCAG 2.2 AA includes every 2.1 AA criterion except 4.1.1
Parsing, so a theme that passes 2.2 AA also meets the 2.1 AA reference of EN 301 549 V3.2.1 while
V4.1.1 awaits citation.

Rules built into `base` and enforced for every theme:

| Area | Rule | WCAG |
|---|---|---|
| Structure | `<header>`, `<nav>` with labels, one `<main>`, `<footer>`; one `<h1>` per page, no skipped levels; skip link to `<main>` as first focusable element | 1.3.1, 2.4.1, 2.4.6 |
| Language | `lang` on `<html>`; EN fallback blocks inside another language carry `lang="en"` (spec 09 fallback) | 3.1.1, 3.1.2 |
| Focus | `:focus-visible` outline of at least 2 px in `--color-focus` (≥ 3:1), never removed | 2.4.7 |
| Focus not obscured | `scroll-padding-top: var(--header-height)` for the sticky header; the consent banner never covers the focused element (it is either the first thing in the focus order and a non-modal region that reserves its own space at the bottom, or a modal dialog that traps focus) | 2.4.11 |
| Target size | Every pointer target at least 24×24 CSS px, or spaced so a 24 px circle fits; primary controls 44 px high | 2.5.8 |
| Dragging | Carousels, galleries and the price filter work with buttons and inputs; no drag-only interaction | 2.5.7 |
| Consistent help | The help/contact link sits in the same place in the header and footer on every page, including checkout | 3.2.6 |
| Forms | Visible labels (never placeholder-only), `autocomplete` tokens on personal data, inline errors tied with `aria-describedby`, an error summary with links on submit | 1.3.5, 3.3.1, 3.3.2, 3.3.3 |
| Redundant entry | Checkout offers "billing same as shipping" and pre-fills from the account and the previous step | 3.3.7 |
| Authentication | No cognitive test; paste allowed; password managers and passkeys work (§14.7) | 3.3.8 |
| Colour and contrast | Token contract pairs (§14.4); stock, sale and error states also have text or an icon with text | 1.4.1, 1.4.3, 1.4.11 |
| Reflow and spacing | Usable at 320 CSS px and at 200% zoom; no fixed heights on text containers | 1.4.4, 1.4.10, 1.4.12 |
| Motion | No autoplay; animation only inside `prefers-reduced-motion: no-preference` | 2.2.2, 2.3.3 (advisory) |
| Status messages | HTMX cart and filter updates announced through one polite live region | 4.1.3 |
| Images | Alt text from the translatable alt fields (spec 09); decorative images `alt=""` | 1.1.1 |

RTL languages are not planned; using logical properties keeps the door open at no cost, but RTL is
not tested.

### WCAG 2.2 follow-up for tasks already done (3.3.8 Accessible Authentication)

CORE.4 (MFA: TOTP, passkey, recovery codes) and CORE.5 (login lockout, Turnstile) were built
before the 2.2 target was set, and VEN.14 (email OTP for contract signature) is not built yet.
Their templates must:

- allow paste in every password and code field and never block it with script;
- carry `autocomplete="current-password"` (login), `"new-password"` (registration and reset),
  `"one-time-code"` (TOTP, recovery and email OTP fields) and `"username webauthn"` where passkey
  autofill is offered; passkeys count as a compliant authentication method;
- use Turnstile in a mode that does not ask the user to solve a puzzle (managed or invisible
  challenge), with an accessible fallback when it cannot decide.

This is covered by the new row CMP.9 for CORE.4 and CORE.5, and by an addition to VEN.14's
done-when. No code changes are part of this spec update.

## 14.7 Performance budgets

Measured on the built theme and on the key pages (home, listing, product, cart, checkout) with
the anonymous page cache on.

| Budget | Limit | Checked by |
|---|---|---|
| CSS per theme (`theme.css`, Brotli) | ≤ 45 KB | unit test on the build output (THM.5) |
| JavaScript total, including htmx and Alpine (Brotli) | ≤ 40 KB | unit test (THM.5) |
| Web fonts | ≤ 2 families, ≤ 4 woff2 files, Latin + Latin Extended-A subsets, `font-display: swap`, one preload | `LICENSES.toml` and build check (THM.5) |
| LCP (mobile, 4G profile) | < 2.5 s | SEO.6 on STAGE |
| CLS | < 0.1 (images and embeds always have width and height or `aspect-ratio`) | SEO.6 |
| INP | < 200 ms | SEO.6 |
| Third-party requests before consent | 0 | SEO.5 e2e |

No preloader, no hero video by default, the LCP image is an `<img>` with `fetchpriority="high"`
and responsive renditions (`srcset`, AVIF/WebP) from the media pipeline.

## 14.8 Clean-room and licence rule

- The owner's reference template (kept in `private/`) is a **visual reference only**: layout,
  hierarchy, spacing, palette, typography and component shapes. Its HTML, CSS, SCSS, JavaScript,
  images, icons and fonts are never copied, adapted or committed, and its product, author and
  marketplace are never named in versioned files. Class names, markup and CSS are written from
  scratch for MPMV.
- Fonts: only SIL OFL 1.1 or Apache 2.0 fonts downloaded from their upstream project, self-hosted.
  Paid or unknown-licence fonts from the reference template are not reused.
- Icons: an MIT or ISC licensed SVG icon set (subset), never icon fonts.
- Images: placeholders made for MPMV (licence CC0, own work) in the theme; real photos come from
  the owner and vendors through the CMS and catalog, with a written licence. No stock photos in
  the repository.
- Every asset is listed in the theme's `LICENSES.toml`; a test fails on an asset without an entry
  or on a licence outside the allow list (`OFL-1.1`, `Apache-2.0`, `MIT`, `ISC`, `CC0-1.0`).

## 14.9 Pages and components

Pages are built by the tasks that own them (CAT.7, CAT.8, VEN.10, ORD.5, ORD.7, CORE.4, CORE.5,
CMS.1 to CMS.5). Rule: a page task that starts after THM.4 uses `base` layouts and components; a
page built earlier is moved onto them in THM.7 to THM.9. Vertical theme tasks only restyle and
override; they do not add features.

| MPMV page | `base` provides | First vertical theme (THM.6 to THM.9) |
|---|---|---|
| Home (`HomePage` + CMS.2 blocks) | Block templates: hero, product grid, featured terms and vendors, CTA, blog teaser | Full-width hero with large condensed headings, section titles, brand/vendor strip |
| Listings: category, term, brand, vendor storefront, search | Filter panel (form, no drag), product grid, sort, pagination | Product card variant |
| Product page | Gallery (`<dialog>` zoom), variant selector, price with Omnibus strikethrough, GPSR block, related products | Visual restyle |
| Cart, checkout, order confirmation | Per-vendor packages, step indicator, error summary, Payment Element container | Visual restyle, Payment Element appearance |
| Sign in, sign up, password reset, MFA | Auth layout | Visual restyle |
| Customer area, vendor area | `layouts/account.html`, tables, timelines | Visual restyle |
| Blog index and post, standard, legal, contact, term and vendor landing, vendor recruitment | Content layouts, rich text styles, FAQ (`<details>`) | Visual restyle |
| 404, 500 | Error layout | Visual restyle |
| Consent banner (SEO.5) | Markup or CSS hooks for the chosen tool | Visual restyle |

Pages of the reference template with no MPMV counterpart (events, routes or trails, tour packages
with prices, countdown pages) are not planned; any of them would need its own feature task.

Component library in `base` (`templates/components/`, one file per component, documented context,
included with `only`): skip link, header and disclosure navigation, language switcher, search
form, mini-cart status, breadcrumb, buttons (primary, secondary, ghost, link; sizes), form fields
(text, select, checkbox, radio, textarea, error summary), alert, badge, price, rating (text plus
icons), product card, product grid, pagination, accordion, tabs (only where content is short),
dialog, scroll-snap carousel with buttons, gallery, quantity stepper, cart line, order summary,
step indicator, empty state, live-region toast, responsive table, hero, CTA band, logo strip, FAQ,
footer.

A staff-only **styleguide page** (`/_styleguide/`, not indexed, not cached) renders every component
in every state with the active theme. Accessibility and visual tests run against it.

## 14.10 Visual regression

The e2e suite already saves screenshots ([ADR-0006](../adr/0006-testing-strategy.md)). THM.5 adds
comparison against baselines:

- Baselines: `tests/e2e/baselines/<theme>/<page>-<width>.png` for the styleguide and the key pages
  at 360, 768 and 1280 px, rendered from fixed seed data with placeholder images.
- Deterministic rendering: self-hosted fonts loaded before capture, `reduced_motion="reduce"`,
  caret hidden, dynamic regions (dates, order numbers) masked.
- Comparison: pixel difference with a small tolerance, a diff image saved next to the screenshot
  on failure. Baselines are produced only in the CI image (Linux Chromium), because font rendering
  differs on macOS; locally the comparison is skipped unless run in the same container.
- Updating baselines is an explicit command (`make e2e-baselines`) and the new PNGs are reviewed
  in the pull request like code.

## 14.11 Adding a theme later

Contract (checked automatically for every registered theme, parametrised tests):

1. `theme.toml` is valid; the chain ends in `base`; the slug is unique.
2. `tokens.toml` resolves, defines every required semantic token, and passes every contrast pair.
3. Templates and CSS pass the CSP lint; CSS uses no raw colour values outside tokens.
4. axe-core with the `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa` and `wcag22aa` tags reports zero violations on
   the styleguide and the key pages.
5. Size budgets (§14.7) hold.
6. `LICENSES.toml` covers every asset with an allowed licence.
7. Visual baselines exist for the styleguide and the key pages at three widths.

Checklist for the person adding a theme:

- [ ] Copy no code or asset from a commercial template; note the visual reference in the PR.
- [ ] Start from `tokens.toml`; override templates only where the layout really differs.
- [ ] Self-host fonts with an allowed licence; add icons to the sprite.
- [ ] Run `make assets`, `make check`, `make test`, `make test-e2e`; generate baselines in CI.
- [ ] Manual keyboard and screen-reader pass on checkout (CMP.7 procedure).
- [ ] Owner approval of the visual result on STAGE before selecting it for a LIVE site.

## 14.12 Tasks

THM.1 to THM.5 build the system and `base`; THM.6 to THM.10 build the first vertical theme. The
vertical theme rows are P1 on the assumption that it is the theme at go-live; if the owner chooses
to launch with `base`, they move to P2.

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| THM.1 | Theme framework: `apps/themes` registry and manifest, theme-aware template loader with parent fallback and `(theme, name)` cache, `ThemeSettings` per Wagtail Site with audit, `MPMV_THEME_DEFAULT`, system checks, import-linter rule, ADR for the frontend toolchain | P1 | 8 | CMS.1 | Two sites render different themes in an integration test; a template missing in the child comes from `base`; an unknown theme fails the system check |
| THM.2 | Design-token pipeline: `tokens.toml` resolution and inheritance, token contract with contrast pairs, `tokens.css` rendering, `tools/build_theme.py` and `make assets`, Containerfile build step | P1 | 6 | THM.1 | Unit tests on `resolve_tokens`, `contrast_ratio` and `check_tokens` (a pair below 4.5:1 fails); the image build produces hashed `theme.css` for every theme |
| THM.3 | `base` theme: layouts, skip link, header with disclosure navigation, language switcher, consistent help link, footer, typography, form controls, buttons, alerts, focus and target-size rules, reduced motion; vendored htmx and Alpine CSP build with `VENDORED.toml`; CMS.1 and account templates moved onto the layouts | P1 | 10 | THM.2 | Home, standard page and login render with `base`; CSP lint and the hash check of vendored files pass; keyboard-only e2e reaches every control with a visible focus |
| THM.4 | `base` component library (§14.9) and staff-only styleguide page | P1 | 12 | THM.3 | Every component renders in every state on `/_styleguide/` with no CSP violation; e2e screenshots saved |
| THM.5 | Theme quality gates, parametrised over registered themes: CSP template lint, axe-core (`wcag22aa`) on the styleguide, visual comparison harness with CI-only baselines, size budgets, `LICENSES.toml` check | P1 | 6 | THM.4, ARC.11 | A theme with a 3:1 text pair, an inline style, an unlicensed font or a 50 KB CSS file fails CI; `base` passes |
| THM.6 | First vertical theme `ride` (name pending owner confirmation): tokens from the reference template's visual language adjusted for contrast, self-hosted OFL fonts, SVG icon subset, header, hero and home block overrides, placeholder imagery, `LICENSES.toml` | P1 | 8 | THM.4, CMS.2 | Home page renders with `ride` and passes the THM.5 gates; no file derived from the reference template (PR checklist) |
| THM.7 | `ride` catalog pages: listings with filters, search results, product card, product page, vendor storefront | P1 | 6 | THM.6, CAT.7, CAT.8, VEN.10 | Catalog key pages pass the THM.5 gates at 360, 768 and 1280 px |
| THM.8 | `ride` cart, checkout and account pages: cart, checkout steps, Payment Element appearance from tokens, order confirmation, customer area, sign in and up, MFA, password reset | P1 | 6 | THM.6, ORD.5, ORD.7 | A keyboard-only e2e checkout completes in test mode with `ride`; the Payment Element uses the theme's colours and fonts |
| THM.9 | `ride` content and system pages: blog, standard, legal, contact, term and vendor landing, vendor recruitment, 404 and 500, consent banner styling | P1 | 4 | THM.6, CMS.3, CMS.4, CMS.5, SEO.5 | Content key pages and the consent banner pass the THM.5 gates; focus is never hidden by the banner or the sticky header |
| THM.10 | Visual regression baselines for `base` and `ride`: styleguide and key pages at three widths, CI job, update procedure in the developer docs | P1 | 4 | THM.5, THM.7, THM.8, THM.9 | CI compares every key page with its baseline; an intentional change needs a reviewed baseline update |
