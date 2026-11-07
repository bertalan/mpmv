# Progress

Task status and coherence checks. The backlog itself is generated from the spec
([90-backlog.md](spec/90-backlog.md)); this file records what is done and verified.
`make docs` validates that every task ID below exists in the spec.

Statuses: `done` (merged, done-when met) · `in review` (PR open) · `in progress`.

## Tasks

| ID | Status | PR | Notes |
|---|---|---|---|
| ARC.1 | done | — | ADR-0001 accepted (Django 6.1 → 6.2 LTS, Wagtail 8.x) |
| ARC.2 | done | — | ADR-0003 commerce boundaries, ADR-0004 Procrastinate |
| ARC.3 | done | — | Project skeleton, settings per environment, Makefile |
| ARC.11 | done | #1 | TDD harness: unit / integration / e2e suites (ADR-0006) |
| ARC.4 | done | #2 | CI: static, secret scan, tests, e2e |
| ARC.6 | done | #3 | core: money, countries, ChangeSet, BaseModel, append-only AuditLog |
| CORE.1 | done | #4 | Custom User, email login, preferred language |
| ARC.7 | done | #10 | PlatformOperator as versioned configuration, admin, no hardcoded platform country |
| ARC.9 | done | #11 | Procrastinate worker and cron scheduler in business time, heartbeat job |
| VEN.1 | done | #8 | Vendor models, translations, status machine, admin |
| CORE.6 | done | #9 | ApiClient: hashed keys, scopes, expiry, rotation, revocation, IP allow-list |
| SEC.1 | done | #7 | Strict CSP, Permissions-Policy, hardened cookies, security headers |

## Follow-ups from wave 1

Open questions for the owner:

- ARC.7: confirm the choice values for tax_regime, commission_vat_mode and einvoice_channel with the tax advisor.
- ARC.7: may staff configure a PlatformOperator row that starts in the past? The service allows it today.
- ARC.7: should a future platform_entity FK (VEN.4, VEN.6) point to a configuration version row or to a stable legal entity key?
- VEN.1: confirm the extra status transitions (suspended to active, closing from any open status).
- VEN.1: should the vendor slug become read-only after creation?
- CORE.6: confirm that key authentication logic may live in apps/api, and record it in spec 01.
- CORE.6: confirm that rotation revokes the old key at once with no grace period, and record it in spec 02.
- CORE.6: track the ApiClient.vendor FK migration under VEN.1 or CORE.3? VEN.1 is now merged.

Deferred to other tasks:

- ARC.7: the EUR 10,000 distance-sales threshold goes to TAX.2; the tax context snapshot dataclass goes to TAX.3.
- ARC.7: no PlatformOperator row is seeded, so current_operator() fails until ARC.10 or the go-live checklist adds one.
- ARC.9: job retention cleanup and alerts on queue depth and failed jobs go to the OPS monitoring tasks.
- ARC.9: the worker container is ARC.5; Heartbeat has no admin page yet.
- ARC.9: human review requested for _BusinessTimePeriodicTask and on_app_ready, which use undocumented Procrastinate attributes.
- VEN.1: plan, payout_account, dac7_complete and logo fields wait for VEN.2, VEN.6, CMP.1 and the media stack.
- VEN.1: translation workflow status and source_hash go to I18N.2.
- VEN.1, CORE.6: client IP comes from REMOTE_ADDR; behind the reverse proxy it must come from the trusted proxy header (OPS, ARC.5).
- CORE.6: per-client rate limit and logging of mutating API calls go to CORE.7; key issuing admin goes to CORE.8.
- SEC.1: PAY tasks must allow the payment feature and add the payment provider sources to the CSP; CORE.5 needs the same for the captcha.
- SEC.1: __Host- cookie prefixes, CORP/COEP headers and a CSP report endpoint are not done yet.
- SEC.1: the CSRF cookie is HttpOnly, so front-end code must read the token from the page.

Review findings not fixed (all minor):

