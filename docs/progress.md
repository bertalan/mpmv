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
| I18N.1 | partial: "/it/, /de/, /fr/ and /es/ active on STAGE" pending STAGE | #12 | Language-prefix routing, language switcher, preferred language |
| ARC.5 | done | #13 | OCI image, Apple container local stack, Podman Quadlets, tag deploy and rollback tooling, Procrastinate worker role. Done-when narrowed by owner decision; the STAGE deploy moved to ARC.13 |
| ARC.12 | done | #16 | factory_boy factories per model, hand-written test builders converted |
| CORE.2 | done | #18 | VendorMember with owner and staff roles, role matrix, last-owner rule, audited membership services |
| I18N.2 | done | #19 | Shared TranslationModel with status, source_hash, stale detection and EN fallback; language choices no longer frozen in migrations. Reused by vendor and catalog (CAT.1, #27) |
| CORE.7 | partial: OpenAPI proven locally and in CI, STAGE publication pending hosting | #20 | Ninja API v1 skeleton: API key auth, dry-run, RFC 9457 problem details, OpenAPI 3.1, mutation log |
| CORE.5 | done | #21 | Progressive login lockout per account and IP, registration rate limit, Turnstile captcha, trusted-proxy client IP |
| CORE.4 | done | #22, #32 | Mandatory MFA for staff, superusers and every vendor member (owners and staff since #32, owner decision): TOTP, passkeys, recovery codes, step-up |
| SHP.1 | done | #25, #38 | Carrier, zones, vendor rates, weight bands (exclusion constraint), pickup options, config versions; audited admin |
| CAT.1 | done | #27 | Product, Variant and translations, per-vendor SKU constraints, audited price services, admin |
| CORE.3 | done | #26 | VendorScopedQuerySet, 404 helpers, cross-tenant harness with negative tests generated for every endpoint, API operation and vendor-owned model (catalog and shipping included) |
| SEC.4 | done | #28 | AuditLog on logins, MFA, users, groups, vendors and shipping rates with before/after; read-only staff audit view with filters and step-up; audit policy system check; one request audit context |
| VEN.12 | done | #29 | Vendor legal and e-invoicing identity, staff-editable payout requirements by country, region and legal form including the DAC7 dataset, FatturaPA party block |
| TAX.1 | done | #35 | TaxRate with non-overlapping validity periods, Territory, versioned VendorTaxProfile tied to the VEN.12 fiscal regime, VatValidation as an append-only VIES cache; audited services with dry-run, add-only admin. Tax code, flagged for careful human review |
| CORE.13 | done | #34 | Optional `ApiClient.vendor` (empty means a platform key); vendor keys scoped through CORE.3 with negative tests for every API operation; issuing needs `api.add_apiclient` or vendor ownership; read-only admin with audited revoke |
| SEC.3 | done | #36 | AES-256-GCM field encryption with versioned keys outside the database for Vendor.iban and Vendor.tax_code; raw SQL dump test finds no cleartext; key checks at migrate, `rotate_field_keys` with dry-run and audit. KYC files and the DAC7 date of birth get it when their models exist |
| VEN.5 | done | #37 | Application, onboarding and activation flow; versioned contract with per-language texts locked by database triggers once accepted; append-only acceptances tied to the version, language and text hash shown; activation blocked until the contract is accepted |
| CMS.1 | done | #40 | Wagtail 8.1 with one locale per configured language, home and About pages per language, per-language header and footer menus, editors' admin at /admin/ under MFA and strict CSP, repeatable `seed_cms` |

## Owner decisions 2026-11-07

