# Security headers: which layer owns what (SEC.3, SEC.5, SEC.12)

Two layers send responses. The app (Django, behind gunicorn) answers everything it receives. The
reverse proxy (Caddy, `deploy/caddy/Caddyfile`) answers some requests itself: public media files,
the 404 for any other `/media/` path, its own error pages (502, 413) and the HTTP-to-HTTPS
redirect.

## Rule

- The app owns the values. `settings/base.py`, `settings/live.py` and `apps.core.security` define
  them, and the tests in `tests/integration/test_security_headers*.py` check every response type.
- Caddy only fills gaps. Its `security_headers` snippet sets each header with `?` (default): a
  value the app already sent is kept, so a proxied response is never changed and never gets a
  duplicate. The snippet is imported by the site block, `handle_errors` and the plain-HTTP redirect
  block, so it covers every response Caddy produces.
- Public media is the one place where Caddy sets a stricter value than the default: a sandbox CSP
  per file type, equal to `apps.core.media` (`RASTER_CSP`, `SVG_CSP`).
- `tests/unit/test_proxy_security_headers.py` compares the snippet with the app's values.

## Headers

| Header | App | Caddy snippet |
|---|---|---|
| `Strict-Transport-Security` | sets | default; on media and errors |
| `X-Content-Type-Options` | sets | default; media sets `nosniff` itself |
| `Referrer-Policy` | sets | default |
| `Cross-Origin-Opener-Policy` | sets | default |
| `Cross-Origin-Resource-Policy` | sets `same-origin` on every response, the 301 included (outermost middleware) | default |
| `X-Frame-Options` | sets | default |
| `Content-Security-Policy` | sets (per page) | default `default-src 'none'; frame-ancestors 'none'`; media uses its sandbox CSP |
| `Permissions-Policy` | sets | default, same value as `build_permissions_policy` |
| `Server` | not removable | deleted (`-Server`): Caddy's own and gunicorn's |
| `Via` | none | deleted (`-Via`): `reverse_proxy` adds `Via: 1.0 Caddy` |

ZAP findings and their accepted exceptions are in `docs/dev/security-scans.md`.

## Documented exceptions

- The app's own HTTP-to-HTTPS 301 (`SecurityMiddleware`) is returned before the CSP,
  Permissions-Policy and X-Frame-Options middleware run, with an empty body. Only CORP reaches it,
  because its middleware is the outermost one. In LIVE and STAGE
  Caddy redirects plain HTTP first (308, full set), so the app never sends it.
  `test_https_redirect_301_is_the_documented_exception` records the behaviour.
- The 421 for a Host header that differs from the TLS server name comes from `strict_sni_host`
  before any route runs. It has an empty body and carries `Server: Caddy` and no security headers.
  Caddy has no option to change it; the only thing it discloses is the proxy software.
