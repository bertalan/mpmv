# Security scan triage (SEC.5)

The weekly `security-stage` workflow runs OWASP ZAP (baseline) and nuclei against a deployed
environment (`deploy/ci/stage-scan.sh`). The log shows only plugin ids, risk levels and counts;
reports stay on the runner. This page records the alerts we fixed and the ones we accept.

## Fixed

- ZAP 10098, Cross-Domain Misconfiguration (medium): WhiteNoise sent
  `Access-Control-Allow-Origin: *` on every static file. `WHITENOISE_ALLOW_ALL_ORIGINS = False`
  in `mpmv/settings/live.py`; Caddy and the media view never sent it. Tests:
  `tests/integration/test_security_headers_by_response_type.py`,
  `tests/unit/test_proxy_security_headers.py`.
- ZAP 90004, missing `Cross-Origin-Resource-Policy` (low): `same-origin` on every response
  (`CrossOriginResourcePolicyMiddleware`, and the Caddy `security_headers` snippet for what Caddy
  answers itself).

## Accepted (ignored in `deploy/ci/zap-rules.tsv`, one line per plugin id)

- 90004, `Cross-Origin-Embedder-Policy` not set: `require-corp` would block the Stripe Payment
  Element and consent-gated third-party embeds, which load without CORP headers.
- 10110, Dangerous JS Functions (low): the only match is `eval(` and `new Function(` inside the
  vendored htmx 2.0.4 (`apps/themes/static/vendor/htmx.min.js`, hx-on and `js:` expressions).
  The layout sets `allowEval: false` (`themes/base/templates/layouts/base.html`) and the CSP has
  no `'unsafe-eval'`, so the code never runs. Our own JS has no such calls.

Both ids cover only these cases: 90004 is also raised for CORP, which is fixed above, and a
regression would be hidden by the ignore. The header tests above are what guards CORP.

## Re-triage

1. Run the workflow; any new alert at medium or higher fails it and is listed by plugin id.
2. Decide: fix it (with a test), or accept it. An accept needs a reason line in this page and one
   line in `zap-rules.tsv`; `tests/unit/test_stage_scan_report.py` checks the two agree.
3. On a htmx or other vendored library bump, recheck 10110 with
   `grep -c "eval(\|new Function" <file>` and confirm `allowEval` is still `false`.
4. To audit the ignores, remove a line locally and run the scan against STAGE from the workflow.
