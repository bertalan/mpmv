# SSRF-guarded outbound HTTP (MIG.12)

`apps/core/safe_http.py` is the only client for URLs that a person, a feed or a connector chose
(staff, vendors, supplier feeds, source connectors; spec 13 §13.2, SEC.9). Fixed provider URLs
(payments, VIES, captcha, geocoding) keep using `apps/core/outbound.py`.

```python
from apps.core.safe_http import safe_get

response = safe_get(url, max_bytes=2_000_000, timeout=(5, 15))
```

What the guard does:

- Allows `https` only, unless `allowed_schemes=("https", "http")`; ports 443 (and 80 for http).
- Refuses userinfo, control characters, spaces, backslashes, zone ids, IPv4 written in decimal,
  octal or hex, `localhost` and `*.localhost`. An IDN host is converted to punycode and judged in
  that form.
- Resolves the host itself and refuses the host if any answer is loopback, private, link-local,
  CGNAT, multicast, reserved, unspecified, a cloud metadata address, or an IPv4-mapped, NAT64 or
  6to4 wrapper of one of those.
- Connects to the vetted IP (no second lookup); the name is kept for SNI, certificate and `Host`.
- Refuses redirects by default. With `max_redirects=n` every hop repeats all the checks.
- Tries each vetted address in turn when a connection fails; all of them were vetted.
- Enforces a total `deadline` (default 30 s) over connect, headers and body, checked while
  streaming, besides the per-step `timeout`; a trickling server cannot hold a worker.
- On a redirect to another origin it drops the caller's credential headers (only Accept,
  Accept-Language and User-Agent survive).
- Reads the body in chunks and raises `ResponseTooLarge` past `max_bytes`.
- Raises typed errors (`SafeHttpError` subclasses); messages never carry the query or credentials.

Not in this slice: per-connector rate limit, retries with `Retry-After`, the per-call log line,
the connector interface and the `wordpress_db` wrapper (later MIG.12 slices). There is no
allow-list for internal hosts; add one only with an owner decision.

New outbound call sites are registered in `OUTBOUND_CALL_SITES` in
`tests/security/test_integration_attacks.py`.
