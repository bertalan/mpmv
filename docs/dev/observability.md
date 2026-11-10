# Observability (OPS.2, app side)

## Logs
`LOGGING` writes one JSON object per line to stdout: `time`, `level`, `logger`, `message`,
`request_id`, and `exc_type` / `exc_text` for exceptions, plus any `extra=` fields.
`MPMV_LOG_FORMAT=plain` gives a readable line for a terminal.

- **Request id**: `RequestIdMiddleware` (outermost after the CORP middleware) keeps an inbound
  `X-Request-ID` of 8 to 64 letters, digits or dashes, else makes one, and answers it in
  `X-Request-ID`. The id is in a context variable, so every record of the request carries it.
- **Redaction** (`apps/core/observability.py`): `RedactSensitiveData` runs on the handler before any
  output, on the message, traceback, `stack_info` and extra fields. It removes e-mail addresses
  (also percent-encoded), IBANs of any country, EU VAT numbers, card-like numbers, the
  `Authorization` header of any scheme, URL credentials, provider key shapes (`sk_live_`,
  `whsec_` and so on), long tokens after `=`, and the value of any key containing `secret`,
  `passwd`/`password`, `pwd`, `token`, `api_key`, `private_key`, `session`, `cookie`, `csrf`,
  `signature` (also `pw`, `pin` as whole keys), quoted or not, in text and in dict reprs. Extra
  field values are redacted recursively (dicts, lists, tuples, objects through their text) on a copy,
  with depth and size limits. It prefers over-redaction: an order number of 13 or more digits looks
  like a card number. Do not log personal data on purpose; the filter is a safety net.
  It runs in the request thread, so every pattern is linear (matches start at the start of a
  run, possessive repeats) and a message is cut to 8 KB (tracebacks to 16 KB, keeping the end)
  before any pattern runs; `test_redaction_performance.py` guards both. Non-ASCII text is NFKC
  normalised first, so fullwidth digits and letters are caught (the logged text is the
  normalised form). Known gap: zero-width characters or homoglyphs inside a key name.

## Error tracking
`apps/core/errortracking.py`: an `ErrorTracker` protocol, adapters `none` (default, no-op) and
`log` (one ERROR line on `mpmv.errors`), `MemoryTracker` for tests. `MPMV_ERROR_TRACKER` is `none`
or `log`. No external provider is chosen (lane-a decision): adding one is a class in `ADAPTERS`.
Only the exception type is sent as the message; context is redacted. A root log handler forwards
`logger.exception` records. A failing tracker never breaks the caller.

## Health
- `/healthz`: liveness, constant answer, no dependencies (container health command).
- `/readyz`: database (`SELECT 1`), cache round trip, storage `exists`. 200 `{"status": "ok"}` or
  503 `{"status": "unavailable"}`, never the reason (it goes to the log). Unauthenticated,
  `no-store`, exempt from the LIVE HTTPS redirect like `/healthz`.

## Alerts
`apps/core/alerts.py`. An alert is an ERROR log line on `mpmv.alerts`, a tracker event and an
e-mail (template `core.ops_alert`) through the outbox, once per alert key and
`OPS_ALERT_REPEAT_HOURS`. Recipient: `MPMV_OPS_ALERT_EMAIL`, else the platform operator's support
address.

- `report_checkout_error(exc, context=...)`: for the checkout's error path (no checkout code
  exists yet; the CHK tasks call it). Reports the exception type only. Call it after the
  transaction rolled back (in the `except` clause, outside `atomic`), or the outbox e-mail rolls
  back with it (a warning is logged when it is called inside a transaction). The e-mail repeats per exception class and message (digits ignored).
- `/readyz` logs a failure at most once per dependency per minute.
- `uv run python manage.py check_ops`: for the host's timer. Exit 1 when an alert is raised.

| Check | Settings (env `MPMV_` + name) | Alert keys |
|---|---|---|
| Disk space | `OPS_DISK_PATHS` (comma list), `OPS_DISK_MIN_FREE_PERCENT` (15) | `disk:<path>` |
| WAL archiving | `OPS_WAL_ARCHIVE_CHECK` (off), `OPS_WAL_MAX_LAG_SECONDS` (900) | `wal:never`, `wal:failing`, `wal:lag` (from `pg_stat_archiver`) |
| Backup age | `OPS_BACKUP_STATUS_FILE`, `OPS_BACKUP_MAX_AGE_HOURS` (26) | `backup:missing`, `backup:age`, `backup:unreadable` |

The status file holds one ISO 8601 timestamp of the last successful backup, written by the host.
A check with no setting is skipped.

Tests: `apps/core/tests/unit/test_observability_logs.py`, `test_errortracking.py`,
`test_ops_checks.py`, `apps/core/tests/integration/test_readiness.py`, `test_ops_alerts.py`
(the simulated checkout error).
