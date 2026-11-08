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
- **Vertical themes** (first one: `ride`, name confirmed by the owner, Q-59): `parent =
  "base"`. They change tokens, add fonts and icons, and override the few templates where the
  layout differs (home hero, header, product card). Every page they do not override comes from
  `base`.

Both themes ship at go-live (owner decision Q-60): `ride` and `base` are registered and selectable
per Wagtail site, so THM.6 to THM.10 stay P1.

Theming is presentation only. Text comes from gettext and the CMS, never from theme files, so a
theme adds no translation work (I18N.5 lints theme templates too). Structured data (JSON-LD),
canonical, hreflang and robots come from the `seo` app and are identical across themes.

## 14.3 Template resolution and theme selection

### Resolution

The `themes` app (`apps/themes/`) owns the registry, the template loader, the token engine and
the selection rules. It imports only `core` and Wagtail; no domain app imports it, they only use
its templates and context. The one exception is `vendors`, which imports the pure palette
functions of `apps/themes/engine.py` for vendor branding (§14.12, THM.11).

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
when a manifest is invalid.

Vendors cannot choose a theme. Without branding, a vendor storefront shows the vendor's name and
logo as content (VEN.10) in the site theme's colours. **Vendor branding** (owner decision Q-64) is
a paid option of the vendor's plan: accent colours, logo and banner on the vendor's storefront and
on that vendor's product pages, validated against the token contract and delivered as a
per-vendor stylesheet (§14.12). It is P2 and depends on plan entitlements (VEN.19). Fonts always
stay the theme's.

## 14.4 Design tokens

Tokens are the only place where a theme sets colours, fonts, sizes, spacing, radii, shadows and
motion. They are written in `tokens.toml`, validated by `check_tokens`, and rendered as CSS custom
properties in `@layer tokens`. Component CSS uses only `var(--…)`; a CSS lint rule rejects raw
colour values outside token files.

| Tier | Examples | Who sets it |
|---|---|---|
| Primitive | `color.red.700`, `color.slate.900`, `font.family.display`, `space.4`, `radius.2` | Theme |
| Semantic | `--color-text`, `--color-text-muted`, `--color-surface`, `--color-surface-raised`, `--color-surface-inverse`, `--color-accent`, `--color-on-accent`, `--color-accent-hover`, `--color-accent-tint`, `--color-secondary`, `--color-on-secondary`, `--color-secondary-hover`, `--color-secondary-text`, `--color-link`, `--color-focus`, `--color-danger`, `--color-info`, `--color-success`, `--color-warning`, `--font-body`, `--font-display`, `--step-0` … `--step-5` (fluid type with `clamp()`), `--space-xs` … `--space-3xl`, `--radius-control`, `--radius-card`, `--shadow-raised`, `--duration-fast`, `--header-height` | Theme (required set = token contract) |
| Component | `--button-bg`, `--card-padding`, `--price-sale-color` | `base` derives them from semantic tokens; a theme may override |

The **token contract** (`apps/themes/contract.py`) lists the required semantic tokens and the
contrast pairs that every theme must satisfy:

| Pair | Minimum |
|---|---|
| `text` / `surface`, `text-muted` / `surface`, `link` / `surface`, `on-accent` / `accent`, `on-accent` / `accent-hover`, `accent` / `accent-tint`, `on-secondary` / `secondary`, `on-secondary` / `secondary-hover`, `secondary-text` / `surface`, `text-inverse` / `surface-inverse`, `danger` / `surface`, `text` / `surface-raised`, `text-muted` / `surface-raised`, `link` / `surface-raised`, `link` / `accent-tint` | 4.5:1 |
| `focus` / `surface`, `focus` / `surface-inverse`, `border-control` / `surface`, `accent` / `surface` (as a UI component), `info` / `surface-raised`, `success` / `surface-raised`, `warning` / `surface-raised`, `danger` / `surface-raised` (the alert level bar) | 3:1 |

