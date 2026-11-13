# Anonymous page cache (SEO.6, part 1)

With `MPMV_PAGE_CACHE=1` (LIVE and STAGE; off by default, enable after a STAGE soak), public CMS
pages are served to anonymous visitors from a cache, so a hit renders no page (its only queries are the per-request indexing verdict of SEO.8, which must stay fresh). Signed-in visitors never touch it. Responsive renditions and the Core Web Vitals
budget are later parts of SEO.6.

## The hard rule

**A cached page holds nothing per request**: no CSP nonce, no CSRF token, no cookie, nothing read
from a session or a user. Two checks enforce it for every candidate page
(`apps.seo.page_cache_engine.response_skip_reason`):

- what the request did: a CSP nonce was generated (`request._csp_nonce` is truthy), a CSRF token was
  asked for, a cookie was set, the session was modified;
- what the body contains: a ` nonce=` attribute or a `csrfmiddlewaretoken` field.

A page that fails either is simply not stored; it is rendered for every visitor, as before. The CSP
header is not stored: the CSP middleware sits outside the cache, so a hit gets the policy built for
that request (no nonce, because the stored HTML uses none). The same goes for HSTS,
Permissions-Policy, `X-Robots-Tag` (SEO.8) and `Content-Language`.

Consequences for templates and views on cached routes:

- No inline script or style that needs `{% csp_nonce_attr %}`. The theme already ships external
  modules only (spec 14 §14.5).
- No `{% csrf_token %}`, and no form at all on a cached page. The language switcher renders plain
  `<a href>` links to the same page under the other language prefix for an anonymous visitor (the
  URL prefix decides the language: no cookie, no session, no state change on GET). `switch_language`
  stays a CSRF-protected POST, used by the signed-in switcher and the Manager. A link has no
  one-time "not available in this language" notice (that needs the session): an untranslated
  object leads to the other language's home.
- Anything for a signed-in user (user menu, help button, account links) renders only when
  `request.user.is_authenticated`. Such a visitor has a session cookie, so they bypass the cache
  and the cache never sees their page. Never render a user detail for a visitor without a cookie.

## What is cached

`GET` of a route named in `PAGE_CACHE_URL_NAMES` (`home` and `wagtail_serve`: the Wagtail page
tree), status 200, `text/html`, at most `PAGE_CACHE_MAX_BYTES` (256 KiB). Catalog listings, the Manager,
accounts, the API, media and the admin are not cached.

Never served from or stored in the cache:

| Request | Why |
|---|---|
| not `GET` (`HEAD`, `POST`, ...) | writes and conditional answers |
| any query string | tracking parameters and previews must not split or poison entries |
| cookie `sessionid`, the CSRF cookie or `messages` (`PAGE_CACHE_BYPASS_COOKIES`) | signed in, or has per-visitor state |
| `Authorization` header | API style access |
| path under `PRIVATE_PATHS` (`/admin/`, `/manager/`, `/account/`, ...) | the editors' preview and private areas |

A response is also skipped when the view sent `Cache-Control: private`, `no-store`, `no-cache` or
`max-age=0`.

## Key and variation

`apps.seo.page_cache_engine.cache_key` hashes: generation, scheme, host (that is the Wagtail site),
path (that is the language prefix), active language, and whether indexing is forced off
(`MPMV_FORCE_NOINDEX` or demo mode). Theme and SEO settings are not part of the key: they drop the
generation when they change (below). Device class is not a variant yet; the renditions part adds it
if renditions ever differ by device (they are expected to use `srcset` and need no variant).

## Invalidation

One global *generation* token is in every key. `apps.seo.page_cache.invalidate()` writes a new
token, which empties the cache for every process at once. It is called on:

- Wagtail `page_published`, `page_unpublished` and `post_page_move`;
- save or delete of a live page, a `Site`, a `Locale`, and every site or generic setting
  (`BaseSiteSetting`, `BaseGenericSetting`: the theme choice, the event settings, the SEO panel);