- ARC.7: history rows are immutable only in Python; QuerySet.update() and re-closing a closed row bypass the guard.
- ARC.7: concurrent configure_platform_operator calls end in a raw IntegrityError instead of a ValidationError.
- ARC.7: dac7_authority_country accepts non-EU countries.
- ARC.7: PlatformOperator.EInvoiceChannel shares its name with the CMP.2 EInvoiceChannel interface.
- ARC.7: current_operator() raises TypeError on a naive datetime instead of a clear error.
- ARC.9: the heartbeat worker test asserts on the whole log text, so Procrastinate's own log lines satisfy it.
- ARC.9: no test shows that rolling back the caller's transaction drops an enqueued job.
- ARC.9: record_heartbeat is a mutating service without dry_run or ChangeSet, and the exception is not recorded in the spec.
- ARC.9: run_worker(scheduler=False) swaps the global periodic task registry; safe only in tests.
- ARC.9: a local test run warned that the test database was still in use at teardown.
- VEN.1: admin create, edit and delete of vendor legal and tax data write no AuditLog.
- VEN.1: bulk "move to closed" has no confirmation step.
- VEN.1: VendorTranslation.language choices are frozen in the migration, so adding a language needs a migration.
- VEN.1: the make_vendor test helper is copied into three test modules.
- VEN.1: two concurrent vendor creates with the same name fail on the slug instead of getting a suffix.
- CORE.6: allowed_ips is a CharField array, not InetAddress as in spec 02, and stored values are not validated.
- CORE.6: the key format regex has no upper bound on secret length.
- CORE.6: issuing or rotating a key with a naive expires_at raises TypeError instead of InvalidApiClient.
- CORE.6: rotate and revoke check is_active on a stale instance without select_for_update.
- SEC.1: SECURE_REDIRECT_EXEMPT for /healthz is out of scope and belongs with the OPS.2 probe design.
- SEC.1: the strict CSP needs the Alpine.js CSP build and htmx without indicator styles or hx-on; note it for front-end tasks.
- SEC.1: no test covers a view that sets its own Permissions-Policy.
- SEC.1: a mixed Permissions-Policy allowlist such as ["*", "self"] emits an invalid quoted "*".

## Coherence checks

Run at least every 5 completed tasks: code vs spec and ADRs, done-when criteria, docs drift,
anonymization, `make check`, `make test`, `make test-e2e`.

### Check 1 (2026-11-07, after ARC.11, ARC.4, ARC.6, CORE.1)

| Area | Result |
|---|---|
| Done-when criteria | Met for all four tasks; CI green on #1–#3; #4 pending re-run |
| Spec 01 conventions | Money (Decimal, ROUND_HALF_UP per line, 4-dp internals, EUR), ISO countries (GR not EL), `public_id` UUID, dry-run `ChangeSet`, `AuditLog` (who/what/before/after/where): implemented as specified |
| ADRs | 0001 versions match `uv.lock`; 0002 local DB on Apple `container`; 0004 not yet used (ARC.9); 0006 suites in place |
| App dependency graph | import-linter contracts kept (2/2) |
| Anonymization | No vendor names, legacy identifiers or hostnames in new code; test credentials generated at runtime |
| Docs drift | README status line was stale ("empty shells") → fixed |

Findings and actions:

1. **Audit log and TRUNCATE.** The trigger blocks UPDATE/DELETE, but TRUNCATE stays allowed because
   the test runner flushes tables with it. Added to OPS.1: the application DB role must not have
   TRUNCATE or DDL rights.
2. **Secret scanners flag test literals.** GitGuardian and gitleaks flagged a throwaway CI database
   password and literal test passwords. CI and local databases now use trust auth (bound to
   localhost), and tests generate passwords with `secrets`. The rule is in CLAUDE.md.
3. **A commit was pushed with a failing type check** (fixed in the next commit). Rule: run
   `make check` before committing, not only before the PR.
4. **Parallel test runs collide on one test database.** Added `MPMV_TEST_DB_NAME`, used by
   parallel agents.