Every background role has its own foreground token (`on-accent`, `on-secondary`), so a light
secondary such as an orange can carry dark text; the vendor palettes of §14.12 use the same roles.

`ride` defaults to the **operator palette** (§14.12), the colours of the platform's own LIVE site
adjusted for AA: accent `#046BD2` with white text, accent hover `#035AB1`, accent tint `#EEF6FF`,
secondary `#FBA926` (background only) with `#1A1A1A` text, secondary hover `#E89A1E`, secondary
text `#BE5900`, and `#0580FA` as the dark-mode accent. Layout, type scale and component shapes
still follow the reference template's visual language. `base` keeps a neutral palette.

Dark mode is not required at go-live. The semantic tier allows it later as a second token set
under `prefers-color-scheme: dark`, validated with the same pairs.

## 14.5 Toolchain and CSP

Consistent with [01](01-stack-and-architecture.md) §1.2 (Django templates + HTMX + minimal
Alpine.js, CSS with a lightweight build). Recorded in an ADR in THM.1.

- **No Node toolchain, no npm.** CSS is written as plain modern CSS: custom properties, cascade
  layers (`@layer reset, tokens, base, layout, components, utilities, overrides`), native nesting,
  logical properties, container queries, `clamp()`. Target: browsers in Baseline "widely
  available".