- save or delete of a `PageViewRestriction` or `CollectionViewRestriction`.

The drop runs through `transaction.on_commit`, never at the signal: a page rendered between the
signal and the commit would read the old rows and be stored under the new generation. For the same
reason the middleware builds the key, generation included, **before** it renders the page, so a
change that commits during a render leaves that page under the old, unreachable generation.

A page with a view restriction on it or on an ancestor is never stored. A
`before_serve_page` hook (`apps/seo/wagtail_hooks.py`) marks the request with
`not page.get_view_restrictions().exists()`; it reads the restrictions itself and does not rely on
Wagtail's own check, which is the `on_serve_page` wrapper that runs after all `before_serve_page`
hooks. The middleware stores only requests marked clear, so an unmarked request is not stored. A hit
would otherwise skip the restriction check and `PageRestrictionProtectionMiddleware`.

Why global and not per URL: a page's HTML depends on its parents (menus, breadcrumbs, the language
switcher's translated URLs), its site's settings and its theme. A wrong guess is a stale page; an
unneeded miss costs one render.

Things that change a page without one of these signals **rely on the lifetime**
(`PAGE_CACHE_SECONDS`, default 300) and show up to five minutes late, unless someone wires them to
`invalidate()`:

- snippets, navigation menus and the footer (they are not Wagtail settings or pages);
- products and categories shown in a page block (title, price, stock, availability);
- events and event lists shown in a block (the event page's own publish is covered);
- vendor storefront and profile changes (spec 04 asks to invalidate them on a profile change:
  call `invalidate()` from the VEN service when it adds the hook);
- images replaced in place, and translations edited without publishing.

The SEO indexing toggle of PR #271 is covered if its model is a site or generic setting; otherwise
its save must call `invalidate()`. A running countdown shortens the lifetime to its target (`data-target`,
spec 10 §10.2), so a page never outlives a launch.

## Headers

Stored and served pages send `Cache-Control: public, max-age=0, must-revalidate` and
`Vary: Cookie, Accept-Language`, plus `X-Page-Cache: HIT|MISS|BYPASS` for debugging. A proxy may
keep a page but asks the app each time, so the app, which does the purging, stays in control.

## Storage and configuration

A Django `FileBasedCache` (alias `pagecache`) in `MEDIA_ROOT/.page-cache`, because the app and the
task worker both mount the media volume while the root file system is read-only. A scheduled
publish in the worker therefore reaches the app's cache. No Redis (ADR-0001) and no new
dependency. Caddy only serves image files from the media volume, so the cache folder is not
reachable. A cache that cannot be read or written is a miss: the page is rendered. Size is bounded:
`MAX_ENTRIES` 500 (older entries are culled) times 256 KiB per entry, about 128 MiB at most.

| Setting | Env | Default |
|---|---|---|
| `PAGE_CACHE_ENABLED` | `MPMV_PAGE_CACHE` | off everywhere by default; `MPMV_PAGE_CACHE=1` turns it on in LIVE and STAGE |
| `PAGE_CACHE_SECONDS` | `MPMV_PAGE_CACHE_SECONDS` | 300 |
| `CACHES["pagecache"]["LOCATION"]` | `MPMV_PAGE_CACHE_DIR` | `MEDIA_ROOT/.page-cache` |

To turn it off in an incident: set `MPMV_PAGE_CACHE=0` (or unset it) and restart. The next switch on starts with an
empty cache only if something called `invalidate()` meanwhile, which every publish does.

## Tests

`apps/seo/tests/unit/test_page_cache_engine.py` (pure rules) and
`apps/seo/tests/integration/test_page_cache.py` (real middleware stack: hits, variation by
language and host, bypass cases, nonce/CSRF/cookie never stored, invalidation on publish,
unpublish, move, delete and settings, `X-Robots-Tag` and robots meta on a hit).
