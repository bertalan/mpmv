# Anonymous page cache (SEO.6)

Public CMS pages can be served to anonymous visitors from a cache, so a hit renders no page. The
cache is **off by default** (`MPMV_PAGE_CACHE=1` turns it on, after a STAGE soak). Signed-in
visitors never touch it. This page is the SEO.6 decision record and the contract for templates.

SEO.6 ships in slices: 1/3 the rules, the store and the middleware (this one); 2/3 the purges
(publish, unpublish, move, locale, view restrictions, vendor storefronts and events); 3/3 the GET
language links in the switcher, the production volume and the switch-on. Until 3/3 every real page
carries a CSRF token from the switcher form, so the rules below store nothing: that is the safe
direction, and the test `test_a_real_page_with_a_form_token_is_served_and_never_stored` pins it.

## Decision: mirror the rules of wagtail-cache, do not add the package (D-36)

wagtail-cache keeps whole responses with Django's cache middleware. What we need differs in four
ways: the CSP nonce rule (a replayed nonce defeats the policy), the CSRF and form rule, the
per-model purge (including a hook that `vendors` can use without importing `seo`), and a file store
shared between the web workers and the task worker with no Redis (ADR-0001). The package would
bring a dependency to audit, pickled whole responses with their headers, and still need all four.
So the rules are mirrored as a pure function (`apps/seo/page_cache_engine.py`), the store is a
Django `FileBasedCache` alias, and no dependency is added.

## The hard rule

**A cached page holds nothing per request**: no CSP nonce, no CSRF token, no cookie, nothing read
from a session or a user. Two checks enforce it for every candidate page
(`page_cache_engine.response_skip_reason`):

- what the request did: a CSP nonce was generated (`LazyNonce` is falsy until it is), a CSRF token
  was asked for, a cookie was set, the session was modified;
- what the body contains: a ` nonce=` attribute or a `csrfmiddlewaretoken` field.

A page that fails either is rendered for every visitor, as before ("a page that needs a nonce is
served uncached"). Nothing on the public pages uses a nonce today: only the Manager, the admin, the
account and checkout pages do (`git grep csp_nonce`), and those are not cached routes. The CSP
header is not stored: the CSP middleware sits outside the cache, so a hit gets the policy built
for that request, and Django drops the nonce source from it when no nonce was generated. The CSP
is never weakened to make a page cacheable. The same goes for HSTS, Permissions-Policy,
`X-Robots-Tag` (SEO.8) and `Content-Language`.

Rules for templates on cached routes: no inline script or style that needs `{% csp_nonce_attr %}`,
no `{% csrf_token %}` and no form, nothing for a signed-in user unless `request.user` says so (such
a visitor has a session cookie and bypasses the cache).

## What is cached

`GET` of a route named in `PAGE_CACHE_URL_NAMES` (`home` and `wagtail_serve`: the Wagtail page
tree), status 200, `text/html`, at most `PAGE_CACHE_MAX_BYTES` (256 KiB). Catalog, cart, the
Manager, accounts, the API, media and the admin are not cached routes.

Never read from or stored in the cache:

| Request | Why |
|---|---|
| not `GET` (`HEAD` included: conservative, a `HEAD` is rendered and not stored) | writes and conditional answers |
| any query string | tracking parameters and previews must not split or poison entries |
| cookie `sessionid`, the CSRF cookie, `messages`, `mpmv_consent` or `mpmv_garage` | signed in, cart, consent or garage state |
| `Authorization` header | API style access |
| path under `PRIVATE_PATHS` (`/admin/`, `/manager/`, `/account/`, ...) | previews and private areas |

Also not stored: a response with `Cache-Control: private`, `no-store`, `no-cache` or `max-age=0`,
a page with a view restriction on it or an ancestor (a `before_serve_page` hook,
`mark_unrestricted_page`, marks the request; an unmarked request is never stored, because a hit
would skip the restriction check), and a streaming response.

## Key

`cache_key` hashes: generation, scheme, host (the Wagtail site), path (the language prefix), the
active language, and whether indexing is forced off. The generation token is in every key;
`page_cache.invalidate()` writes a new one, which empties the cache for every process at once.
The middleware builds the key **before** it renders, so a change that commits during a render
leaves that page under the old, unreachable generation. The default lifetime is 300 s
(`PAGE_CACHE_SECONDS`), shortened by the view's `max-age` and by a running launch countdown
(`data-target`), so a page never outlives a launch.

## Headers

Stored and served pages send `Cache-Control: public, max-age=0, must-revalidate` and
`Vary: Cookie, Accept-Language`, plus `X-Page-Cache: HIT|MISS|BYPASS`.

## Storage

A Django `FileBasedCache` (alias `pagecache`) in `var/page-cache`, outside `MEDIA_ROOT` and
`STATIC_ROOT`. It pickles entries: never expose the directory and never share it with an untrusted
writer. A cache that cannot be read or written is a miss, never an error. Bounded by
`MAX_ENTRIES` 500 and 256 KiB per entry (about 128 MiB at most).

| Setting | Env | Default |
|---|---|---|
| `PAGE_CACHE_ENABLED` | `MPMV_PAGE_CACHE` | off |
| `PAGE_CACHE_SECONDS` | `MPMV_PAGE_CACHE_SECONDS` | 300 |
| `CACHES["pagecache"]["LOCATION"]` | `MPMV_PAGE_CACHE_DIR` | `var/page-cache` |

## Not here yet

Purges (slice 2). The LCP target (< 2.5 s on a product page) is measured on STAGE after switch-on:
a follow-up note, not part of the code slices.

## Tests

`apps/seo/tests/unit/test_page_cache_engine.py` (pure rules) and
`apps/seo/tests/integration/test_page_cache.py` (middleware: nonce, CSRF, cookie and session never
stored, bypass cases, countdown, restriction mark, key taken before the render).