- `python -m apps.themes.build` (`make assets`; a module of the app rather than a `tools/` script,
  because the image build context leaves `tools/` out): resolves tokens for each theme chain,
  checks them against the token contract, writes `tokens.css`, concatenates the CSS sources of
  the chain in layer order, minifies, and writes one `theme.css` per theme under
  `build/themes/`. It runs in the Containerfile build stage before `collectstatic`; the output is
  git-ignored. `CompressedManifestStaticFilesStorage` hashes and compresses it.
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
  video embed without consent (SEO.5). Event maps use vendored Leaflet and same-origin tiles from
  the tile proxy, so they need no extra origin ([15](15-events.md)); a Google layer, if added after
  go-live (EVT.17, P2), adds its sources only on consented responses. Third-party UI that needs its own origin (Stripe Payment
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
  Paid or unknown-licence fonts from the reference template are not reused. `ride` uses Barlow
  (body) and Barlow Condensed (display), OFL 1.1, downloaded from the upstream project (owner
  decision Q-62).
- Icons: an MIT or ISC licensed SVG icon set (subset), never icon fonts.
- Images: placeholders made for MPMV (licence CC0, own work) in the theme. Real photos are
  uploaded through the CMS and catalog, never committed, and are either self-produced (by the
  operator or the vendors) or taken from free-licence sources that allow commercial use (CC0 or a
  licence on the staff allow list, such as CC BY 4.0); non-commercial and no-derivatives licences
  are refused (owner decision Q-63). Every uploaded image carries its source, licence and
  author or credit, and the credit is shown where the licence requires attribution
  ([10](10-content-seo-ai.md) §10.2, CMS.8). No stock photos in the repository.
- Every asset is listed in the theme's `LICENSES.toml`; a test fails on an asset without an entry
  or on a licence outside the allow list (`OFL-1.1`, `Apache-2.0`, `MIT`, `ISC`, `CC0-1.0`).

## 14.9 Pages and components

Pages are built by the tasks that own them (CAT.7, CAT.8, VEN.10, ORD.5, ORD.7, CORE.4, CORE.5,
CMS.1 to CMS.5). Rule: a page task that starts after THM.4 uses `base` layouts and components; a
page built earlier is moved onto them in THM.7 to THM.9. Vertical theme tasks only restyle and
override; they do not add features.

| MPMV page | `base` provides | First vertical theme (THM.6 to THM.9) |
|---|---|---|
| Home (`HomePage` + CMS.2 and CMS.10 blocks) | Block templates: hero, product grid, featured terms and vendors, CTA, blog teaser | Full-width hero with large condensed headings, section titles, brand/vendor strip |
| Listings: category, term, brand, vendor storefront, search | Filter panel (form, no drag), product grid, sort, pagination | Product card variant |
| Product page | Gallery (`<dialog>` zoom), variant selector, price with Omnibus strikethrough, GPSR block, related products | Visual restyle |
| Cart, checkout, order confirmation | Per-vendor packages, step indicator, error summary, Payment Element container | Visual restyle, Payment Element appearance |
| Sign in, sign up, password reset, MFA | Auth layout | Visual restyle |
| Customer area, vendor area | `layouts/account.html`, tables, timelines | Visual restyle |
| Blog index and post, standard, legal, contact, term and vendor landing, vendor recruitment | Content layouts, rich text styles, FAQ (`<details>`) | Visual restyle |
| 404, 500 | Error layout | Visual restyle |
| Consent banner (SEO.5) | Markup or CSS hooks for the chosen tool | Visual restyle |

Pages of the reference template with no MPMV counterpart are dropped (owner decision Q-61), except
events, which the owner later asked for as a module of their own:

- Tours, trails and packages are not sold. When needed they are product categories (CAT.2) or
  content inside CMS articles; there are no dedicated page types.
- Events have a dedicated page type, `EventPage`, with an events index, month view, RSS and ICS
  feeds and maps ([15](15-events.md)). This supersedes the earlier answer "an event is a CMS
  article"; articles can still tell the story of an event.
- The countdown is kept for announcing a product launch, as the **launch countdown** StreamField
  block usable in CMS pages and articles ([10](10-content-seo-ai.md) §10.2, CMS.9). `base` provides
  its component; vertical themes restyle it.

Component library in `base` (`templates/components/`, one file per component, documented context,
included with `only`): skip link, header and disclosure navigation, language switcher, search
form, mini-cart status, breadcrumb, buttons (primary, secondary, ghost, link; sizes), form fields
(text, select, checkbox, radio, textarea, error summary), alert, badge, price, rating (text plus
icons), product card, product grid, pagination, accordion, tabs (only where content is short),
dialog, scroll-snap carousel with buttons, gallery, quantity stepper, cart line, order summary,
step indicator, empty state, live-region toast, responsive table, hero, CTA band, logo strip, FAQ,
launch countdown (CMS.9), image credit (CMS.8), footer.

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

## 14.12 Vendor branding (P2)

Owner decision Q-64: vendor branding is a paid option of the vendor's plan. It is **P2** and
depends on plan entitlements (VEN.19, [04](04-vendors-commissions-ledger.md) §4.2): the option is
the `vendor_branding` key in `VendorPlan.features`, checked with `plan_allows(vendor,
"vendor_branding")`. The plan fee is charged and documented with a non-fiscal statement; the
marketplace issues no invoice for it, and the operator invoices outside the platform.

### Scope

- Where: the vendor storefront and that vendor's product pages only. Listings, cart, checkout,
  the customer area and every other page keep the site theme, so a shopper never sees one vendor's
  colours on a mixed page.
- What: a palette (an accent colour, optionally a secondary colour), a logo and a banner image.
  Logo and banner are CMS.8 images with their licence metadata. Fonts, layout and templates stay
  the theme's.
- Gate: without the entitlement the storefront renders with the theme only; the stored branding is
  kept, not deleted, and comes back if the vendor returns to a plan that includes it.

### Palette roles

A palette is a set of roles. Every role used as a background has an explicit foreground ("on")
colour, so an orange background can carry dark text instead of white.

| Role | Token | Used as | Foreground |
|---|---|---|---|
| Primary | `--color-accent` | Button and badge background, link text, non-text UI (borders, icons, selected states) | `on_primary` (`--color-on-accent`) |
| Primary hover | `--color-accent-hover` | Hover and pressed background | `on_primary` |
| Primary tint | `--color-accent-tint` | Tinted section background | primary as text |
| Primary, dark mode | dark token set (§14.4) | Accent on the dark surface `#121417` | — |
| Secondary (optional) | `--color-secondary` | Background only, always with text | `on_secondary` (`--color-on-secondary`) |
| Secondary hover | `--color-secondary-hover` | Hover background | `on_secondary` |
| Secondary text | `--color-secondary-text` | Secondary colour used as text or icon on the surface | — |

### Presets

The vendor starts from one of nine preset palettes or from empty fields. Presets are data
(`BrandPalette` rows seeded by a data migration in THM.11), not code; staff can add more, and
every row goes through the same validator.

The **operator** palette is the colour set of the platform's own LIVE site, adjusted for WCAG AA,
and it is also the default token set of `ride` (§14.4):

| Role | Value | Measured pair |
|---|---|---|
| Primary / on primary | `#046BD2` / `#FFFFFF` | 5.20:1 |
| Primary hover | `#035AB1` | white on it 6.77:1 |
| Secondary / on secondary | `#FBA926` / `#1A1A1A` (background only, always dark text) | 8.95:1 |
| Secondary hover | `#E89A1E` | `#1A1A1A` on it 7.52:1 |
| Secondary text | `#BE5900` | on white 4.55:1 |
| Primary tint | `#EEF6FF` | primary on it 4.77:1 |
| Primary, dark mode | `#0580FA` | on `#121417` 4.81:1 |

The legacy values `#C65D00` as text (4.23:1 on white) and `#046BD2` on the dark surface (3.55:1)
fail AA; the values above are the nearest compliant fixes. White on `#FBA926` is 1.94:1, which is
why the secondary carries its own dark foreground. The other eight presets use white on primary
and on secondary, their secondary is also their secondary text colour, and their secondary hover
is derived:

| Preset | Primary | Hover | Secondary | Tint | Primary, dark mode |
|---|---|---|---|---|---|
| Racing red | `#B3261E` | `#8C1D18` | `#1F2A37` | `#FCEBEA` | `#FF8A80` |
| Petrol blue | `#0B5C7A` | `#08465D` | `#C2410C` | `#E6F2F6` | `#7CC8E3` |
| Forest green | `#2F6B3A` | `#23522C` | `#7A4E12` | `#EAF4EC` | `#8FD19E` |
| Night blue | `#1D4ED8` | `#1E3A8A` | `#B45309` | `#EAF0FE` | `#93B4FF` |
| Workshop orange | `#B54708` | `#8F3806` | `#1E3A5F` | `#FEF0E6` | `#FDB07A` |
| Plum violet | `#6B2C91` | `#53216F` | `#0F766E` | `#F3EAF8` | `#D2A8F0` |
| Graphite | `#374151` | `#1F2937` | `#B91C1C` | `#F1F2F4` | `#C9CED6` |
| Teal | `#0F766E` | `#0B5953` | `#9D174D` | `#E6F4F2` | `#5EEAD4` |

Every preset, the operator palette included, is only a **proposal**: the vendor can edit any
colour. Empty roles are derived by a pure function (`derive_palette`, fixed OKLCH lightness steps
from the primary and secondary) and stay editable. Every edit goes through the validator below.

Models live in `vendors`: `BrandPalette` (code, translated name, the role colours) and
`VendorBranding` (vendor, starting preset, the role colours as edited, logo, banner, generated
file name, updated by and at). The pure functions below live in `apps/themes/engine.py`.

### Contrast validation and nearest compliant colour

`check_brand_palette(palette, surfaces)` reuses `contrast_ratio` and the pair format of the token
contract (§14.4). Pairs, measured with the WCAG 2.x relative luminance formula:

| Pair | Minimum | Why |
|---|---|---|
| `on_primary` on primary; `on_primary` on primary hover | 4.5:1 | Buttons |
| primary on primary tint | 4.5:1 | Accent text on tinted sections |
| primary as link text on the surface | 4.5:1 | `--color-link` (1.4.3) |
| `on_secondary` on secondary; `on_secondary` on secondary hover | 4.5:1 | Secondary buttons and badges |
| secondary text on the surface | 4.5:1 | Secondary text and icons |
| dark-mode primary on `#121417` | 4.5:1 | Ready for the later dark token set (§14.4) |
| primary as non-text UI against the surface and the tint | 3:1 | Borders, icons, selected states (1.4.11) |

The secondary is background-only and always carries text, which identifies the component, so it
has no 3:1 rule against the surface; it is never used alone for borders or icons (the secondary
text role is). "The surface" is `#FFFFFF` and the resolved `--color-surface` of every registered
theme, so a palette stays valid whatever theme the site uses. A stored palette that fails after a
theme change is not applied, and a system check warning lists the vendors concerned.

When a pair fails, the form names the pair and its measured ratio and offers the **nearest
compliant colour**, which the vendor accepts in one click or replaces with another colour:

```python
def nearest_compliant(colour: str, against: str, min_ratio: float) -> str | None
    # Keeps the OKLCH hue and chroma of `colour` and moves only its lightness, in the direction
    # that raises the contrast with `against` (darker if `against` is lighter, lighter otherwise),
    # by the smallest step (0.001 L) until contrast_ratio >= min_ratio. Chroma is reduced only as
    # far as needed to stay inside sRGB. Returns `colour` unchanged if it already passes, and None
    # if no lightness in that direction passes.
def suggest_foreground(background: str, min_ratio: float) -> str | None
    # For a background the vendor keeps: `on` colour `#FFFFFF` or `#1A1A1A`, whichever passes
    # with the higher ratio; None if neither does.
```

When `nearest_compliant` returns None, the form says so plainly (for example: white text cannot
reach 4.5:1 on this orange) and, for a foreground role, proposes `suggest_foreground` for the
background the vendor locked.

Unit tests (THM.11):

- every one of the nine presets passes `check_brand_palette`;
- `nearest_compliant("#C65D00", "#FFFFFF", 4.5)` returns a colour of at least 4.5:1 close to
  `#BE5900`;
- `nearest_compliant("#046BD2", "#121417", 4.5)` returns a lighter colour of at least 4.5:1;
- a colour that already passes is returned unchanged;
- `nearest_compliant("#FFFFFF", "#FBA926", 4.5)` returns None, and `suggest_foreground("#FBA926",
  4.5)` returns `#1A1A1A`.

### Delivery and CSP

- `render_brand_css(palette, vendor_slug)` (pure) writes only custom-property overrides of the
  role tokens above, plus `--color-link`, in `@layer overrides`, scoped to
  `[data-vendor-brand="<slug>"]`. Component CSS is unchanged.
- The file is generated when the branding is saved, stored under a content hash, and served from
  the site's own origin (`/brand/<slug>.<hash>.css`, `Cache-Control: immutable`), so `style-src`
  stays `'self'`. No inline `<style>` and no `style` attributes.
- The storefront and product templates add the `<link rel="stylesheet">` and the
  `data-vendor-brand` attribute only when `plan_allows` is true and the palette is valid.
- Every change is audited (`AuditLog`) and the vendor sees a preview before publishing.

## 14.13 Tasks

THM.1 to THM.5 build the system and `base`; THM.6 to THM.10 build the first vertical theme; THM.11
is vendor branding. Both `base` and `ride` ship at go-live (Q-60), so THM.6 to THM.10 are P1.

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| THM.1 | Theme framework: `apps/themes` registry and manifest, theme-aware template loader with parent fallback and `(theme, name)` cache, `ThemeSettings` per Wagtail Site with audit, `MPMV_THEME_DEFAULT`, system checks, import-linter rule, ADR for the frontend toolchain | P1 | 8 | CMS.1 | Two sites render different themes in an integration test; a template missing in the child comes from `base`; an unknown theme fails the system check |
| THM.2 | Design-token pipeline: `tokens.toml` resolution and inheritance, token contract with contrast pairs, `tokens.css` rendering, `python -m apps.themes.build` (`make assets`), Containerfile build step | P1 | 6 | THM.1 | Unit tests on `resolve_tokens`, `contrast_ratio` and `check_tokens` (a pair below 4.5:1 fails); the image build produces hashed `theme.css` for every theme |
| THM.3 | `base` theme: layouts, skip link, header with disclosure navigation, language switcher, consistent help link, footer, typography, form controls, buttons, alerts, focus and target-size rules, reduced motion; vendored htmx and Alpine CSP build with `VENDORED.toml`; CMS.1 and account templates moved onto the layouts | P1 | 10 | THM.2 | Home, standard page and login render with `base`; CSP lint and the hash check of vendored files pass; keyboard-only e2e reaches every control with a visible focus |
| THM.4 | `base` component library (§14.9) and staff-only styleguide page | P1 | 12 | THM.3 | Every component renders in every state on `/_styleguide/` with no CSP violation; e2e screenshots saved |
| THM.5 | Theme quality gates, parametrised over registered themes: CSP template lint, axe-core (`wcag22aa`) on the styleguide, visual comparison harness with CI-only baselines, size budgets, `LICENSES.toml` check | P1 | 6 | THM.4, ARC.11 | A theme with a 3:1 text pair, an inline style, an unlicensed font or a 50 KB CSS file fails CI; `base` passes |
| THM.6 | First vertical theme `ride`: colour tokens from the operator palette (§14.4, §14.12), type scale, spacing and shapes from the reference template's visual language, self-hosted Barlow and Barlow Condensed (OFL 1.1, from upstream), SVG icon subset, header, hero and home block overrides, placeholder imagery, `LICENSES.toml` | P1 | 8 | THM.4, CMS.2 | Home page renders with `ride` and passes the THM.5 gates; no file derived from the reference template (PR checklist) |
| THM.7 | `ride` catalog pages: listings with filters, search results, product card, product page, vendor storefront | P1 | 6 | THM.6, CAT.7, CAT.8, VEN.10, TAX.12 | Catalog key pages pass the THM.5 gates at 360, 768 and 1280 px |
| THM.8 | `ride` cart, checkout and account pages: cart, checkout steps, Payment Element appearance from tokens, order confirmation, customer area, sign in and up, MFA, password reset | P1 | 6 | THM.6, ORD.5, ORD.7 | A keyboard-only e2e checkout completes in test mode with `ride`; the Payment Element uses the theme's colours and fonts |
| THM.9 | `ride` content and system pages: blog, standard, legal, contact, term and vendor landing, vendor recruitment, 404 and 500, consent banner styling | P1 | 4 | THM.6, CMS.3, CMS.4, CMS.5, SEO.5 | Content key pages and the consent banner pass the THM.5 gates; focus is never hidden by the banner or the sticky header |
| THM.10 | Visual regression baselines for `base` and `ride`: styleguide and key pages at three widths, CI job, update procedure in the developer docs | P1 | 4 | THM.5, THM.7, THM.8, THM.9 | CI compares every key page with its baseline; an intentional change needs a reviewed baseline update |
| THM.11 | Vendor branding (paid plan option): `BrandPalette` and `VendorBranding` in `vendors` with explicit foreground per role, the nine presets (operator palette included) seeded by a data migration, every preset editable, `derive_palette`, `check_brand_palette` over every registered theme's surface, `nearest_compliant` and `suggest_foreground` offered in one click, logo and banner (CMS.8 images), `render_brand_css` to a hashed same-origin file, storefront and product page hooks gated by `plan_allows`, preview, audit | P2 | 12 | VEN.19, THM.4, VEN.10, CAT.7, CMS.8 | The §14.12 unit tests pass (nine presets valid, `#C65D00` fixed close to `#BE5900`, fixed point unchanged, white on `#FBA926` gives None and `#1A1A1A` as foreground); a custom palette with a 3:1 pair is rejected with its ratio and a one-click fix; a branded vendor's storefront and product pages load its stylesheet with no CSP violation and pass axe; listings and checkout never load it; a vendor whose plan loses the entitlement renders with the theme only and keeps its stored branding |