- Hosting: Hetzner Cloud (EU, x86), app VPS with Podman Quadlets, self-managed PostgreSQL 17 on a dedicated database VPS, PITR with pgBackRest to Hetzner Object Storage ([ADR-0007](adr/0007-hosting-hetzner-self-managed-postgres.md), supersedes ADR-0005; OPS.1 updated).
- ARC.5 done-when narrowed to what is proven locally and in CI; the STAGE deploy, rollback and the STAGE checks of CORE.7 and I18N.1 move to the new task [ARC.13](spec/01-stack-and-architecture.md).
- Media storage on Hetzner Object Storage with private buckets and signed URLs: new task [ARC.15](spec/01-stack-and-architecture.md), required by CAT.4 and SEC.2.
- `platform_entity` on LedgerEntry and PayoutAccount points to the PlatformOperator version row; balances per company group versions by legal identity ([spec 04](spec/04-vendors-commissions-ledger.md)).
- Vendor-scoped API keys: new task [CORE.13](spec/02-core-identity-permissions.md) (`ApiClient.vendor`).
- Languages (D-18 revised): EN, IT, DE, FR and ES at go-live, in that order; interface text translated into all five (I18N.7), visitor content EN only until after go-live with the EN-canonical fallback (new P2 task I18N.8); images neutral unless dedicated to one language, translatable alt text and captions (CAT.4, I18N.3). Whether legal pages must be translated at go-live is still open. P1 +6 h, P2 +6 h ([spec 09](spec/09-multilingual.md)).
- VEN.12 payout requirements: defaults confirmed; non-EU vendors need IBAN and foreign TIN; the DAC7 dataset (registered office address, country of tax residence, VAT number or TIN) and the FatturaPA party data are payout requirements for every vendor ([spec 04](spec/04-vendors-commissions-ledger.md), [spec 11](spec/11-compliance.md) CMP.1).
- Vendor legal documents are not visitor content: versioned documents with one text per language (IT, EN, DE, FR, ES), handled apart from CMS content, which stays English only until after go-live. The vendor accepts the version in its own language; each version declares a prevailing language; without a text in the vendor's language the vendor accepts the English text with an explicit acknowledgement. The exact accepted document (language, text, hash) is stored ([spec 04](spec/04-vendors-commissions-ledger.md), [spec 09](spec/09-multilingual.md), [spec 11](spec/11-compliance.md)).
- Go-live prerequisite (owner's business task, not a backlog task): legal translation of the vendor contract and the other vendor legal documents into IT, EN, DE, FR and ES, with the prevailing language chosen.
- Contract personalization on three levels: templates per language with merge fields from the vendor's data, vendor-specific clauses with four-eyes approval by a second staff member, fully custom uploaded contracts; hash and timestamp cover the exact personalized PDF; a template or clause change issues a new document to re-accept: new task VEN.13.
- Signature at go-live: simple electronic signature (eIDAS SES) with email one-time code plus MFA step-up, stored PDF with SHA-256 hash, RFC 3161 timestamp behind a `TimestampAuthority` interface, evidence package emailed to the vendor; a third-party e-signature provider only later, behind a `SignatureProvider` interface: new task VEN.14. Qualified signature upload (PAdES, CAdES) verified with pyHanko and the EU trusted lists: new task VEN.17 (P2).
- Vendor document area with every accepted document, evidence, timestamp and signed upload, kept per the retention rules: new task VEN.15.
- Contract updates: re-acceptance within a deadline (default per version, per-vendor override by staff), email reminders, a configurable consequence per version with per-vendor override (warn, block payouts, block sales, suspend), audited and reversible, staff view of pending vendors; acceptance lifts automatic restrictions but not a staff suspension: new task VEN.16. CMP.5 no longer covers vendor terms acceptance.
- Double signature for clausole vessatorie (articles 1341 comma 2 and 1342 of the Italian civil code, which apply to vendor contracts too): templates and staff-added vendor clauses mark them with number and title (VEN.13, 18 h); the PDF has two signature blocks, the second with the formula "Ai sensi e per gli effetti degli artt. 1341 e 1342 c.c. si approvano specificamente le clausole n. ..."; the signature flow has two separate acts, the acceptance of the whole contract and then the specific approval of the marked clauses on a separate screen with a second email one-time code, never both in one action, with evidence and an RFC 3161 timestamp for each act (VEN.14, 22 h). The legal risk of a simple electronic signature for article 1341 is recorded in [spec 11](spec/11-compliance.md), to be confirmed by counsel ([spec 04](spec/04-vendors-commissions-ledger.md)).
- The specific approval is completed only with a qualified or advanced electronic signature upload (VEN.17, now P1, 18 h) or with the paper original signed by hand and sent by post or courier, checked and approved by a staff moderator who records the receipt date, the scan and the archive location (VEN.16, 20 h). The two online acts let the vendor start operating; until completion a "specific approval pending" requirement applies with its own deadline and consequence on the VEN.16 machinery (defaults are an owner question), and the contract text warns the vendor. Staff can narrow the routes per version or per vendor, for example to require a qualified signature.
- Transactional email: new task [ARC.16](spec/01-stack-and-architecture.md) (P1, 8 h): `EmailSender` interface with an EU provider (choice pending), per-language templates, DKIM, SPF and DMARC, bounce and complaint handling, outbox with retries on the task queue, test backend. VEN.11, VEN.14, VEN.16, ORD.8, ORD.9, SHP.8, SHP.10 and ING.10 depend on it.
- Frontend themes ([spec 14](spec/14-frontend-themes.md), #47): a vertical-neutral `base` theme plus vertical themes with design tokens, template overrides and CSP-safe components; the CMS.7 row is replaced by THM.1 to THM.10 (70 h), CMP.9 (3 h, accessible authentication) is added, and WCAG 2.2 AA is the target: P1 +49 h.
- Theme answers (#50, Q-59 to Q-64): `ride` is the first vertical theme and both `ride` and `base` ship at go-live; fonts Barlow and Barlow Condensed, self-hosted; images only from free commercial-use licences or own work, with source, licence and author required (new task CMS.8, 5 h) and a launch countdown block (new task CMS.9, 4 h); vendor branding as a paid plan option (THM.11, P2) with plan entitlements and plan fees on non-fiscal statements (VEN.19, P2): P1 +9 h.
- Existing Italian content at go-live (D-18 refined, owner answer to the docs check question): the IT texts of the current site (products, categories, taxonomy terms, CMS pages, blog) are migrated and published at go-live (MIG.3, MIG.7); new visitor content stays EN only until after go-live, and DE, FR and ES visitor content comes after go-live (I18N.8). Migrated pages carry EN and IT hreflang, new pages EN only; ING.5 writes EN only, and the SEO.3 feed has an IT entry only for products with an IT translation.
- No invoicing and no bookkeeping: the marketplace issues only non-fiscal documents (per-vendor order confirmations, the CMP.2 commission statement or pro-forma, payout statements and ledger extracts), each labelled "Documento non fiscale / Non-fiscal document" in all five languages, with a stable reference ID and never an invoice number sequence; vendors invoice customers and the operator's accountant invoices commissions outside the platform. The ledger is an operational record for balances, payouts and Stripe reconciliation, not accounting. The `EInvoiceChannel` and the SDI intermediary are dropped (ADR-0003 updated); the VEN.12 e-invoicing fields stay as data for the accountant and for vendors ([spec 00](spec/00-vision-and-review.md), [spec 04](spec/04-vendors-commissions-ledger.md), [spec 11](spec/11-compliance.md)).
- Vendor obligations checklist with self-attestation (new task CMP.10, 24 h): per vendor, the obligations that apply (VAT and OSS, invoices and e-invoicing mandates, consumer rights, GPSR, batteries, WEEE, packaging, tyres, oils, product conformity, green claims, connected products), each answered "yes", "no" or "in progress" with an expected date; append-only answers with version, actor and audit; date-based reminders and periodic reminders on "no" with a one-click link, staff flag after N unanswered reminders or an overdue date; default consequence is reminder plus staff flag, with an optional per-obligation consequence on the VEN.16 mechanism; reminders are service emails, logged as evidence. Platform duties (DAC7 data, DSA article 30 trader data, KYC) stay blocking in the VEN.12 payout table and VEN.5 activation.
- Regulatory check 2026-11 ([report](dev/regulatory-check-2026-11.md)): CMP.2 reworked (8 h), CMP.3 (12 h), CMP.5 moved to P1 (10 h), CMP.6 (10 h), new CMP.8 (4 h), CMP.11 (16 h), CMP.12 (4 h), CMP.13 (6 h, blocked by D-21) and VEN.18 (2 h): P1 +68 h, P2 −6 h.

## Follow-ups from wave 1

Open questions for the owner:

- ARC.7: confirm the choice values for tax_regime, commission_vat_mode and einvoice_channel with the tax advisor.
- ARC.7: may staff configure a PlatformOperator row that starts in the past? The service allows it today.
- ARC.7: should a future platform_entity FK (VEN.4, VEN.6) point to a configuration version row or to a stable legal entity key? Resolved: the version row (owner decisions 2026-11-07).
- VEN.1: confirm the extra status transitions (suspended to active, closing from any open status).
- VEN.1: should the vendor slug become read-only after creation?
- CORE.6: confirm that key authentication logic may live in apps/api, and record it in spec 01.
- CORE.6: confirm that rotation revokes the old key at once with no grace period, and record it in spec 02.
- CORE.6: track the ApiClient.vendor FK migration under VEN.1 or CORE.3? Resolved: new task CORE.13.

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
- ARC.7: PlatformOperator.EInvoiceChannel shares its name with the CMP.2 EInvoiceChannel interface. Moot since #49: the interface is dropped and the field is informational only.
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

- ARC.5: choose the hosting provider (price, EU region, managed PostgreSQL 17 with PITR) and record it as ADR-0005 requires. Resolved: Hetzner with self-managed PostgreSQL, ADR-0007 supersedes ADR-0005.
- ARC.5: either provision STAGE and run a real tag deploy, rollback and unhealthy deploy, or narrow the done-when and add a new spec 01 row for the STAGE deploy. Resolved: done-when narrowed, new task ARC.13.
- I18N.1: should `/` redirect signed-in users to their preferred language prefix? Today it always serves EN.

Deferred to other tasks:

- ARC.5: there is no automatic deploy from CI to STAGE; deploys run by hand with mpmv-deploy until a host and SSH secrets exist.
- ARC.5: release images are built only for linux/amd64; an arm64 server needs a multi-platform build.
- ARC.5: rollback does not undo migrations, so migrations must stay compatible with the previous release (expand, then contract).
- ARC.5: media has no writable location on STAGE/LIVE (read-only root, no volume); it needs an object storage backlog row before CAT.4 and SEC.2. Resolved: new task ARC.15.
- I18N.1: the staff admin is always in EN, because unprefixed paths use the primary language.
- I18N.1: page strings are marked for translation, but .po/.mo catalogs come with I18N.5.
- I18N.1: hreflang, canonical and sitemaps go to I18N.6; wagtail-localize goes to I18N.3.
- I18N.1: the "/it/, /de/, /fr/ and /es/ active on STAGE" check needs a smoke test once STAGE exists.

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
- CORE.4: confirm that vendor staff members (not owners) do not need MFA. Resolved: every vendor member needs MFA (owner decision, #32).

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
- CORE.2: ApiClient.vendor FK still missing (CORE.6 follow-up or CORE.3). Now task CORE.13.
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

## Follow-ups from wave 4

Integration notes:

- CORE.3 merged after CAT.1 and SHP.1, so their product, variant, rate, band and pickup models were moved to VendorScopedManager during integration.
- SEC.4 merged after them: catalog and shipping models are classified in the audit policy, and catalog and shipping admin tests ignore the login entry that staff sign-in now writes.
- Coherence check 3, finding 1 resolved in SEC.4: every view and admin builds its audit context with `apps.core.audit.audit_context`, which uses the trusted-proxy client IP; a guard test refuses new copies.
- VEN.12 merged with SEC.4: one `save_vendor` validates the record and audits the whole vendor, legal identity included and IBAN masked, as `vendor.updated`.

Open questions for the owner:

- CAT.1: simple products hold stock on Product itself; confirm, or should they get one implicit variant?
- CAT.1: product and variant SKUs are unique per vendor in separate namespaces; should one shared namespace be enforced (it matters for CAT.9)?
- CAT.1: on-request and catalog-only products store a net price of 0.00; acceptable, or should the price be nullable?
- CAT.1: may variants exist only on variable products, and must companions and upsells belong to the same vendor?
- CAT.1: may a product change vendor after creation? Today the variants follow, and a SKU clash ends in a 500.
- CORE.3: `for_actor` lets every active `is_staff` user see all vendors; decide which flag or permission means platform staff before Wagtail editors get `is_staff` (CORE.8).
- SEC.4: lockout audit entries still store the typed identifier for unknown accounts; treat them like failed logins?
- SEC.4: should vendor refund and shipping policy texts be audited (VendorTranslation is exempt today)?
- VEN.12: the payout gate requires a postal code from every vendor; how should vendors in countries without postal codes fill it?
- VEN.12: Greece uses GR as FatturaPA IdPaese and EL in VIES; confirm with the SDI intermediary (CMP.2). Since #49 there is no SDI intermediary: the question applies to the FatturaPA-mappable CMP.2 export and goes to the operator's accountant.
- VEN.12: accept the `# country-literal:` pragma added to the ARC.7 scanner, or restrict it to named constants?

Deferred to other tasks:

- SHP.1: the over-max-weight policy (D-06) and the per-vendor free-shipping threshold go to SHP.3; the SHP.6 editor replaces the admin.
- SHP.1: shipping audit snapshots carry the vendor pk, not its public id, so the audit view's vendor filter misses shipping rows.
- CORE.3: no vendor-facing endpoint exists yet; CORE.9 and the catalog API add the first VendorScoped and VendorScopedList entries.
- CAT.1: category, brand and terms (CAT.2), options (CAT.3), images (CAT.4), slugs (CAT.5), GPSR (CMP.3), publishing (CAT.10), rich text (CAT.11).
- SEC.4: commissions, IBAN, payouts and data exports get audited when their models exist (pattern in docs/dev/audit-trail.md).
- SEC.4: product deletion and non-price product edits are classified as unaudited catalog content.
- SEC.4: anonymous failed-login entries grow the append-only table; retention or partitioning is an OPS item.
- VEN.12: a real VIES client and periodic re-check: the client is task TAX.5 (VIES client with cache, timeout, fail-closed); the periodic re-check of vendor numbers has no row yet.
- VEN.12: the IBAN and DAC7 values are stored in clear and kept in append-only audit rows until SEC.3 adds field-level encryption.
- VEN.12: there is no vendor-facing profile form yet (VEN.9, CORE.9).
- VEN.12: fiscal_regime is not checked against VendorTaxProfile.exempt_reason (TAX.2, CMP.2).

Review findings not fixed (all minor):

- SHP.1: the admin bulk delete is not wrapped in a transaction, and cascaded band prices are not in the rate's delete snapshot.
- SHP.1: ShippingConfigVersion is immutable only through save(), and migration 0001 references NumRange in the live models module.
- SHP.1: no test for the pickup translation unique constraint; a band form with a field error can add a spurious overlap error.
- CORE.3: StaffOnly and NoVendorData always use GET, so POST-only endpoints are not exercised with POST.
- CORE.3: ownership through ManyToMany or GenericForeignKey is not detected, and the markers leave out Vendor B's display name and slug.
- CORE.3: a unit test patches `VendorMember.vendor_scope` instead of using a test-only model.
- CAT.1: to_price lets decimal.InvalidOperation escape and does not check the 10 integer digits of MoneyField.
- CAT.1: the admin price change check reads stored prices without a row lock.
- CAT.1: normalize_sku is used only by tests, SKUs have no character set limit, and naive sale datetimes are accepted.
- SEC.4: save_vendor does not refuse a status change made outside change_status.
- SEC.4: step_up() does nothing when STEP_UP_DECORATOR is unset, with no system check.
- SEC.4: group audit entries are keyed by the group name, so a rename splits the trail.
- VEN.12: check_vat_number does not lock the row or compare the stored number, and sends non-EU numbers to VIES.
- VEN.12: a sole proprietor without first and last name falls back to the legal name without a problem being reported.
- VEN.12: the address data migration does not check field lengths before copying legacy values.
- VEN.12: seed_legal_requirements lives in tests/fakes.py, which is meant for doubles of external boundaries.

## Follow-ups from wave 5

Integration notes:

- Merge order: TAX.1 (#35), CORE.13 (#34), SEC.3 (#36), VEN.5 (#37), CMS.1 (#40).
- VEN.5's vendor migrations were renumbered to 0009, 0010 and 0011 so they follow SEC.3's 0008_encrypt_iban_and_tax_code (one leaf).
- CMS.1 merged after the five-language change (#41): locales come from settings.LANGUAGES, the seed has synthetic FR and ES texts, and the "language added in settings" tests use a hypothetical sixth language (pt).
- CMS.1 keeps MFA_REQUIRED_POLICIES at exactly `is_vendor_member` plus `can_access_cms_admin`.
- TAX.1 and SHP.1 both need btree_gist; the tax migration creates it with IF NOT EXISTS and never drops it.

Open questions for the owner:

- TAX.1: for the tax advisor, confirm that a forfettario vendor (RF19) must be exempt with reason flat_rate_forfettario, and that this reason is allowed only for RF19.
- TAX.1: validity bounds are instants in UTC; decide whether staff enter local midnight of the rate's country or business time, before TAX.3 turns a date into an instant.
- CORE.13: which scopes may a vendor key hold? Today an owner can give their key any valid scope; this needs a vendor-grantable scope set or platform-only operations before API v1 exposes platform data.
- CORE.13: should keys of a suspended or closed vendor keep authenticating? Today they do, limited to their own vendor.
- SEC.3: confirm the encrypted field list: IBAN and TIN now, KYC files, payout accounts and the DAC7 date of birth later; the registered office address stays in clear.
- SEC.3: should TOTP secrets be encrypted too? They belong to django-otp and would need a custom device model.
- VEN.5: add a backlog row for the public vendor application form with account creation, or leave it to CORE.9?
- VEN.5: should missing DAC7 data block activation? Today it is listed but does not block, as the VEN.12 spec says.
- VEN.5: once a version is accepted, no new language text can be added to it; allow adding new languages to an accepted version?
- CMS.1: Wagtail's default Editors and Moderators groups are kept; confirm, or define editor roles in CORE.8.

Deferred to other tasks:

- TAX.1: re-checking a tax profile when the vendor's fiscal_regime changes goes to TAX.2 or VEN.11; the request and review workflow is TAX.2.
- TAX.1: the VIES client, its cache lifetime, timeout and fail-closed policy are TAX.5; seeding territories and rates is TAX.6 and TAX.7.
- TAX.1: staff-recorded VAT answers were removed; they need a task with a recorded_by actor and an audited service.
- CORE.13: rotating and revoking a vendor key do not check the actor; owner self-service belongs to CORE.9, and issuing from the admin (show the key once) to CORE.8.
- SEC.3: moving the key to a Podman secret and a key backup kept apart from pgBackRest belong to OPS.1 and OPS.3; step-up on the admin IBAN view is not planned yet.
- VEN.5: the vendor pages (application form with Turnstile, onboarding page) go to CORE.9; tax regime and Stripe Express checklist items to TAX.2 and VEN.6; re-acceptance of new versions to VEN.16; a rejection reason to CORE.8.
- CMS.1: wagtail-localize is I18N.3, hreflang and the EN fallback notice I18N.6; the Turnstile widget on /admin/login/ goes to CORE.8.
- CMS.1: Wagtail's inline panels, page choosers and the editor's accessibility checker need inline scripts or styles; CMS.2 should use StreamField and chooser blocks.

Review findings not fixed (all minor):

- TAX.1: legal notes can be moved to another profile, or edited on a closed profile, from the admin.
- TAX.1: the `Versioned` base is a copy of PlatformOperator's versioning code; it should move to apps/core.
- TAX.1: postcode ranges use a dash, which is ambiguous for Portuguese and Polish postcodes, and range bounds are not checked.
- TAX.1: oss_registered_since is not checked against the regime.
- TAX.1: the PR text calls the 4-decimal rate and the extra territory statuses a task requirement; they are implementation choices.
- CORE.13: deleting a vendor deletes its API keys without an audit entry for each key.
- CORE.13: the vendor-key scope question and the suspended-vendor question are not yet in the spec or the decision log.
- CORE.13: the tenancy harness skips any marker that is a substring of the request path.
- SEC.3: the associated data binds a value to its column but not to its row, so a value copied to another vendor's row decrypts.
- SEC.3: ordering by an encrypted field is not blocked, although the docs say it cannot be sorted.
- SEC.3: the migrate check decrypts one sample per key id, not every row.
- SEC.3: mask_tax_code shows the last 4 characters even of a short TIN.
- SEC.3: with both MPMV_FIELD_ENCRYPTION_KEYS and its _FILE variant set, the file wins without a warning.
- SEC.3: the encryption data migration imports live helpers and assumes an `id` primary key.
- VEN.5: two first contract versions saved at once fail with an IntegrityError instead of a clear error.
- VEN.5: submit_application does not reset vies_status, vies_checked_at and legacy_wp_user_id on the incoming vendor.
- VEN.5: no test covers an inactive owner accepting a contract.
- VEN.5: the vendor change page computes the current contract twice.
- CMS.1: the page factories patch ContentType lookups for `.build()`, which no test uses.
- CMS.1: the MFA middleware does not exempt Wagtail's sign-out for editors who have not verified yet.
- CMS.1: seeded menus of other languages are not Wagtail translations of the EN menu, which wagtail-localize (I18N.3) will need.
- CMS.1: WAGTAILADMIN_BASE_URL falls back to localhost in every environment instead of being required on live.
- CMS.1: locale sync after migrate always writes to the default database.
- CMS.1: editors can create extra HomePages in a locale that already has one.

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
| App dependency graph | import-linter 3/3 kept. The layers contract lists whole packages, so `apps.api.v1`, the accounts MFA modules, `apps.core.http`, `apps.core.i18n` and `apps.core.translations` are covered. Vendor owner MFA reaches `apps.vendors` through the `MFA_REQUIRED_POLICIES` dotted path, not an import (since #32 the policy is `is_vendor_member`, all members) |
| Check 2 findings | Finding 1 resolved: with an extra `("fr", ...)` entry in LANGUAGES, `makemigrations --check` reports no changes and both language fields offer the new code at runtime. Finding 2 still open (owner question 5 of check 2). Findings 3, 4 and 5 still open. Finding 6 did not recur |
| Security | MFA required for staff, superusers and vendor owners (all vendor members since #32), enforced by middleware (system check accounts.E002); step-up guards device removal and the admin MFA reset. Lockout per account and IP, trusted-proxy client IP in throttles, captcha, API auth, API mutation log and the core and vendor admin audit (not MFA audit, finding 1). CSP unchanged and strict; Turnstile sources only when the captcha is on; Swagger UI and the passkey script are served from our static files with the nonce |
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
   Since #32 the entry is `is_vendor_member` (all members); the same holds for it.
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
   recovery codes of 16 characters. Confirm. Resolved: confirmed by the owner and written into
   spec 02 (#32).
3. SEC.3: add TOTP secrets to the field-level encryption scope next to IBAN, KYC and DAC7?
4. Spec 02: which task takes the compromised-password check (a CORE.1 follow-up or a new CORE
   row)?

### Check 4 (2026-11-07, after SHP.1, CAT.1, CORE.3, SEC.4, VEN.12)

Scope: main at b4b7cd5. Owner decisions (#30: ADR-0007, ARC.13, ARC.15, CORE.13, OPS.1),
SHP.1, CAT.1, CORE.3, SEC.4, VEN.12, the wave 4 progress update (#31) and MFA for all vendor
members (#32). Code against spec chapters 01, 02, 03, 04, 06, 11 and 12 and ADRs 0005 and 0007;
spec against spec; coherence check 3 findings; repo hygiene.

| Area | Result |
|---|---|
| Done-when criteria | Met for SHP.1 (constraint tests incl. the weight band exclusion constraint), CAT.1 (migrations and constraint tests), CORE.3 (cross-tenant tests generated from a registry of every endpoint, API operation and vendor-owned model; no exemptions), SEC.4 (a band change is audited with before and after; the staff view shows a vendor change in e2e) and VEN.12 (FatturaPA party block checked field by field, `assert_payout_allowed` refuses incomplete vendors while `save_vendor` accepts an incomplete profile). The official XSD check of the party block belongs to CMP.2. CORE.4 after #32: a password-only vendor staff member is sent to enrolment |
| Spec 01 conventions | Engines import no ORM. Catalog prices go through `set_product_price` and `set_variant_price` with `dry_run`, `ChangeSet` and audit; vendor saves go through `save_vendor`. Shipping writes have no service (finding 1). Money is `MoneyField` everywhere (prices, band costs, pickup cost, share capital); weights and sizes are `Decimal`; no `FloatField` in any model |
| App dependency graph | import-linter 3/3 kept. Catalog, tax, shipping and cms are independent siblings above vendors, as in spec 01 §1.3. Shipping imports only `apps.core` and `apps.vendors`; the tax and shipping contract forbids orders, cart, payments and ledger |
| Tenancy | All 11 vendor-owned models (vendor, translations, members, products, variants and their translations, rates, bands, pickup options and their translations) use `VendorScopedManager` with a `vendor_scope` path that reaches Vendor; `NOT_VENDOR_OWNED` is empty. `for_actor` treats every active `is_staff` user as platform staff (CORE.3 question under the wave 4 follow-ups) |
| Audit | One `apps.core.audit.audit_context` for views and admins, with the trusted-proxy client IP; the API keeps its own context with the API client as actor, and anonymous login events build theirs in `accounts.services`. Check 3 finding 1 resolved |
| Factories | 27 factories for 27 models (TOTPDevice included); coverage and use guard tests pass |
| Check 3 findings | Finding 1 resolved (SEC.4). Finding 2 (spec 12 ID collision) still open: §12.3 still numbers requirements SEC.1 to SEC.10. Finding 7 now applies to `is_vendor_member`. Finding 9: `ApiClient.vendor` is now task CORE.13; the engine import contract and the API-key-shaped literal in `test_problems.py` are still open |
| Spec vs spec | `make docs-check` green. P1 1,074 h, P2 181 h and P3 18 h in the backlog match spec 91 and its go-live total (1,304 h subtotal, 1,410 to 1,570 h). D-06 is left open on purpose (empty `over_max_weight` means the platform default). D-19 is not decided by code: `on_request` exists as a value, as spec 03 sketches. ADR-0005 is marked superseded and spec 01, 12, 91, 92 and deploy/README name ADR-0007; leftovers in finding 3 |
| Anonymization | No real vendor names, legacy order numbers, legacy plan or carrier names or hostnames; only example domains in tests. gitleaks on the working tree reports one generic-api-key false positive in `backlog.csv` (CMP.7 row) |
| Migrations | One leaf per app; `makemigrations --check` clean |
| Gates | `make check` green; `make test` 1547 passed; `make test-e2e` 17 passed |

Findings and actions:

1. Major. Shipping rates are written by the admin with no service. `apps/shipping/admin.py`
   (`AuditedAdmin`) saves carriers, zones, rates, bands and pickup options directly and writes
   the audit rows itself, with no `dry_run` and no `ChangeSet`. This breaks the spec 01 rule for
   mutating services and rule 2 of docs/dev/audit-trail.md ("write audited models only through
   services"), and `core.E203` does not catch it because the admin overrides `save_model`. Rates
   are money. SHP.5 (import of the LIVE configuration with a parity report) and SHP.6 (editor)
   will need the same writes with dry-run. Action: a shipping `services.py` with audited,
   dry-run rate and band services, as an SHP.1 follow-up or inside SHP.5 (owner question 3).
2. Major. No backlog task depends on VEN.12, although its payout gate and party block are meant
   to be used by others. VEN.7 (payouts, "block if DAC7 incomplete") depends on VEN.4 and PAY.3
   only; CMP.1 ("DAC7 dataset on the vendor") repeats what VEN.12 now holds; CMP.2 (commission
   invoices) needs the FatturaPA party block. A payout flow built from the VEN.7 row alone could
   skip `assert_payout_allowed`. Action: add VEN.12 to the dependencies of VEN.7, CMP.1 and CMP.2
   and narrow CMP.1 to aggregates and export (owner question 2).
3. Minor. ADR-0005 leftovers. `deploy/server/mpmv.env.example` still says "Managed PostgreSQL 17
   in the same EU region (ADR-0005)"; the Containerfile, the Quadlet units, the release workflow,
   two deploy test docstrings and docs/dev/background-jobs.md cite ADR-0005 instead of ADR-0007;
   spec 01 lists "PostgreSQL 16+" while ADR-0007 and the local stack use 17. Action: fix with
   ARC.13, which touches these files.
4. Minor. `docs/spec/README.md` still gives the old go-live scope ("116 P1 micro-tasks,
   ≈ 1,350–1,510 h"); the backlog has 121 P1 tasks and spec 91 says 1,410 to 1,570 h. Action:
   update it with the next spec edit.
5. Minor. OPS.2 asks for alerts on database disk space, WAL archiving and backup age but depends
   on ARC.5 only; those exist only once the database VPS (ARC.13) and pgBackRest (OPS.1) do.
   Action: add ARC.13 or OPS.1 to its dependencies (owner question 2).
6. Minor. The CORE.4 row still says "mandatory for staff/owners" while spec 02 and the code now
   require MFA for every vendor member (#32). Action: reword the row with the next spec 02 edit.
7. Minor. `ShippingConfigVersion` stores a snapshot of every vendor's rates and is not vendor
   scoped. Correct for staff today; the SHP.6 editor must not show it to vendors.

Owner questions:

1. CAT.1, CORE.3, SEC.4 and VEN.12: the eleven wave 4 questions listed under "Follow-ups from
   wave 4".
2. May we add VEN.12 to the dependencies of VEN.7, CMP.1 and CMP.2, narrow CMP.1, and make OPS.2
   depend on ARC.13 (findings 2 and 5)?
3. Which task takes the shipping services with dry-run: an SHP.1 follow-up now, or SHP.5?

Resolved:

- Finding 1 in #38: `apps/shipping/services.py` (`save_config`, `delete_config`) with dry-run,
  ChangeSet and audit; the shipping admin calls them, and `core.E203` now also fails for an
  admin override that saves through Django's default with `super()` (owner question 3).
- Finding 2 in #39: VEN.7, CMP.1 and CMP.2 depend on VEN.12; CMP.1 reads the DAC7 dataset from
  VEN.12 and keeps the aggregates and the export (owner question 2, OPS.2 part still open).
- Finding 3 in #39: `mpmv.env.example` names self-managed PostgreSQL 17 on a dedicated VPS
  (ADR-0007) and spec 01 says PostgreSQL 17. The ADR-0005 citations in the Containerfile, Quadlet
  units, release workflow, deploy tests and docs/dev/background-jobs.md stay for ARC.13.
- Finding 4 in #39: the spec README gives 121 P1 tasks and 1,410 to 1,570 h, as spec 91.

### Docs check (2026-11-07, Markdown only, after #49 and #50)

Scope: main at 538cfe2. Every versioned Markdown file: relative links, task IDs, spec index,
language lists, WCAG target, hours and totals against the generated backlog (175 tasks, P1 146
tasks and 1,298 h, P2 199 h, P3 18 h, critical path 144 h), D-xx status, the no-invoicing
leftovers of #49, and redaction. No code was checked.

| Area | Result |
|---|---|
| Links and index | No broken relative link; every spec file is in the spec README index |
| Task IDs | Every referenced ID exists, except the CMS.7 history notes (intended) and the spec 12 §12.3 requirement IDs SEC.7 to SEC.10 (check 2 finding 2, still open); §12.3 now says its IDs are not task IDs |
| Totals | Spec 91 §91.2 (1,528 h subtotal, ≈ 1,660–1,850 h go-live), §91.4 (critical path 144 h), the §91.6 remaining work (116 tasks, 1,073 h) and the spec README (146 P1 tasks) match the backlog. The §91.6 forecast table still sizes the remaining code at ~900 h, from the 947 h measured before the theme and regulatory rows; refresh it at the next velocity measurement |
| Stale facts fixed | CORE.4 row (MFA for every vendor member, check 4 finding 6), CMS.1 done-when (every configured language), D-07 shown as a proposal, 21 decisions in the spec index, 02–14 in the approval checklist, background-jobs.md cites ADR-0007, SHP.1 lists #38, the #47 theme decision recorded above |
| No invoicing | Spec 02, spec 05 and field-encryption.md no longer speak of platform invoices; the ARC.7 and VEN.12 follow-ups note that the SDI intermediary and `EInvoiceChannel` are gone |
| Redaction | No vendor names, legacy hostnames, order numbers or credentials, and no reference template name. Spec 12 §12.1, its SEC.0 done-when and the spec README no longer name kinds of weakness on the current system |
| Owner questions | One new question on the existing IT texts at go-live against D-18 (migration, ING.5 and ING.7, SEO.3 feed) |
