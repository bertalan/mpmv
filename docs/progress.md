# Progress

Task status and coherence checks. The backlog itself is generated from the spec
([90-backlog.md](spec/90-backlog.md)); this file records what is done and verified.
`make docs` validates that every task ID below exists in the spec.

Statuses: `done` (merged, done-when met) · `in review` (PR open) · `in progress` · `partial` (merged,
part of the done-when still open) · `not started`.

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
| I18N.1 | partial: "/it/ and /de/ active on STAGE" pending STAGE | #12 | Language-prefix routing, language switcher, preferred language |
| ARC.5 | partial: STAGE deploy pending hosting decision | #13 | OCI image, Apple container local stack, Podman Quadlets, tag deploy and rollback tooling, Procrastinate worker role |
| ARC.12 | done | #16 | factory_boy factories per model, hand-written test builders converted |
| CORE.2 | done | #18 | VendorMember with owner and staff roles, role matrix, last-owner rule, audited membership services |
| I18N.2 | partial: reused by vendor, catalog reuse pending CAT.1 | #19 | Shared TranslationModel with status, source_hash, stale detection and EN fallback; language choices no longer frozen in migrations |
| CORE.7 | partial: OpenAPI proven locally and in CI, STAGE publication pending hosting | #20 | Ninja API v1 skeleton: API key auth, dry-run, RFC 9457 problem details, OpenAPI 3.1, mutation log |
| CORE.5 | done | #21 | Progressive login lockout per account and IP, registration rate limit, Turnstile captcha, trusted-proxy client IP |
| CORE.4 | done | #22 | Mandatory MFA for staff, superusers and vendor owners: TOTP, passkeys, recovery codes, step-up |

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
- ARC.9: Heartbeat has no admin page yet. The worker container is resolved by ARC.5 (#13).
- ARC.9: human review requested for _BusinessTimePeriodicTask and on_app_ready, which use undocumented Procrastinate attributes.
- VEN.1: plan, payout_account, dac7_complete and logo fields wait for VEN.2, VEN.6, CMP.1 and the media stack.
- VEN.1: translation workflow status and source_hash go to I18N.2.
- VEN.1, CORE.6: client IP comes from REMOTE_ADDR; behind the reverse proxy it must come from the trusted proxy header (OPS, ARC.5). Resolved in CORE.5: `apps.core.http.client_ip` reads `MPMV_TRUSTED_PROXY_HEADER` only from `MPMV_TRUSTED_PROXIES`; Caddy overwrites `X-Real-IP` and `mpmv-net` has a fixed subnet; the API allow-list and admin audit use it too.
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
- VEN.1: the make_vendor test helper copied into three test modules is resolved by ARC.12 (#16).
- VEN.1: two concurrent vendor creates with the same name fail on the slug instead of getting a suffix.
- CORE.6: allowed_ips is a CharField array, not InetAddress as in spec 02, and stored values are not validated.
- CORE.6: the key format regex has no upper bound on secret length.
- CORE.6: issuing or rotating a key with a naive expires_at raises TypeError instead of InvalidApiClient.
- CORE.6: rotate and revoke check is_active on a stale instance without select_for_update.
- SEC.1: SECURE_REDIRECT_EXEMPT for /healthz is out of scope and belongs with the OPS.2 probe design.
- SEC.1: the strict CSP needs the Alpine.js CSP build and htmx without indicator styles or hx-on; note it for front-end tasks.
- SEC.1: no test covers a view that sets its own Permissions-Policy.
- SEC.1: a mixed Permissions-Policy allowlist such as ["*", "self"] emits an invalid quoted "*".

## Follow-ups from wave 2

Open questions for the owner:

- ARC.5: choose the hosting provider (price, EU region, managed PostgreSQL 17 with PITR) and record it as ADR-0005 requires.
- ARC.5: either provision STAGE and run a real tag deploy, rollback and unhealthy deploy, or narrow the done-when and add a new spec 01 row for the STAGE deploy.
- I18N.1: should `/` redirect signed-in users to their preferred language prefix? Today it always serves EN.

Deferred to other tasks:

- ARC.5: there is no automatic deploy from CI to STAGE; deploys run by hand with mpmv-deploy until a host and SSH secrets exist.
- ARC.5: release images are built only for linux/amd64; an arm64 server needs a multi-platform build.
- ARC.5: rollback does not undo migrations, so migrations must stay compatible with the previous release (expand, then contract).
- ARC.5: media has no writable location on STAGE/LIVE (read-only root, no volume); it needs an object storage backlog row before CAT.4 and SEC.2.
- I18N.1: the staff admin is always in EN, because unprefixed paths use the primary language.
- I18N.1: page strings are marked for translation, but .po/.mo catalogs come with I18N.5.
- I18N.1: hreflang, canonical and sitemaps go to I18N.6; wagtail-localize goes to I18N.3.
- I18N.1: the "/it/ and /de/ active on STAGE" check needs a smoke test once STAGE exists.

Review findings not fixed (all minor):

- I18N.1: User.preferred_language choices are frozen in the migration (coherence check 2, finding 1).
- ARC.5: concurrent deploy or rollback runs are not locked and can corrupt the release history.
- ARC.5: the release workflow publishes an image for any v*.*.* tag without checking that the commit is on main with green CI.
- ARC.5: WhiteNoise runs before the CSP and Permissions-Policy middleware, so static responses carry neither header.
- ARC.5: .dockerignore patterns for .env and key files match only at the root of the build context.
- ARC.5: the Caddy unit does not drop capabilities, and Caddy has no trusted_proxies for Cloudflare, so the app sees Cloudflare IPs.
- ARC.5: the implementation commit changed a parser in the test-only commit.

## Follow-ups from wave 3

Open questions for the owner:

- CORE.5: approve the lockout numbers (account 5 failures, IP 20, lock from 1 minute doubling to 1 hour, forgotten after 24 hours; registration 5 per IP per day) or set others in spec 02.
- CORE.5: should a locked user see a "too many attempts" message instead of the generic wrong-credentials error?
- CORE.2: may a closed vendor lose all its members (a staff-only removal), or must it always keep an owner?
- CORE.2: confirm that the storefront profile (texts, policies, logo) is owner-only, since spec 02 lists only orders, quotes and products for vendor staff.
- I18N.2: should a stale published translation stay visible until review, instead of falling back to EN after any EN edit?
- CORE.7: confirm the fail-open choice when a mutation-log row cannot be written (error logged, response still returned).
- CORE.4: confirm that vendor staff members (not owners) do not need MFA.

Deferred to other tasks:

- CORE.7: serve and smoke-test /api/v1/openapi.json on STAGE once it exists; until then the CI artifact openapi-v1 is the published schema.
- CORE.7: per-client API rate limit (spec 02) has no backlog row yet; it needs a cache shared across workers.
- CORE.7: ApiMutationLog needs a retention policy and an admin or report view (CORE.8, OPS), since it stores client IPs.
- CORE.7: 405 responses from the ninja dispatcher are plain text, not problem+json.
- CORE.5: no staff action to unlock an account, and a password reset does not clear the lock; candidate for CORE.8.
- CORE.5: future login forms must render the captcha widget when the captcha is enabled.
- CORE.5: the core.W001 trusted-proxy warning does not block deploys; Caddyfile and Quadlet subnet are tested statically only.
- CORE.4: no site or vendor login page yet, so MFA pages send anonymous users to the admin login.
- CORE.4: TOTP secrets are stored in plain text by django-otp; field-level encryption is task SEC.3 (requirement SEC.6 in spec 12 §12.3), whose scope lists IBAN, KYC and DAC7 but not TOTP secrets.
- CORE.4: step-up must be applied to IBAN, payout, rate and export views when they exist.
- CORE.4: passkeys are bound to the request host; MFA_WEBAUTHN_RP_ID and allowed origins settings are not added yet.
- CORE.2: no admin or UI for memberships yet; CORE.8 and CORE.9 must call the membership services.
- CORE.2: ApiClient.vendor FK still missing (CORE.6 follow-up or CORE.3).
- CORE.2: GDPR erasure must remove memberships first, since deleting a member user is blocked.
- I18N.2: review-before-publish transition rules go to I18N.4; the fallback notice and sitemap exclusion go to the storefront and SEO tasks.

Review findings not fixed (all minor):

- CORE.4: no test asserts that the session key rotates on verification.
- CORE.4: a wrong code during TOTP enrolment is not throttled, and concurrent device removals can leave zero factors.
- CORE.4: register_passkey has no dry_run, and a duplicate credential ends in a 500 instead of a 400.
- CORE.4: self-service actions by non-staff users are audited with source system.
- CORE.5: an attacker can keep an account locked indefinitely with one attempt per hour.
- CORE.5: the lock check is check-then-act, so a parallel burst gets a few extra guesses.
- CORE.5: TOO_MANY_ATTEMPTS and VERIFICATION_FAILED are not marked for translation.
- CORE.5: Turnstile answers are not checked for hostname, and token length is not bounded.
- CORE.5: the admin login template copies Django's upstream block and can drift on upgrades.
- CORE.5: every new lock writes an append-only AuditLog row keyed on the submitted identifier.
- CORE.2: change_member_role leaves updated_at stale on the caller's instance, and remove_member leaves its pk.
- CORE.2: Meta.ordering on the vendor FK adds a join to every default query.
- CORE.2: has_vendor_permission is typed for User only; AnonymousUser is untested.
- CORE.2: the last-owner rule and the VendorAction matrix are not written into spec 02.
- I18N.2: save(update_fields=...) can store a hash that does not match the stored texts, and a language change is not handled.
- I18N.2: resolve_translation ignores a prefetch cache (N+1 queries on listings).
- I18N.2: the vendor data migration imports runtime hash code instead of a frozen copy.
- I18N.2: stale detection lives in Model.save(), with no dry_run and no AuditLog.
- CORE.7: unknown /api/v1 paths answer a CSRF 403 page to POST instead of a 404 problem.
- CORE.7: refused unauthenticated mutating calls without credentials are not logged.
- CORE.7: no system check refuses an unscoped ApiKey on mutating routes.
- CORE.7: the docs claim validation details never echo submitted values, but Django messages can.
- CORE.7: a problem extension named request_id is overwritten, and problem() accepts non-error statuses.
- CORE.7: a test literal mimics the API key format.
- CORE.7: ApiMutationLog and the problem type convention are not documented in spec 02.
- Integration: CORE.4's MFA middleware check is accounts.E002, because CORE.5 took accounts.E001.

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

### Check 2 (2026-11-07, after ARC.7, ARC.9, VEN.1, CORE.6, SEC.1)

Scope: main at a807dbc. Code against spec chapters 01, 02, 04, 09 and 12 and ADRs 0003, 0004;
spec against spec; repo hygiene.

| Area | Result |
|---|---|
| Done-when criteria | Met for all five tasks: no platform country literal (guard test), heartbeat job scheduled and observable, vendor admin CRUD and status tests, 401/403 for expired, revoked and out-of-scope keys, header scan test. CI green on main |
| Spec 01 conventions | Logic in `services.py` and `engine.py`; engines import no ORM. Mutating services (`configure_platform_operator`, `change_status`, issue, rotate and revoke key) take `dry_run` and return a `ChangeSet` through `run_mutation`, which writes `AuditLog`. No money fields yet |
| ADRs | 0003 respected (nothing built that it delegates). 0004: Procrastinate only behind `apps.core.tasks`, wrapper offers `task`, `enqueue`, `periodic`, `locked` |
| App dependency graph | import-linter 3/3 kept: layers now cover vendors and api, Procrastinate contract, tax and shipping independence |
| Spec vs spec | `make docs-check` green. No open decision (D-xx) blocks a merged task or is decided by code |
| Scope creep | None found. Fields owned by later tasks (plan, payout_account, dac7_complete, logo, ApiClient.vendor) are left out |
| Anonymization | No real vendor names, legacy order numbers or hostnames; no credential-like literals (test keys and passwords are generated) |
| Migrations | One leaf per app; `makemigrations --check` clean |
| Gates | `make check` green; `make test` 316 passed; `make test-e2e` 5 passed |

Findings and actions:

1. Major. Language choices are frozen in migrations. `User.preferred_language` (CORE.1) and
   `VendorTranslation.language` (VEN.1) use `choices=settings.LANGUAGES`, so adding a language
   needs a new migration, while spec 09 says adding a language never needs code changes. Every
   future `*Translation` model would copy this. Action: use a callable for the choices (Django
   serializes the reference, not the list) before more translation tables exist; take it with
   I18N.1 or I18N.2.
2. Major. Spec 12 reuses task IDs for requirements. §12.3 numbers requirements SEC.1 to SEC.10,
   while §12.6 numbers tasks SEC.0 to SEC.6 with other meanings (requirement SEC.1 is the access
   model, task SEC.1 is HTTP hardening; requirement SEC.3 is HTTP hardening, task SEC.3 is
   field-level encryption). "SEC.1 done" can be misread. `make docs-check` only reads task tables,
   so it does not catch this. Not changed here: it is a spec table edit (owner question 5).
3. Minor. Staff manage vendors and the platform operator through the Django admin, while spec 02
   keeps the Django admin for the technical superuser on a restricted network and puts staff in
   Wagtail ViewSets. Acceptable until CORE.8; CORE.8 should take these screens over, and the
   restricted network rule needs an ARC.5 or OPS item.
4. Minor. "engine.py has no ORM" is a convention only. Action: add an import-linter contract
   that forbids `django.db` in `apps.*.engine` (a small ARC.4 follow-up).
5. Minor. `ApiClient.vendor` (spec 02) is still missing although VEN.1 is merged. The wave 1
   question on which task owns it stays open.
6. Minor. The "test database still in use" teardown warning did not come back in four local runs
   of `make test` on main. It appeared once on the ARC.9 branch before its review fixes. The likely
   source is a Procrastinate worker connection left open by an interrupted run, but this is not
   confirmed. `run_worker` and `defer_periodic` open and close their own pool. No action; reopen
   if it shows up again.

Owner questions:

1. SEC.1: live settings send HSTS for one year with includeSubDomains and preload. Confirm that
   every subdomain of the production domain will be HTTPS only before anyone submits the domain to
   the preload list.
2. CORE.6: a valid key used from an address outside its allow-list gets 403, not 401. Confirm.
3. VEN.1: closed is terminal, and a rejected application becomes closed (no separate rejected
   status, no reopening). Confirm.
4. ARC.9: the default retry policy (5 retries, backoff from 30 s doubling up to 1 h) and the
   heartbeat every 5 minutes without retries were chosen in code. Confirm, or tune them with the
   OPS alerting tasks.
5. Spec 12: may we rename the §12.3 requirement IDs (for example SR-1 to SR-10) so they no longer
   collide with task IDs?

### Check 3 (2026-11-07, after ARC.12, I18N.1, ARC.5, CORE.2, I18N.2, CORE.7, CORE.5, CORE.4)

Scope: main at 18bbe31. Code against spec chapters 01, 02, 09 and 12 and ADRs 0004, 0005, 0006;
spec against spec; coherence check 2 findings; repo hygiene.

| Area | Result |
|---|---|
| Done-when criteria | Met for ARC.12, CORE.2, CORE.5 and CORE.4 (role matrix, failed-attempt and MFA enforcement tests, vendor owner MFA through the real settings). ARC.5 and CORE.7 are partial and recorded as such. I18N.1 and I18N.2 were marked done but their done-when is not met yet (no STAGE; no catalog model): now partial |
| Spec 01 conventions | Logic in `services.py` and `engine.py`; engines import no ORM. Mutating services of CORE.2 and CORE.4 take `dry_run` and return a `ChangeSet`, except `register_passkey` and `confirm_totp_device` (finding 4). MFA and membership changes write `AuditLog` |
| App dependency graph | import-linter 3/3 kept. The layers contract lists whole packages, so `apps.api.v1`, the accounts MFA modules, `apps.core.http`, `apps.core.i18n` and `apps.core.translations` are covered. Vendor owner MFA reaches `apps.vendors` through the `MFA_REQUIRED_POLICIES` dotted path, not an import |
| Check 2 findings | Finding 1 resolved: with an extra `("fr", ...)` entry in LANGUAGES, `makemigrations --check` reports no changes and both language fields offer the new code at runtime. Finding 2 still open (owner question 5 of check 2). Findings 3, 4 and 5 still open. Finding 6 did not recur |
| Security | MFA required for staff, superusers and vendor owners, enforced by middleware (system check accounts.E002); step-up guards device removal and the admin MFA reset. Lockout per account and IP, trusted-proxy client IP in throttles, captcha, API auth, API mutation log and the core and vendor admin audit (not MFA audit, finding 1). CSP unchanged and strict; Turnstile sources only when the captcha is on; Swagger UI and the passkey script are served from our static files with the nonce |
| Factories | One factory per model (14 models incl. TOTPDevice), guard tests for coverage and use, fixed Faker seed, pseudonymous names. Remaining `objects.create` calls are migration and constraint tests |
| Spec vs spec | `make docs-check` green. No D-xx blocks a merged task or is decided by code |
| Anonymization | No real vendor names, legacy order numbers or hostnames; legacy user ids in tests are synthetic |
| Migrations | One leaf per app; `makemigrations --check` clean |
| Gates | `make check` green; `make test` 889 passed; `make test-e2e` 14 passed |

Findings and actions:

1. Major. MFA audit rows record the proxy address. `apps.accounts.http.audit_context` takes
   `REMOTE_ADDR`, so behind Caddy every enrolment, recovery code use, device removal and admin
   MFA reset is logged with the proxy IP. CORE.4 and CORE.5 were built in the same wave and the
   switch to `client_ip` missed this helper. Action: CORE.4 follow-up, use `client_ip` and test
   it; then merge the three `audit_context` helpers (accounts, core admin, vendors admin) into one
   in `apps.core`.
2. Major. The spec 12 ID collision (check 2, finding 2) has caused a wrong pointer: the CORE.4
   follow-up sent TOTP secret encryption to SEC.6, which is the penetration test task; the task
   is SEC.3. The collision covers SEC.1 to SEC.6, not SEC.0 to SEC.3 as check 2 said. Both
   fixed here; the rename is still an owner question.
3. Minor. I18N.1 and I18N.2 were recorded as done without their done-when. Status set to partial
   here. I18N.2 also implements the "EN edit sends IT and DE back to needs review" rule that is
   the I18N.4 done-when; I18N.4 must reuse it.
4. Minor. `register_passkey` has no `dry_run` or `ChangeSet` (confirmed; it is audited), and
   `confirm_totp_device` has none because verifying consumes the code. Neither exception is in
   spec 01. A duplicate credential race still ends in an IntegrityError.
5. Minor. Spec 02 §2.2 asks for a check against compromised passwords; no backlog task covers it
   (only Django's common password list is used).
6. Minor. Spec 02 sketches `User.mfa_enabled` and `is_staff_member`; the code derives MFA state
   from confirmed devices and uses `is_staff`. Update the sketch when spec 02 is next edited.
7. Minor. Removing the `owns_a_vendor` entry from `MFA_REQUIRED_POLICIES` would drop owner MFA
   silently; only the integration test guards it. A system check would make it a hard error.
8. Minor. The passkey e2e test does not assert zero CSP violations, unlike the TOTP, admin and
   API docs tests. MFA views run a few read-only device queries directly instead of through
   services.
9. Minor, carried. No import-linter contract forbids `django.db` in engines (check 2, finding 4);
   `ApiClient.vendor` is still missing (check 2, finding 5; CORE.3 may take it); the
   API-key-shaped test literal (CORE.7) is still in `test_problems.py`.

Owner questions:

1. CORE.7: `/api/v1/docs` and `/api/v1/openapi.json` are open without a key in every
   environment, LIVE included. Keep them public, or require a key or staff login on LIVE?
2. CORE.4: a verification counts as fresh for step-up for 5 minutes, and each user gets 10
   recovery codes of 16 characters. Confirm.
3. SEC.3: add TOTP secrets to the field-level encryption scope next to IBAN, KYC and DAC7?
4. Spec 02: which task takes the compromised-password check (a CORE.1 follow-up or a new CORE
   row)?
