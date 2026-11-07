# ADR-0008: Frontend toolchain and themes without Node

- **Status:** Accepted
- **Date:** 2026-11-07
- **Deciders:** project owner, tech lead
- **Backlog:** THM.1 (recorded here), THM.2, THM.3, THM.5

## Context

[Spec 01](../spec/01-stack-and-architecture.md) §1.2 fixes the frontend as Django templates with
HTMX and minimal Alpine.js, and CSS with a lightweight build. [Spec 14](../spec/14-frontend-themes.md)
adds themes: a `base` theme plus vertical themes (the first one is `ride`), selectable per Wagtail
Site, all under the strict CSP of SEC.1 (no `unsafe-inline`, no `unsafe-eval`) and WCAG 2.2 AA.

Constraints:

- One person maintains the platform. Every toolchain is a supply chain to watch (OPS.4) and a
  step that can break the image build.
- The CSP forbids inline scripts, inline styles and `eval`, which rules out the default builds of
  Alpine.js and HTMX's `hx-on` attributes.
- Theme files hold no Python logic, and a theme must never change what the site does.

## Options considered

1. **Node toolchain (npm, a bundler, PostCSS or Tailwind):** a large ecosystem and utility CSS.
   But it adds a second package manager and lock file, hundreds of transitive packages to audit,
   a Node stage in the image, and utility-class markup that is hard to theme by tokens alone.
2. **Python-only build over plain modern CSS:** custom properties, cascade layers, native nesting
   and container queries are Baseline "widely available", so there is nothing to transpile. A
   short Python script resolves the design tokens, concatenates and minifies the CSS of each
   theme chain. JavaScript is two vendored files plus small ES modules.
3. **No build at all:** serve every CSS source file as is. Simple, but each page would load many
   files and the token values could not be validated or computed (contrast pairs).

## Decision

Option 2.

- **No Node, no npm.** CSS is plain modern CSS in the layers
  `reset, tokens, base, layout, components, utilities, overrides`. Component CSS uses only
  `var(--…)` tokens; raw colours live only in `tokens.toml`.
- **Build:** `tools/build_theme.py` (`make assets`, THM.2) resolves each theme's tokens through
  its chain, writes `tokens.css`, concatenates and minifies the chain's CSS into one `theme.css`
  per theme. It runs in the Containerfile build stage before `collectstatic`; the output is
  git-ignored and hashed by `CompressedManifestStaticFilesStorage`.
- **JavaScript:** htmx 2 and the CSP build of Alpine.js, self-hosted under `static/vendor/` and
  listed in `static/vendor/VENDORED.toml` with version, source URL, SHA-256 and licence; a unit
  test checks the hashes (THM.3). Theme behaviour lives in `Alpine.data(...)` components loaded
  as ES modules. No third-party code in themes, no CDN, no inline handlers.
- **Themes** are directories `themes/<slug>/` outside `apps/` (manifest `theme.toml`,
  `tokens.toml`, `templates/`, `static/themes/<slug>/`, `LICENSES.toml`). Every theme descends
  from `base`. The `apps.themes` app owns the registry, the template loader (first loader,
  cached per `(theme, template name)`, never wrapped by Django's name-keyed `cached.Loader`) and
  the per-site choice (`ThemeSettings`, audited; `MPMV_THEME_DEFAULT` as fallback). The Wagtail
  and Django admins are never themed. No domain app imports `apps.themes` (import-linter).
- **Quality gates** (THM.5) run for every registered theme: CSP template lint, axe-core with the
  WCAG 2.2 AA tags, size budgets, licence check, visual baselines.

## Consequences

- One dependency manager (uv) and one language for tooling; the image build gains a Python step,
  not a Node stage.
- No utility-CSS framework: components are written by hand against the token contract, which is
  more CSS to write in THM.3 and THM.4 but keeps a theme to tokens plus a few overrides.
- Browsers outside Baseline "widely available" get no transpiled fallbacks.
- Revisit if a needed feature has no Baseline support, if CSS grows past the budgets of spec 14
  §14.7 without a build step that only a Node tool provides, or if a frontend component needs a
  framework that only ships through npm.
