# Consent providers

The consent banner is one **provider** behind the `ConsentProvider` interface
(`apps/seo/providers.py`, SEO.9, owner decision D-10). The default, `self-hosted`, is our own
minimal banner (SEO.5). An operator who prefers an external certified consent platform (a CMP)
plugs it in with settings, never with a fork.

## What the default does

- Technical cookies are always on. Three categories need a choice: `preferences`, `statistics`
  and `marketing`.
- The banner offers *Reject all* and *Accept all* with the same button, the same size and the
  same weight, and a *Choose categories* disclosure. *Cookie settings* in the footer leads to a
  page where the choice is changed at any time, with or without JavaScript.
- The choice is kept in the `mpmv_consent` cookie for six months
  (`v1.<banner version>.<consent id>.<categories>`; `apps/seo/engine.py`). A new banner version
  (`BANNER_VERSION`) asks everyone again.
- Every decision is appended to `seo.ConsentLog`: time, granted categories, banner version,
  provider, page language and a random consent id. **No IP address, no user agent, no account.**
  A change of mind is a new row with the same consent id. Rows are never updated.
- The banner is the same markup for everyone and is `hidden` until `consent.js` finds no valid
  choice, so a page cache never holds one visitor's state. `POST /consent/` is exempt from CSRF
  (a cached page cannot carry a token) and refuses a request that does not come from this site.
- Consent Mode v2 signals (`ad_storage`, `ad_user_data`, `ad_personalization`,
  `analytics_storage`, `functionality_storage`, `personalization_storage`, `security_storage`)
  follow the categories. The page script publishes them as `window.mpmvConsent.signals()` and in
  a `mpmv:consent` event on `document`; a tag adapter (analytics, SEO.5 events) reads them there.

## Selecting a provider

```python
# settings (or the environment for the name)
MPMV_CONSENT_PROVIDER = "acme"  # default "self-hosted"
MPMV_CONSENT_PROVIDERS = {"acme": "acme_cmp.provider.AcmeProvider"}
```

An unknown name, or a class that cannot be imported or is not a `ConsentProvider`, fails the
`seo.E001` system check (`make check`, and every deploy). `seo.W001` warns when the provider
needs a script origin that `SECURE_CSP` does not list.

## How to add a consent provider

Subclass `ConsentProvider` and implement:

| Method | What it must do |
| --- | --- |
| `state(request)` | The visitor's choice as a `ConsentState`, for server-side decisions. **Undecided until a choice is made.** |
| `allows(request, category)` | Whether a category may run. `necessary` is always allowed; the default reads `state`. |
| `signals(request)` | The Consent Mode v2 state, `granted` or `denied` per signal, following the choice (default: derived from `state`). |
| `render_banner(request)` | The banner markup for every page. **The same for every visitor** (pages may be cached); the browser decides whether to show it. May be empty for a tool that injects its own banner. |
| `gate_script(category, src)` | Markup of a script that does **not load `src` until the category is granted**. Refuse an unknown category or an unsafe source with `ValueError`. |
| `record(request, action, selected)` | Keep the **proof** of a choice or a change of choice and return the new state. An external tool keeps its own log; say where in your provider's docs. |
| `preferences_url()` | Where a visitor changes the choice later, or `None`. The footer link uses it. |
| `csp_origins()` | The origins the provider needs in `script-src`. |

A provider must:

1. **Block before consent.** Nothing gated runs, loads or is requested before the visitor grants
   its category. `{% consent_script "statistics" "https://…" %}` calls `gate_script`, so the tag
   code is the same for every provider. The test `assert_inert` in
   `apps/seo/tests/integration/test_consent.py` shows what "inert" means: no loadable `src`.
2. **Signal state.** Expose the choice to the page (cookie, event or both) and keep Consent Mode
   v2 in step with it.
3. **Keep proof.** A record of who agreed to what and when, with the banner text version, and no
   more personal data than needed.
4. **Respect the CSP.** The CSP has no `'unsafe-inline'` (spec 14 section 14.5): no inline script
   without the request nonce, no inline handler. Any third-party origin is added to
   `SECURE_CSP["script-src"]` (and `connect-src`, `frame-src` when needed) by its own reviewed
   change, never as a wildcard, and listed in `csp_origins()`.
5. **Weigh accept and reject equally** and let the visitor change the choice as easily as it was
   given.

A provider whose choices do not arrive at our `consent/` endpoint leaves `accepts_choices` at
`False`: the endpoint and the settings page then answer 404.

`apps/seo/tests/fakes.py` is a complete second provider used by the tests; copy its shape.

## Reviewing changes

The consent log is personal-data handling (a random id, a time and the choices). Changes to the
log fields, the retention, the banner text (bump `BANNER_VERSION`) or the cookie format need
human review.
