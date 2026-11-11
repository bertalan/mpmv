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
| OPS.4 | done | #48, #59 | Weekly Dependabot update PRs for uv, GitHub Actions and Containerfile images (minor and patch grouped, majors and Django/Wagtail alone, cooldown), full CI on update PRs, CycloneDX SBOMs of the Python environment and the image in CI and attached to releases |
| ARC.8 | done | #51 | Feature flag registry with code, environment, staff and per-vendor values; kill switches for checkout, import and payments seeded on; `flag_enabled` helper; audited services with dry-run; admins with step-up on kill switches |
| CAT.2 | done | #53 | Category and term trees (treebeard), configurable taxonomies with typed term attributes, brands, per-language names, slugs and SEO texts; product links; admin CRUD with translations |
| SHP.12 | done | #54 | Several shipping methods per vendor and zone (VendorZoneRate renamed in place), vendor zones overriding platform zones, country groups, shared postcode grammar in apps/core/postcodes.py, per-method free-shipping threshold and stored VAT treatment |
| TAX.2 | done | #52 | Regime change workflow: owner request, staff approval or rejection with the review permission checked in the service, step-up, one transaction for the new profile version and fiscal regime, history. Tax code, flagged for careful human review |
| TAX.11 | done | #74 | Tax territory postcode patterns use the shared grammar in apps/core/postcodes.py; data migration rewrites old `a-b` ranges to `a..b`; `postcode_in_territory`. Tax code, flagged for careful human review |
| SHP.2 | done | #73 | Legacy zone cases 1-7 as regression tests, destination aliases, ISO-validated `Destination`, postcode patterns parsed once per zone snapshot |
| SHP.13 | done | #77 | Shipping configuration file format v1 (JSON and CSV), audited export/import with per-row report and all-or-nothing writes, `ShippingTemplate` with one standard template, `shipping_config` command |
| CORE.14 | done | #75 | Vendor API keys limited to vendor-grantable scopes, suspended vendors read-only and closed vendors refused, per-key audit when a vendor is deleted. Security code, flagged for careful human review |
| CAT.12 | done | #76 | Audited product and variant edit and delete services (fiscal category, sales mode, weight, dimensions), vendor and price rules enforced in the service, admin routed through them |
| CAT.14 | done | #80, #94, #116 | Catalogue integrity rules |
| CAT.3 | done | #84, #96, #116 | Typed attributes, translated values and variant axes |
| CAT.5 | done | #81, #116, #221 | SKU-free product slugs, SlugHistory and 301 redirects. #221 (D-25): the slug is the title without the `-{lang}` suffix, `catalog/0010` reslugs existing products on the historical models with audit, old slugs answer one 301 |
| CAT.6 | done | #174 | Redirect model, admin, `import_redirects`, resolved in the middleware |
| THM.1 | done | #88 | Theme registry, loader and per-site theme |
| ARC.18 | done | #106 | Engine purity contract and dry-run exception guard |
| TAX.5 | done | #92, #224 | VIES client with cache, timeout and fail-closed check. Tax code, flagged for careful human review. #224 (D-28): VIES consultation log with the vendor as requester, append-only (migration `tax/0008`), Madeira and Azores territories seeded (`tax/0009`). Tax code, needs human review |
| CAT.13 | done | #83 | Internal product identity; the vendor SKU is a label |
| CORE.12 | done | #102 | Per-API-key rate limit with a shared PostgreSQL counter |
| ORD.1 | done | #100 | Cart, guest and user carts, merge on sign-in |
| CMS.5 | partial: the recruitment page's link to the application waits for VEN.22 | #87 | Versioned legal pages per language and the Sell with us page |
| ARC.16 | done | #112 | Transactional email outbox, `EmailSender`, bounce suppression (ARC.19 split off) |
| VEN.18 | done | #107 | Fiscal regime RF20 (cross-border VAT franchise) |
| SHP.14 | done | #93 | Special areas and per-method treatments; seed gaps are follow-ups (check 6) |
| VEN.21 | done | #101 | Staff reopen a closed vendor to onboarding |
| CMP.13 | done | #91 | Vendor establishment gate on the allowed vendor countries |
| I18N.3 | done | #89 | wagtail-localize for CMS pages, images per locale |
| CMS.2 | done | #109, #117 | Hero, CTA, FAQ and rich text blocks; page, term and vendor choosers |
| EVT.12 | done | #82 | Geocoder chain and Find coordinates endpoint |
| CAT.8 | done | #103, #110, #118, #134 | Listing engine, views, templates and e2e; interim net price marked on cards until TAX.12 |
| THM.2 | done | #108 | Design-token pipeline |
| CMS.8 | done | #113 | Image source, licence and credit |
| THM.3 | done | #114, #119 | Base layouts, skip link, header, footer, page shells, error pages |
| THM.4 | done | #121, #122, #123, #135 | Form, button, card, badge, alert and navigation components, styleguide page |
| EVT.1 | done | #125, #127 | Event page models, constraints, tenancy, admin panels and templates |
| CORE.16 | done | #136 | API rate limit defaults and per-key override |
| EVT.2 | done | #130 | Recurrence engine |
| EVT.3 | done | #147, #149, #205 | Event services, Event review workflow, moderation UI (hide, restore, cancel, policies). #205 security fixes: platform staff exempt from the vendor scope check, recheck on submit, depth cap, no userinfo in CTA URLs. Event code, needs human review |
| ARC.13 | partial: the done-when is proven on STAGE (lane-a) | #146, #148, #152 | Multi-arch release image, database env file and root CA mount, site-only Caddy, media volume |
| THM.5 | done | #128 | Theme quality gates |
| VEN.25 | partial: the EX number is not in the FatturaPA party data yet (tax advisor) | #139 | RF20 only for EU-established vendors with an EX number |
| TAX.13 | done | #138 | Exemption reasons tied to their law's country. Tax code, flagged for careful human review |
| VEN.23 | done | #137 | DAC7 seller dataset |
| ARC.20 | done | #132 | Trusted input for AI agents |
| ARC.10 | done | #150 | Synthetic data seed (`make seed`) |
| CMS.10 | done | #126 | Product grid block |
| CORE.15 | done | #140, #163 | Secure action links, review follow-ups |
| ARC.23 | done | #153 | Release gate with production parity |
| EVT.13 | done | #144 | Map picker for event locations |
| I18N.9 | done | #142 | Customer legal and checkout pages per language |
| I18N.11 | done | #220, #239 | Language switch stays on the same object, from the one `{language: URL}` mapping shared with hreflang (`apps/core/i18n.py`); untranslated object goes to the target home with a notice once; pages with secret-bearing URLs opt out |
| EVT.5 | done | #180 | Vendor events API `/api/v1/events` (list, get, create, update, submit, cancel), scopes `events:read` and `events:write`, vendor-scoped keys, dry run, RFC 9457 errors. Event code, needs human review |
| ORD.2 | done | #214 | `Order`, `VendorOrder`, `OrderLine` with price, tax and commission snapshots, per-vendor numbering with dry-run preview, order and vendor-order state machines in `apps/orders/engine.py` enforced by model validation and database triggers (illegal transition cannot be saved), fiscal snapshot immutability (D-29), tax retention and privacy lifecycle with scrub gates (D-30), `paid_at`, legacy id for MIG.4. Migrations `orders/0001` to `0010`. `OrderEvent` and `ReturnRequest` are not in this row. Order and money code, needs human review |
| SEO.8 | partial: 1 of 2 (environment floor); the owner toggle is open | #240 | 1/2 `MPMV_FORCE_NOINDEX` (true in STAGE settings, implied by demo mode): robots meta, `X-Robots-Tag` header (`ForceNoindexMiddleware`) and `/robots.txt` all noindex, listings keep their rules when not forced. Security-relevant, needs human review |
| I18N.12 | done | #237 | A signed-in user's `preferred_language` applies on unprefixed areas (Manager, account, admins) through a middleware after `AuthenticationMiddleware`; prefixed public URLs ignore it. Manager strings stay English until I18N.7 |
| CMP.7 | partial: automated audit in CI; manual review and statement open | #217 | axe (wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa) on the key public pages in the e2e suite (`tests/e2e/test_axe_audit.py`), any violation fails, report uploaded as the `e2e-axe` artifact. Cart, checkout and product detail are not yet in `KEY_PAGES` |
| VEN.29 | done | #238 | Storefront description and the three policies are Wagtail rich text (D-34): sanitised on save and on render, embeds of another vendor's media refused and hidden, legacy plain text rendered as paragraphs, idempotent data migration (`vendors/0026`), editors scoped to the Manager form, e2e screenshot. Security (rich text), needs human review |
| TAX.3 | done | #218 | `resolve_goods_tax` pure function in `apps/tax/engine.py` with the T1 to T15 goods case table (`apps/tax/tests/unit/test_goods_tax.py`; T4 and T10 to T13 wait for TAX.4, T16 to T18 belong to their own rows), exemption-nature codes (D-26, D-28), T15 through `is_outside_vat_territory`; migration `tax/0007`. Tax code, needs human review |
| CMP.5 | partial: 3 of 4 (contacts, terms and ranking pages; notice and action; statement of reasons); 30-day termination notice open | #222, #230, #233 | 1/4 DSA points of contact, platform terms and ranking pages (migration `core/0019`). 2/4 notice and action (art. 16): `Notice` with a tracked outcome and a reviewer workflow, good-faith statement, captcha, declared in the public forms register (migration `compliance/0003`). Compliance code, needs human review. 3/4 DSA art. 17 statement of reasons (`StatementOfReasons`, acting on content through the owning app services), art. 18 notification, art. 24(5) transparency submission stored pending (migration `compliance/0004`). Compliance code, needs human review |
| CORE.25 | done | #234 | Two session levels (D-32): `restricted` default, operational mode from a fresh passkey, per-action "do it once" grants stored in the database and spent once (`accounts/0006`, `0007`), group 3 actions need their own fresh user-verified passkey, timers and action lists in `SessionPolicy` (`core/0020`), start, end and prompts audited, per-user per-day report. Security code, needs human review |
| SEC.14 | done | #251 | Rate limit (per IP and per restriction) and captcha on Wagtail page view restriction forms; the interim block is lifted. Declared in the public forms register. Security code, needs human review |
| OPS.2 | done | #250 | JSON logs, error tracker interface (`MPMV_ERROR_TRACKER`: none or log), `/healthz` and `/readyz`, alert hooks (disk space, WAL archiving, backup age). New `MPMV_OPS_*` variables in the server env example |
| ORD.4 | done | #247 | Transactional `place_order` with `StockReservation` (migration `orders/0011`) and price verification, concurrency test on stock 1. Order code, needs human review |
| VEN.31 | partial: 1 of 2 (images); documents open | #248, #244 | Manager image chooser and upload into the vendor's own collection `vendor-<id>` (`apps/manager/media.py`, section `shop.media`, owner only): crafted collection or foreign image id answers 404 and is audited (`vendor.media_denied`), `upload_storefront_image` with SEC.2 checks, own-work licence, hourly limit, `vendor.media_uploaded` audit; `link` feature dropped from the editor and `linktype="page"` refused on save. Spec row added by #244 (spec 04). Security code (media, tenancy), needs human review |
| CORE.18 | partial: 1 of 2 (roles and matrix); staff UI and assignment open | #253 | `apps/manager/roles.py` (five roles, incompatible pairs, 17-section matrix, role membership never `has_perm`), unmanaged `PlatformAccess` permissions (`manager/0001`), idempotent `sync_role_groups` data migration (`manager/0002`), system checks `manager.E003` (static) and `manager.E004` (group drift; #269: silent until `manager/0002` is applied, so it cannot block the migration that seeds the groups; #274: the themes check also tolerates pending migrations and CI has an "Upgrade path" step), generated `docs/dev/platform-roles.md`. Part 2: Staff and roles UI, assignment service, step-up, audit, one-person team. Permissions code, needs human review |
| ORD.6 | done | #249 | Vendor fulfilment from the Manager shop space: orders list and detail, accept, ship with tracking, delivered (`fulfil_vendor_order`, engine rules, tracking validation, audit row per step), tenant isolation tests, e2e screenshots. No model change. Customer notification on shipped is not in the row. Order code, needs human review |
| SHP.3 | done | #254 | `quote_shipping` pure engine and service with Rates, IndicativeRates, NeedsQuote and Blocked outcomes, over-band policy, pickup, free-shipping threshold per method, special areas; cases 8 to 13, 15, 16, 19. No migration. `WeightBand.surcharge_net` is not yet defined in the spec |
| TAX.4 | done | #255 | `resolve_shipping_tax` with proportional split by goods net (ROUND_HALF_UP, remainder on the largest share), `TaxPortion`, fail-closed `UnsupportedShippingTreatment` for non-default treatments; T8 to T13 plus T1, T7, T14. D-03 stays open for the advisor (default: shipping follows the goods). Tax code, needs human review |
| CMP.4 | partial: 1 of 2 (customer export and anonymisation); closed vendor owner, sub-processor register and retention job open | #256 | `apps/compliance/gdpr.py`: `export_customer_data` (articles 15 and 20, JSON) and `anonymise_customer` (D-29, D-30) with dry run, idempotent, audit rows without personal data; concluded orders go to `restricted_tax_only` through ORD.2, open orders keep the request pending (`ErasureRequest`, `compliance/0005`), queued mail cancelled, sessions and staff rights purged. Audit actor label by public id; lockout audit key as a keyed hash; `core/0021` rewrites old customer actor labels and lockout keys (one-off, the documented append-only exception). Services only, no UI or command yet. Compliance code, needs human review |
| OPS.5 | done (STAGE verification at deploy) | #252 | Technical Django admin loopback only (Q-18, MQ-24): Caddy `@admin_denied` matcher plus `AdminNetworkAllowlistMiddleware` (fail closed, the normal 404, no slash redirect), `MPMV_ADMIN_ALLOWED_NETWORKS` in both env examples, system checks `core.E003` and `core.W002`, SSH tunnel procedure in `docs/dev/admin-access.md`. Security code, needs human review |
| ORD.9 | partial: 1 of 2 (emails); the PDF per-vendor non-fiscal confirmation is open | #257 | `apps/orders/notifications.py`: order received, payment confirmed, one non-fiscal confirmation per vendor, vendor new order, vendor order shipped. Idempotent per event (outbox key), queued in the caller's transaction (rollback or dry run sends nothing), nothing for non-`active` orders (D-30), language from the order snapshot with EN fallback. Hooks in `transition_order(... "paid")` and `transition_vendor_order(... "shipped")`; `place_order` calls `notify_order_placed`. Only the non-fiscal label is translated in all five languages; the de, fr and es body text is English (I18N.7 slices). Orders code with personal data, needs human review |
| ING.1 | done | #258 | `apps/ingest` (`ingest/0001`): `SupplierSource`, `SourceItem`, `SourceObservation`, `ProductDraft`, `ImportRun`, `ReviewItem`, `MappingTable`; pure `engine.py` (run status machine, SKU match exact, alias, ambiguous, new; mapping precedence; payload hash); audited services with `dry_run`; staff-only admin; import contract "ingest depends only on catalog, vendors, accounts and core"; tenancy registry. Adapters, jobs and publish are ING.2 and later |
| CAT.20 | partial: 1 of 3 (data model); services, merge, redirect, audit (2/3) and JSON-LD, logo checks, brand page (3/3) open | #259 | `Manufacturer`; `Brand` gains `status`, `wikidata_id` (format only, unique when set), `url`, `same_as`, `logo` and alt, optional `manufacturer`, `name_key` (catalog `0011_brand_entities`, data kept, rerun a no-op); pure rules in `catalog/engine.py`; `brand_anchor` serves approved brands only; staff admin. `Manufacturer` is classified unaudited until 2/3 |
| SEC.13 | done | #236 | Anonymous POST form sweep: a system check fails for an undeclared anonymous POST endpoint, `@public_form(...)` declarations and the generated public forms register (`tools/build_public_forms.py`). Security code, needs human review |
| I18N.7 | partial: 1 of n (Manager and account strings in Italian) | #245 | Italian catalog for the Manager and account strings through `tools/i18n_catalog.py` (extract, compile, check; `make check` runs the check) |
| CAT.19 | done | #154 | Vendor products in the Manager shop space |
| VEN.24 | done | #141 | Claim an imported vendor (legal email or registered PEC) |
| SEC.5 | partial: items 6, 7, 9 and 10 of the security wave open | #162, #169, #166, #171 | Security headers by response type, endpoint sweep, hostile-input corpus, rate-limit tests |
| CAT.9 | done | #167 | Product search: full-text per language, trigram, SKU prefix |
| CORE.20 | done | #171 | Per-IP limit on refused API keys |
| CMS.3 | done | #156 | Blog: index and post pages, scheduling, translatable authors with author pages, ProfilePage/Person and BlogPosting JSON-LD, choosers, `import_article` |
| CMS.4 | done | #168 | Term and vendor landing pages |
| THM.12 | done | #151, #211 | WebP and AVIF renditions, sanitised SVG logos. #211 de-flaked the SVG sanitiser timing test (load-robust ratio) |
| CMP.3 | partial: Safety Gate checks, repeat-offender suspension and recall procedure not built | #175 | Done-when met (spec 11, row CMP.3): GPSR data (`GpsrInfo`, vendor defaults, per-language safety texts), publication gate in `apps.catalog.publishing`, write guards on published products, nightly check, `SafetyCase` with working-day deadlines and overdue staff alert, per-country warnings languages (owner decisions 2026-11-09). Not built, though the spec row names them: recall procedure, Safety Gate checks, repeat-offender suspension (CMP.26 covers only checkout and sale blockers). Compliance code, needs human review |
| SEC.2 | done | #164 | Secure uploads (spec 12 task row; not the "Tenant isolation" requirement of the same ID): content-checked, re-encoded uploads with app-made names on every upload path (`apps/core/uploads.py`); caps on bytes, pixels, side and animation frames; originals over 40 megapixels downscaled; bounded decode memory and one decode per process; MPO and ICC handled; malicious-upload corpus. The final caps are in code only, not yet in spec 12. Security code, needs human review |
| CMS.9 | done | #129 | Launch countdown block: pure `countdown_state`, server-rendered absolute date and expired state, page cache capped at the target, CSP-safe ES module with visitor time zone, minute updates, pause button, single polite announcement |
| CMP.9 | done | #177 | WCAG 2.2 SC 3.3.8 on the authentication templates: paste allowed, `autocomplete` tokens, passkeys offered, Turnstile without a puzzle |
| CORE.17 | done | #170, #212 | Manager shell (`apps/manager/`): two gated spaces by URL prefix, deny-by-default gate with 404s, navigation registry, vendor picker and switcher, base list view with bulk actions (dry-run preview), closed vendors refused in the shop space, sign-in page meeting CMP.9, URL sweep test. Staff access to the shop space through impersonation is CORE.10. #212 fixes: the language switcher posts through a real CSRF check, `/manager/` redirects to the user's home, a signed-in user without the space's right gets an explanatory 403 page (anonymous still goes to the login), `bootstrap_platform_owner` (CORE.23). Access code, needs human review |
| CORE.8 | done | #185, #188, #189, #209 | 1/3 platform staff is the model permission `vendors.view_all_vendors`, not `is_staff` (migration vendors 0021, no data grant; release note in release.md). 2/3 account unlock service with audit (`accounts.user.unlocked`, `accounts.user.unlocked_by_reset`) and a completed password reset clears the lockout. 3/3 own editor roles per page-tree part replace Wagtail's default Editors and Moderators groups (migration cms 0013, edit-only editor roles, empty publisher roles, nobody approves their own submission), Wagtail admin menu limited to CMS entries (`docs/dev/editor-roles.md`). #209 makes the seeding safe during partial migrations (main was red). Permissions code, needs human review |
| SEC.12 | done | #199 | Reverse proxy header ownership: Caddy drops `Server`, media carry the transport and framing headers, CORP on the HTTPS 301; the three strict xfails are plain tests; documented in security-headers.md |
| OPS.9 | partial: triage decided and rules file merged, no STAGE scan run on the new main yet | #197 | STAGE scan triage (SEC.5): no wildcard CORS, CORP header, ZAP rules file with documented accepts |
| TAX.7 | done | #179 | Special territories by postcode (initial dataset, T15 green): most specific pattern wins (FR 97150 is Saint-Martin, not Guadeloupe), alias countries (MF, BL, AX) fail closed, seed migration tax 0006 never overwrites staff rows, Northern Ireland not seeded (D-02). Tax code, needs human review |
| CMP.25 | done | #181 | Activation gate for platform duties: `activation` purpose in `LegalFieldRequirement` seeded with the DSA article 30 trader data, append-only `SelfCertification` (version, language, text hash, no IP), checked by the VEN.5 activation next to CMP.13; DAC7 data still blocks payouts only (migrations vendors 0022, 0023). Compliance code, needs human review |
| VEN.10 | done | #182 | Public vendor storefront per language: bio, policies, products and store categories, category sidebar correct in IT |
| THM.6 | done | #184 | First vertical theme `ride`: operator palette tokens (light and dark), type scale and shapes, self-hosted Barlow and Barlow Condensed (OFL 1.1), Lucide icon subset (ISC), header, hero and home blocks, placeholder art, `LICENSES.toml` complete |
| VEN.22 | done | #202, #229 | Public "Sell with us" application at `/vendor/apply/`: account and applicant vendor in one form with no password (the person who opens the emailed link chooses it), Turnstile through the CORE.5 verifier, confirmation mail plus one reminder at 48 h each with a cancel link, GET never changes state and a second POST is idempotent, unconfirmed applications deleted after 3 days (D-24, hourly jobs), MFA at first login (CORE.4); migration vendors 0024. Account and compliance code, needs human review. #229: the attacker-chosen test password is generated, no literal |
| CMS.12 | done | #201 | Server-side rich-text sanitiser: `engine.sanitise_rich_text` on the editor's own allow-list, applied by a `pre_save` receiver to every rich-text field and StreamField block, so any write path is covered; the editor opens stored HTML it would not write; the F1 and F2 strict xfails are plain tests. Security code, needs human review |
| ARC.17 | done | #198 | `PlatformOperator.einvoice_channel` and its choices removed (migration core 0018, admin, seed, factory, spec 01); a guard test; no comment says the platform writes FatturaPA XML |
| CORE.9 | partial: 2 of 3 (dashboard and owner-only profile, logo included); platform review and revert open | #200, #204, #208 | 1/3 shop dashboard with product KPIs on a queryset scoped by `for_actor` and the selected vendor. 2/3 `save_storefront_profile`: owners only, plain text with length caps, rate limit, post-moderation email to the operator support address, `vendor.profile_changed` audit with before and after, form at `/manager/shop/profile/` that vendor staff cannot reach. #208 (2b): owner-only vendor logo upload through `MediaImage` validation into a per-vendor collection, replaced image deleted, audited; migration `vendors/0025`. Security (file upload), needs human review |
| CORE.23 | done | #212 | `manage.py bootstrap_platform_owner EMAIL [--create] [--dry-run]`: idempotent, ChangeSet, audit `core.platform_owner.grant`, step in release.md |
| OPS.10 | partial: the workflow has not run yet (manual run after merge) | #213 | Nightly line-only coverage workflow (`coverage-nightly.yml`), report only, `coverage-report` artifact and job summary table |

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

Review findings not fixed (all minor): the 24 items are consolidated, with those of wave 6, in the
cleanup table of [coherence check 5](#check-5-2026-11-07-after-waves-5-and-6-and-the-spec-prs-47-to-61).

## Follow-ups from wave 6

Wave 6 is merged: OPS.4 (#48), ARC.8 (#51), CAT.2 (#53), SHP.12 (#54), TAX.2 (#52).

Integration notes:

- Merge order: OPS.4 (#48), ARC.8 (#51), CAT.2 (#53), SHP.12 (#54), TAX.2 (#52).
- CMP.17 now depends on SHP.12 and SHP.14, which exist on main since #54.
- Dependabot refused the merged `.github/dependabot.yml`: `semver-major-days` is not supported for
  the github-actions and docker ecosystems. #59 keeps it on uv only, with a test.
- Majors from review, all fixed before merge and checked against the diff: ARC.8 (an ordinary
  vendor override could be turned into a kill-switch override without step-up; vendor and flag are
  now read-only on change), TAX.2 (the review permission is now checked in the service, not only
  in the admin), CAT.2 (saving a root term moved it to the front of the whole forest; placement is
  now read and set within its taxonomy).
- TAX.2 merged after SHP.12; `tests/tenancy_registry.py` combines the tax request, the vendor
  feature flag and the shipping method entries.
- Tests on the final main (`MPMV_TEST_DB_NAME=test_mpmv_wave6_integration`): `make check` green,
  `make test` 2605 passed (unit and integration, e2e deselected), coverage 98%.

Open questions for the owner (Q-77 to Q-83 in the private register):

- TAX.2: a request that changes the declared fiscal regime is approved only on or after its date,
  and a past effective date backdates the new profile. Choose the rule.
- TAX.2: should an approval need two staff members (four eyes)?
- CAT.2: brands are platform-owned (spec 03); the legacy system had brands per vendor.
- SHP.12: may staff set a non-default shipping VAT treatment before D-03 is decided?
- SHP.12: confirm the spec additions made in the implementation PR (SHP.13, SHP.14, the SHP.3
  `IndicativeRates` outcome, regression case 19).
- OPS.4: add backlog rows for the update gaps (pre-commit hooks, the Caddy and postgres images, an
  image vulnerability scan and SBOM attestation)?
- TAX.1/SHP.12: move tax territories to the shared postcode grammar in TAX.7 or in a new small row?

Owner actions:

- OPS.4: turn on the dependency graph, Dependabot alerts and security updates (Settings > Advanced
  Security), and create the labels `dependencies`, `python`, `github-actions` and `docker`.

Deferred to other tasks:

- SHP.12: tax territories (TAX.1) still use the old `a-b` postcode range syntax. Proposal: switch
  `Territory.postcode_patterns` to `apps/core/postcodes.py` in TAX.7 or a new small row, so tax
  territories and SHP.14 special areas share one grammar.
- SHP.12: match_postcode and resolve_zone are in SHP.12; SHP.2 still owns legacy cases 1-7.
- SHP.12: the database allows at most one default method per vendor and zone; "exactly one" goes
  to the SHP.8 health check and SHP.3, which falls back to the first method.
- SHP.12: old audit entries keep the label shipping.vendorzonerate, new ones
  shipping.shippingmethod; the public ids are unchanged.
- SHP.12: D-03 (VAT on shipping) and D-06 (weight above the last band) stay open; only the fields
  that will hold their outcome exist.
- ARC.8: nothing calls the kill switches yet; ORD.10, PAY.* and ING.* must call
  `flag_enabled(flags.CHECKOUT|IMPORT|PAYMENTS, vendor=...)`, and ORD.10 owns the STAGE check.
- ARC.8: other processes can see a stale flag for up to FEATURE_FLAGS_CACHE_SECONDS (5 s); ORD.10
  sets it to 0 if a switch must act at once.
- ARC.8: the core flag admin page does not link to the vendor overrides (core cannot import the
  vendors admin); CORE.8 can add it in the Wagtail admin.
- CAT.2: no URL routing for taxonomy, term, brand and category pages while D-07 is open; reserved
  first URL segments are checked with the routing in CAT.8.
- CAT.2: no mutating taxonomy services (none of these changes is sensitive); product category,
  brand and terms from imports or the API are CAT.10, redirects after a move are CAT.5 and CAT.6.
- CAT.2: sibling slug uniqueness and term placement are checked in clean() and the admin form, not
  by the database or `move()`.
- CAT.2: changing a taxonomy's term_attributes does not re-check existing terms.
- CAT.2: Category.google_category_id, legacy term ids and brand logos are not added (spec 10,
  MIG.*, CAT.4).
- TAX.2: the vendor request page, its step-up, the email notifications and withdrawing a pending
  request go to VEN.11 (with the CORE.9 vendor area).
- TAX.2: the profile inconsistency warning shows only in the tax admin and the nightly job log;
  the vendor admin cannot show it without importing tax.
- OPS.4: the Caddy Quadlet image, postgres:17 and the pre-commit hooks are bumped by hand.

Review findings not fixed (all minor): the 26 items are consolidated, with those of wave 5, in the
cleanup table of [coherence check 5](#check-5-2026-11-07-after-waves-5-and-6-and-the-spec-prs-47-to-61).

## Cleanup wave (2026-11-07)

The five cleanup batches of check 5 are merged, in this order: E (#65, OPS.4), A (#66, TAX.1 and
TAX.2), B (#69, SEC.3, VEN.5, ARC.8, CORE.13), C (#70, ARC.8 and CMS.1), D (#67, CAT.2 and
SHP.12). 39 of the 41 consolidated items are fixed; items 7 and 22 were skipped (reasons under the
[consolidated table](#minor-findings-consolidated)).

- Behaviour changes worth a human look: tax legal notes are read-only once their profile period
  has ended, and regime changes are approved on the business-time date (#66); ordering by an
  encrypted field raises, both a secret variable and its `_FILE` form refuse to start, and
  contract versions are numbered under a table lock (#69); the MFA middleware exempts Wagtail's
  sign-out, and the deploy check `cms.E001` requires a real `WAGTAILADMIN_BASE_URL` (#70); a vendor
  with shipping methods or pickup options can no longer be deleted before `delete_config` removes
  them (#67); the release workflow attaches SBOMs from a separate job with `contents: write` (#65).
- New tool: `manage.py verify_encrypted_data` checks every encrypted value (#69).
- Follow-up: CAT.8's done-when should say that taxonomy descriptions are sanitized with an
  allow-list before rendering (#67, item 32); record it at the next spec 03 edit.
- New owner questions in the private register: Q-92 (is the OSS registration date required for
  the OSS regime?) and Q-93 (bind encrypted values to their row, item 7).
- Tests on the final main (`MPMV_TEST_DB_NAME=test_mpmv_cleanup_integration`): `make check` green,
  `make test` 2714 passed (unit and integration, e2e deselected), coverage 98%.

## Follow-ups from wave 7

Wave 7 is merged, in this order: TAX.11 (#74), SHP.2 (#73), SHP.13 (#77), CORE.14 (#75), CAT.12
(#76).

Integration notes:

- Each branch was updated onto main and merged after its six required checks passed; no
  conflicts. SHP.13 adds shipping migration 0007; SHP.2 and TAX.11 add no shipping migration.
- Majors from review, fixed before merge and checked against the diff: SHP.13 (a method without
  a band list in the file deleted its stored bands, partial rows were validated without their
  stored values, and bands in the wrong order could overlap while being written; each fix has a
  test in `test_config_io.py` or `test_config_format.py`).
- Tests on the final main (`MPMV_TEST_DB_NAME=test_mpmv_wave7_integration`): `make check` green,
  `make test` 2929 passed (unit and integration, e2e deselected), coverage 98%.

Open questions for the owner (Q-94 to Q-96 in the private register):

- CORE.14: is the initial list of vendor-grantable API scopes right?
- SHP.13: protect CSV exports against formula injection (cells starting with `=`, `+`, `-`, `@`)?
- CORE.14: refuse issuing an API key to a closed vendor?

Deferred to other tasks:

- SHP.13: D-03 and D-06 stay open; `tax_treatment` and `over_max_weight` are exported and imported
  as stored data only.
- SHP.13: SHP.14 special areas have only the reserved `special_areas` section; a non-empty list is
  refused in format v1.
- SHP.13: translated names (method and group names, pickup labels) are not in format v1; they stay
  with the translation workflow.
- SHP.13: no admin or vendor UI for templates or file upload; staff use the `shipping_config`
  command until the SHP.6 editor. The standard template's zones and prices come from staff;
  nothing is seeded.
- SHP.13: import is merge-only (rows missing from the file are kept); a replace mode can come with
  SHP.5 if needed.
- SHP.2: no data migration rewrites a spaced dash range stored as an exact postcode
  (`70000 - 74999`); such a row now fails when its zone snapshot is built. No LIVE data exists yet.
- SHP.2: `Destination` raises `InvalidCountry` for codes outside ISO alpha-2; the SHP.3 and SHP.4
  checkout callers must pass a validated country.
- TAX.11: TAX.7 decides what a territory postcode match means for the tax decision; this wave adds
  only the matching function.
- TAX.11: ranges with letters in the old form (`AB1-AB9`) are not rewritten and become exact
  postcodes; no territories are seeded yet.
- CORE.14: scopes are checked only when a key is issued; a vendor key that already holds a
  platform-only scope is not refused at request time (see the minor findings below).
- CORE.14: a suspended vendor's key is refused on dry-runs too, since a dry-run is a POST to a
  write operation.
- CORE.14: applicant and onboarding vendors keep full key access; the spec restricts only
  suspended and closed vendors.
- CAT.12: the database-level vendor guarantee and `duplicate_product_to_vendor` stay with CAT.14;
  the composite foreign key from catalog migration 0001 is unchanged.
- CAT.12: kind, stock, GTIN and the classification foreign keys go through `save_product` but are
  not audited; adding a field to `PRODUCT_EDIT_FIELDS` audits it.

Review findings not fixed (all minor, 20):

| Task | File | Finding and proposed fix |
|---|---|---|
| SHP.13 | `apps/shipping/config_format.py` | JSON floats for amounts and weights get a confusing "2 decimal places" error; refuse floats in `_add_row` with "write amounts and weights as decimal strings", with a unit test |
| SHP.13 | `apps/shipping/config_format.py` | CSV export does not escape free text starting with `=`, `+`, `-`, `@` (formula injection); decide before a web download ships (Q-95) |
| SHP.13 | `apps/shipping/config_format.py` | `_put_cell` turns an empty CSV number cell into None, so an empty `priority` or `surcharge_net` fails while JSON takes the model default; leave such cells out, or fix the docstring and spec |
| SHP.2 | `apps/shipping/engine.py` | A stored spaced range (`70000 - 74999`) now raises in `ZoneSnap` and breaks zone resolution for every vendor on that zone; add a data migration or a spec 06 §6.2 note, and name the zone in the error |
| SHP.2 | `apps/shipping/engine.py` | `Destination` raises on non-ISO codes such as XK; state in spec 06 §6.2 that checkout validates the country first and SHP.3 maps `InvalidCountry` to NeedsQuote or Blocked |
| SHP.2 | `apps/shipping/tests/unit/test_legacy_cases.py` | The parse-once test monkeypatches our own `parse_postcode_pattern`; assert on `zone.parsed_patterns` identity instead, or comment that it is a spy |
| TAX.11 | `apps/tax/migrations/0003_territory_postcode_ranges.py` | Inverted ranges (`22199-22100`) become `22199..22100`, which the grammar refuses at match time; swap or skip them, with a unit case, and tell TAX.7 |
| TAX.11 | `apps/tax/engine.py` | `postcode_in_territory` lets `InvalidPostcodePattern` escape for one bad stored pattern; TAX.7 decides between skip-and-log and validation only |
| TAX.11 | `apps/tax/migrations/0003_territory_postcode_ranges.py` | Old-form ranges that are not rewritten (`1-22`, `AB1-AB9`) silently become exact postcodes; log them when the migration runs |
| CORE.14 | `apps/vendors/admin.py` | The admin refuses to delete a vendor with API keys (`ApiClientAdmin.has_delete_permission` is False); override `VendorAdmin.get_deleted_objects`, with an admin integration test |
| CORE.14 | `apps/api/tests/integration/test_vendor_key_policy.py` | The implementation commit edited a test (type narrowing); keep test edits in the tests-first commit |
| CORE.14 | `apps/api/engine.py` | Vendor-grantable scopes are not enforced at request time or on rotation; add a backlog row so `evaluate()` denies them and move the CORE.13 test endpoint to a vendor scope |
| CORE.14 | `apps/vendors/services.py` | `VENDOR_DELETION_AUDIT_HOOKS` are imported only at deletion time; add a system check or test that imports each hook and checks it is callable |
| CORE.14 | `apps/api/services.py` | A key can be issued for a closed vendor and is audited though it never works; refuse it after the owner confirms (Q-96) |
| CAT.12 | `apps/catalog/admin.py` | `delete_queryset` deletes product by product without one transaction; wrap the loop in `transaction.atomic()` |
| CAT.12 | `apps/catalog/services.py` | `delete_product` locks variants with `select_related` but no `of=("self",)`, so it also locks the product and vendor rows; add `of=("self",)` |
| CAT.12 | `apps/catalog/services.py` | `save_product` and `save_variant` duplicate the `save_audited` flow; add a guard hook to `save_audited` or document the copy |
| CAT.12 | `apps/catalog/admin.py` | A `ValidationError` from the services in `save_model`/`save_formset` gives a 500; move the checks to the ModelForm `clean` or document it |
| CAT.12 | `apps/catalog/services.py` | Many-to-many changes are audited only after `save_m2m()`, with no dry-run service; track with CAT.14 |
| CAT.12 | `apps/catalog/tests/integration/test_product_edits.py` | Two tests were added in the implementation commit; keep new tests in a tests-only commit first |

## Follow-ups from wave 8 (lane merge queue)

Merged, in this order, each after its six required checks and with main green before the next:
#85 (parallel lanes protocol), #79 (owner answers Q-58 and Q-42), #80 (CAT.14), #84 (CAT.3),
#81 (CAT.5), #95 (D-07 decided), #97 (TAX.11 review fixes), #94 (CAT.14 review fixes), #96
(CAT.3 review fixes) and #88 (THM.1); then #105 (build_docs newline fix), #106 (ARC.18), #92
(TAX.5), #99 (CORE.14 fixes), #83 (CAT.13), #104 (TAX.2 review controls), #102 (CORE.12), #100
(ORD.1), #87 (CMS.5), #112 (ARC.16), #115 (SHP.2 and SHP.13 zone patterns), #103 (CAT.8 1/3) and
#116 (CAT.14, CAT.5 and CAT.3 audit majors).

Integration notes:

- The ARC.18 dry-run guard (#106) failed the branches that were already open. #92 needed its two
  VIES checks (`verify_vat_number`, `recheck_vat_number`) listed as telemetry exceptions (spec 01
  §1.3 and the guard); #100 needed `dry_run` on `get_or_create_cart`. ARC.18 also made the
  engines Django-free, so `VendorCannotWrite` (#99) no longer extends `PermissionDenied`.
- Merge conflicts were resolved by keeping both sides: #99 (engine imports), #102 (rate limit
  and vendor key policy in `apps/api`), #100 (tenancy registry and audit coverage lists).
- Not merged: #91 (CMP.13) has a major in its cross-review (`is_platform_owner()` was true for any
  superuser; fixed on the branch, awaiting lane-b's re-check), #93 (SHP.14, design fix pending),
  #101 (VEN.21, lane-b fixing a major) and #107 (VEN.18, fix pending).

Follow-ups:

| Task | Where | Follow-up |
|---|---|---|
| CMP.13 | `docs/spec/11-compliance.md` | An active vendor that moves its seat outside the allowed countries or loses its EU establishment stays active with no staff flag (spec 11). Add a backlog row for the re-check (cross-review major, tracked here) |
| CMP.13 | `apps/vendors/services.py` | `suspended -> active` is not gated; a vendor outside a shrunk set can be reinstated without the establishment check |
| CMP.13 | `apps/core/services.py` | `allowed_vendor_countries()` falls back to the EU-27 with no PlatformOperator; owner to confirm |
| CMP.13 | `apps/core/tests/integration/test_allowed_vendor_countries.py` | The check constraint `core_platformoperator_allowed_vendor_countries_not_empty` has no test; two admin tests were added in the implementation commit |
| TAX.5 | `mpmv/settings/test.py` | Test settings keep `VIES_CLIENT = HttpViesClient`; point them at a fake so no test can reach the real VIES |
| TAX.5 | `apps/tax/vies.py` | `HttpViesClient.check()` does not catch `http.client.HTTPException` subclasses (`IncompleteRead`, `BadStatusLine`) |
| TAX.5 | `apps/tax/vies.py` | The `urlopen` timeout applies per socket operation, not to the whole request or DNS; use a total deadline |
| TAX.5 | `apps/tax/tests/integration/test_vies.py` | The implementation commit added tests and cases; keep new tests in the tests-only commit |
| TAX.5 | `apps/tax/vies.py` | `ViesVatNumberValidator` maps malformed and prefix-mismatch numbers to `invalid`, so `Vendor.vies_status` records an answer VIES never gave, with no `VatValidation` row |
| TAX.5 | `apps/tax/engine.py` | `INVALID_REQUESTER_INFO` and every other VIES error code map to `unavailable`; a wrong operator VAT number fails every B2B check with a generic message |
| TAX.5 | `apps/tax/services.py` | `verify_vat_number` logs the full VAT number at warning level; a sole trader's number can be personal data |
| SHP.14 | `apps/shipping/config_io.py` | Area codes in a method's `area_treatments` are stripped but not lower-cased, unlike special-area and country-group rows |
| SHP.14 | `tests/integration/test_special_area_territories.py` | The guard checks only the country of a linked territory, not that its postcode list agrees with the area's |
| SHP.14 | `apps/shipping/migrations/0009_seed_special_areas.py` | The seed covers less than the task row (UK Highlands and Islands, Ionian islands, Mount Athos, Saint-Barthélemy and Saint-Martin) |
| SHP.14 | `apps/shipping/migrations/0009_seed_special_areas.py` | Sardinia's list leaves out OG, OT, CI and VS, so an address with no postcode and one of these provinces is not resolved |
| SHP.14 | `apps/shipping/services.py` | `delete_config(dry_run=True)` on a treated `SpecialArea` previews a success; `ProtectedError` comes only on the real run |
| CAT.13 | `apps/catalog/migrations/0006_sku_identity.py` | The migration does not reverse once raw SKUs exist (the CAT.1 normalized checks come back) |
| CAT.13 | `apps/catalog/migrations/0006_sku_identity.py` | `fill_sku_keys` reports only product-vs-variant clashes, not two rows of one table getting the same match key |
| CAT.13 | `apps/catalog/tests/integration/test_sku_identity.py` | The SKU-SLASH regression test cannot fail: a slug never holds `/`; assert on the product URL |
| CAT.13 | `apps/catalog/services.py` | `update_by_sku` does not validate values before the ChangeSet, so a dry run can succeed where the real run raises `IntegrityError` |
| CAT.13 | `apps/api/v1/catalog.py` | Spec 03 §3.2 wants a warning for troublesome SKUs; it shows only in the staff admin, not on the API create path |
| CAT.5 | `apps/catalog/admin.py` | A language-only change on a translation never calls `sync_product_slug` |
| CAT.5 | `apps/catalog/services.py` | Reclaiming one of the product's own `SlugHistory` rows deletes it with no AuditLog entry |
| CAT.5 | `apps/catalog/services.py` | `sync_product_slug` moves the current slug to `SlugHistory` without checking that another product holds it as history |
| CORE.14 | `apps/api/v1/catalog.py` | The write re-check (`require_active_vendor=True`) is opt-in per service and route and nothing guards it; add a guard test for vendor-scoped write routes |
| CORE.14 | `apps/api/v1/endpoints.py` | `GET /api/v1/me` reports scopes the key can no longer use |
| ORD.1 | `apps/cart/session.py` | A failed merge orphans the guest cart: the session key is popped before `merge_carts`; pop it after |
| ORD.1 | `apps/cart/services.py` | Two concurrent first sign-ins of one user conflict |
| ORD.1 | `apps/cart/services.py` | Inconsistent locking can lose line updates in `set_line_quantity` and `remove_line` |
| ORD.1 | `apps/cart/session.py` | The guest cart key is the live session key |
| CORE.12 | `apps/api/engine.py` | The per-client override in `limit_for` does not apply to scoped buckets, so a runaway key keeps its scope limits |
| CORE.12 | `apps/api/services.py` | The window comes from the app clock, not the database clock |
| CORE.12 | `apps/api/services.py` | Behaviour when the counter table is unavailable is not specified or tested |
| TAX.2 | `apps/tax/services.py` | `configure_vendor_tax_profile` accepts any `requested_by` from a direct caller with no `TaxRegimeChangeRequest` behind it |
| CORE.2 | `apps/vendors/services.py` | Audit chunk 4 m1: the last-owner rule counts inactive owners and `add_member` accepts an inactive user |
| CORE.2 | `apps/vendors/services.py` | Audit chunk 4 m2: the membership services check no actor, so the MEMBERS permission is enforced nowhere |
| CORE.13 | `apps/api/services.py` | Audit chunk 4 m3: removing an owner leaves the keys they issued working, and a key does not record who issued it |
| CORE.13 | `apps/api/services.py` | Audit chunk 4 m4: an applicant vendor's owner may issue a key with write scopes |
| CORE.3 | `tests/tenancy_registry.py` | Audit chunk 4 m5: no test of a vendor key creating for another vendor; `POST /api/v1/catalog/products` is declared `NoVendorData` |
| THM.2 | `apps/themes/engine.py` | THM.2 adds the token functions to the engine |
| THM.3 | `themes/base/templates/layouts/` | THM.3 fills the root manifest and moves the CMS.1 and account templates onto layouts; `data-theme` moves with them |
| THM.11 | `pyproject.toml` | An `ignore_imports` entry for `apps.vendors -> apps.themes.engine` in the "No app imports themes" contract |
| CAT.3 | `apps/api/v1/catalog.py` | There is no API path for a product without a price |
| CAT.3 | `apps/catalog/models.py` | The `VariantOption` docstring says the composite foreign key is in "migration 0003"; it is in `0004_attributes.py` |

Audit chunk 4 (#98): 0 blocker, 0 major, 5 minor (the five rows marked above).

## Merges after wave 8 (2026-11-08 to 2026-11-10)

One line per merged PR, in merge order within each group. #91, #93, #101 and #107, listed above as
not merged, merged on 2026-11-08 after their fixes. Task status is in the [Tasks](#tasks) table.

Spec and process:

| PR | Task | What |
|---|---|---|
| #124 | TAX.12 | Spec: VAT-inclusive consumer prices as a go-live gate |
| #131 | VEN.24 | Spec: owner decisions MQ-1 to MQ-12, VEN.24 and two P2 rows |
| #143 | ARC.13, ARC.21 | Spec: single STAGE VPS, multi-arch image, STAGE abuse tests |
| #145 | ARC.22 | Spec: public STAGE demo mode |
| #132 | ARC.20 | Trusted input for AI agents (rule and docs) |
| #155 | CORE.18 | Spec: Manager backoffice, staff roles, setup wizard, import connectors |
| #157 | SEO.7, CMS.11, CAT.20 | Spec: schema.org graph, product cards, linked brands |
| #158 | — | Coherence check 6: generic wording for the legacy system in the spec |
| #165 | MQ-14 to MQ-17 | Spec: owner decisions 2026-11-09 |
| #172 | — | Coherence check 7: spec rows, docs drift and hygiene fixes |
| #173 | — | Coherence check 7 follow-up: lane-a decisions, progress log, test hygiene |

Commerce (catalogue, vendors, tax, shipping):

| PR | Task | What |
|---|---|---|
| #107 | VEN.18 | Fiscal regime RF20 |
| #93 | SHP.14 | Special areas and per-method treatments |
| #101 | VEN.21 | Staff reopen a closed vendor to onboarding |
| #91 | CMP.13 | Vendor establishment gate on the allowed vendor countries |
| #110 | CAT.8 | Listing views and URLs (2/3) |
| #118 | CAT.8 | Listing templates and e2e (3/3) |
| #134 | CAT.8 | Interim net price marked on product cards |
| #139 | VEN.25 | RF20 only for EU-established vendors with an EX number |
| #138 | TAX.13 | Exemption reasons tied to their law's country |
| #137 | VEN.23 | DAC7 seller dataset |
| #154 | CAT.19 | Vendor products in the Manager shop space |
| #141 | VEN.24 | Claim an imported vendor |
| #167 | CAT.9 | Product search: full-text per language, trigram, SKU prefix |
| #176 | CAT.9 | Make the index-usage EXPLAIN test deterministic |
| #174 | CAT.6 | Redirects |

Themes, CMS, languages and events:

| PR | Task | What |
|---|---|---|
| #89 | I18N.3 | wagtail-localize for CMS pages, images per locale |
| #109 | CMS.2 | Hero, CTA, FAQ and rich text blocks (1/2) |
| #117 | CMS.2 | Page, term and vendor choosers (2/2) |
| #82 | EVT.12 | Geocoder chain and Find coordinates endpoint |
| #108 | THM.2 | Design-token pipeline |
| #113 | CMS.8 | Image source, licence and credit |
| #114 | THM.3 | Base layouts, skip link, header and navigation (1/2) |
| #119 | THM.3 | Footer, page shells, error pages, base form styles (2/2) |
| #121 | THM.4 | Form and button components, styleguide page (1/3) |
| #122 | THM.4 | Cards, product tiles, badges and alerts (2/3) |
| #123 | THM.4 | Navigation components (3/3) |
| #125 | EVT.1 | Event page models, constraints and tenancy (1/2) |
| #127 | EVT.1 | Event admin panels and page templates (2/2) |
| #135 | THM.4 | Safe hrefs and heading levels in card and product tile |
| #130 | EVT.2 | Recurrence engine |
| #128 | THM.5 | Theme quality gates |
| #126 | CMS.10 | Product grid block |
| #144 | EVT.13 | Map picker for event locations |
| #142 | I18N.9 | Customer legal and checkout pages per language |
| #168 | CMS.4 | Term and vendor landing pages |
| #151 | THM.12 | WebP and AVIF renditions, sanitised SVG logos |
| #178 | EVT.1 | Close the date pickers before Publish in the event e2e test |
| #156 | CMS.3 | Blog with scheduling, authors and choosers |
| #147 | EVT.3 | Event services and moderation workflow (1/2) |
| #149 | EVT.3 | Event moderation UI (2/2) |

Core, accounts, API and security:

| PR | Task | What |
|---|---|---|
| #111 | CORE.4 | Block privilege escalation in the user and group admin (audit #98 chunk 3) |
| #136 | CORE.16 | API rate limit defaults and per-key override |
| #140 | CORE.15 | Secure action links |
| #162 | SEC.5 | Security headers on static files and redirects |
| #163 | CORE.15 | Review follow-ups from #140 |
| #169 | SEC.5 | Endpoint security sweep |
| #166 | SEC.5 | Hostile-input corpus and property-based tests |
| #171 | SEC.5, CORE.20 | Rate-limit tests and a per-IP limit on refused API keys |
| #160 | SEC.5 | Periodic security runs: nightly and weekly STAGE scans |
| #177 | CMP.9 | Accessible authentication (WCAG 2.2 SC 3.3.8) |
| #183 | SEC.5 | Refused-key log test made deterministic (flaky fragment assertion) |

Infrastructure and CI:

| PR | Task | What |
|---|---|---|
| #133 | OPS.4 | SBOM fallback to the syft container |
| #146 | ARC.13 | Multi-arch release image (amd64 and arm64, native arm64 runner) |
| #148 | ARC.13 | Database env file, DB root CA mount, site-only Caddy for STAGE and LIVE |
| #150 | ARC.10 | Synthetic data seed |
| #152 | ARC.13 | Media volume for uploads, served read-only by Caddy |
| #153 | ARC.23 | Release gate with production parity |
| #161 | OPS.4 | Dependabot: uv image 0.10.11 to 0.13.7 |
| #159 | THM.12, ARC.13 | Caddy SVG CSP and media allow-list parity |

Compliance, security and content:

| PR | Task | What |
|---|---|---|
| #187 | none | Coherence check 8: release gate, audit and jobs docs, audit-action guard test |
| #175 | CMP.3 | GPSR data, publication gate, safety cases, warnings languages |
| #164 | SEC.2 | Content-checked, re-encoded uploads with caps and a malicious-file corpus |
| #191 | SEC.5 | Register the Hypothesis ci, dev and nightly profiles |
| #129 | CMS.9 | Launch countdown block |
| #192 | SHP.13 | CSV export refuses or caps a cell the importer cannot read (round trip) |
| #170 | CORE.17 | Manager shell, two gated spaces, closed vendors refused |
| #197 | SEC.5, OPS.9 | STAGE scan triage: no wildcard CORS, CORP header, ZAP rules file |
| #186 | TAX.3 | Spec: T15 (Saint-Martin and Saint-Barthelemy outside the VAT territory) in the Done-when |
| #185 | CORE.8 | Platform staff is the `vendors.view_all_vendors` permission, not `is_staff` (1 of 3) |
| #195 | none | Coherence check 9: audit-trail rows, guard test over every app |
| #196 | none | Spec: rows accepted by lane-a (CORE.21, CORE.22, CMP.27, MIG.15, OPS.8, OPS.9, SEC-R2 rename) |
| #203 | ARC | Unit and integration tests in parallel with pytest-xdist (CI and `make test`) |
| #199 | SEC.12 | Proxy header ownership between Caddy and the app |
| #207 | none | Coherence check 10 |
| #205 | EVT.3 | Security fixes: platform staff exemption, recheck on submit, depth cap, no userinfo in CTA URLs |
| #188 | CORE.8 | Account unlock service and unlock after a completed password reset (2 of 3) |
| #189 | CORE.8 | Editor roles replace the default groups, CMS-only Wagtail menu (3 of 3) |
| #179 | TAX.7 | Special territories by postcode, initial dataset |
| #209 | CORE.8 | Editor-role seeding safe during partial migrations (main red fix) |
| #181 | CMP.25 | Activation gate for platform duties, self-certification |
| #182 | VEN.10 | Public vendor storefront |
| #184 | THM.6 | `ride` theme |
| #210 | none | Coherence check 11 (release candidate v0.1.3) |
| #202 | VEN.22 | Public "Sell with us" application |
| #201 | CMS.12 | Server-side rich-text sanitiser |
| #198 | ARC.17 | Remove the e-invoicing leftovers |
| #200 | CORE.9 | Shop dashboard with product KPIs (1 of 3) |
| #212 | CORE.17, CORE.23 | Manager fixes: CSRF, root redirect, explanatory 403, platform owner bootstrap |
| #213 | OPS.10 | Nightly line-only coverage workflow |
| #204 | CORE.9 | Owner-only storefront profile editing (2 of 3) |
| #221 | CAT.5 | D-25: product slugs without the language suffix, reslug migration `catalog/0010` |
| #228 | none | Coherence check 12 |
| #220 | I18N.11 | Language switch stays on the same object |
| #180 | EVT.5 | Vendor events API |
| #232 | none | Coherence check 13 (release gate v0.1.4) |
| #211 | THM.12 | De-flake the SVG sanitiser timing test |
| #229 | VEN.22 | Generate the test password instead of a literal |
| #217 | CMP.7 | Axe accessibility audit of key public pages in CI |
| #237 | I18N.12 | Preferred language on unprefixed areas |
| #214 | ORD.2 | Order, VendorOrder, OrderLine models, numbering, snapshots, D-29 and D-30 guards |
| #240 | SEO.8 | Force noindex floor (1 of 2) |
| #208 | CORE.9 | Vendor logo upload, owner only (2b) |
| #246 | none | Coherence check 14 |
| #222 | CMP.5 | DSA points of contact, platform terms and ranking pages (1 of 4) |
| #236 | SEC.13 | Anonymous POST form sweep and public forms register |
| #245 | I18N.7 | Italian catalog for the Manager and account strings (1 of n) |
| #238 | VEN.29 | Storefront description and policies as rich text (D-34) |
| #239 | I18N.11 | Language switcher sweep on every page |
| #218 | TAX.3 | `resolve_goods_tax` and the goods regression table |
| #230 | CMP.5 | DSA notice and action (2 of 4) |
| #227 | none | Spec: D-32 session levels, anonymous-form sweep, session hardening rows |
| #234 | CORE.25 | Two session levels, restricted and operational (D-32) |
| #247 | ORD.4 | Transactional place_order with StockReservation and price verification |
| #260 | none | Coherence check 15 |
| #226 | none | Spec: D-29 order data retention |
| #251 | SEC.14 | Protect the page view restriction forms; lift the interim block |
| #224 | TAX.5 | VIES consultation log with the vendor as requester; Madeira and Azores (D-28) |
| #233 | CMP.5 | Statement of reasons, acting on content, art. 18 notification (3 of 4) |
| #250 | OPS.2 | Observability: JSON logs, error tracker, readiness, alert hooks |
| #248 | VEN.31 | Manager image chooser and upload into the vendor's own collection (1/2) |
| #253 | CORE.18 | Platform staff roles and the role-permission matrix (1/2) |
| #249 | ORD.6 | Vendor fulfilment: accept, ship with tracking, delivered |
| #254 | SHP.3 | quote_shipping with Rates, IndicativeRates, NeedsQuote, Blocked |
| #255 | TAX.4 | resolve_shipping_tax, shipping follows the goods |
| #256 | CMP.4 | GDPR customer export and anonymisation (1/2) |
| #252 | OPS.5 | Django admin loopback only: proxy and app allow-list, SSH tunnel |
| #262 | none | Coherence check 17 |
| #244 | VEN.31 | Spec: VEN.31 Manager media chooser row (D-34), ARC.29 row |
| #268 | none | Coherence check 18, release gate v0.1.6 |
| #269 | CORE.18 | Role drift check must not block the seed migration (v0.1.6 deploy blocker) |
| #257 | ORD.9 | Transactional order emails per language (1/2) |
| #258 | ING.1 | `ingest` app: staging models, ImportRun, ReviewItem, MappingTable |
| #259 | CAT.20 | Brand and manufacturer as linked entities, data model (1/3) |
| #274 | CORE.18 | Database checks tolerate pending migrations; CI upgrade-path step |

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

### Check 5 (2026-11-07, after waves 5 and 6 and the spec PRs #47 to #61)

Scope: main at bb5c089. Code of TAX.1, CORE.13, SEC.3, VEN.5 and CMS.1 (wave 5) and OPS.4, ARC.8,
CAT.2, SHP.12 and TAX.2 (wave 6) against the spec as it stands after #47 to #61 (themes, the
no-invoicing principle and the regulatory check, D-21 with CMP.13 to CMP.24, the Italian content
at go-live, the events module); spec 01 architecture rules; backlog health; the open minor review
findings; the §91.6 velocity measurement.

| Area | Result |
|---|---|
| Done-when criteria | Covered by tests for all ten tasks: TAX.1 (models, admin, services, profile consistency; migrations through `makemigrations --check`), CORE.13 (vendor key negative tests for every API operation), SEC.3 (raw dump without cleartext, key rotation), VEN.5 (end-to-end onboarding journey and activation gate), CMS.1 (seed in all five languages), OPS.4 (update configuration test; Dependabot opened #60), ARC.8 (environment defaults, kill switches, vendor overrides, step-up in the admin), CAT.2 (27 admin tests with translations), SHP.12 (two methods with different bands, vendor zone override, VAT treatment per method), TAX.2 (request, approval, rejection, history, review permission, step-up; the email and the vendor page are VEN.11 as recorded). Small gaps go to the cleanup table: CMS.1 requests only EN, IT and DE pages over HTTP, SHP.12 has no vendor deletion test |
| Spec 01 conventions | Engines import no ORM; money is `MoneyField`; prices, tax rates, tax profiles, regime changes, shipping configuration, contracts, vendor status, flags, API keys and key rotation go through services with dry-run, `ChangeSet` and audit. Exceptions without dry-run are not listed in spec 01 (finding 8); product fields other than prices are saved by the admin (finding 4) |
| App dependency graph | import-linter 3/3 kept; no edge of spec 01, 06, 14 or 15 is blocked. `apps.themes` and `apps.events` do not exist yet: the EVT.1 row already adds the events layer and bans events from tax and shipping; THM.1 needs a forbidden contract for `themes.engine`, not a layer (finding 8). The contracts let seo, compliance and ingest import orders, payments, ledger and cart, more than spec 01 §1.3 draws, and §1.3 lacks the vendors → cms edge: record both at the next spec 01 edit |
| Tenancy | All 19 vendor-owned models use `VendorScopedManager` with a path to Vendor and are in the tenancy registry, wave 5 and 6 models included (ContractAcceptance, VendorFeatureFlag, ShippingZone, ShippingMethod and its translation, VendorTaxProfile and its translation, TaxRegimeChangeRequest, ApiClient); `NOT_VENDOR_OWNED` is empty. VatValidation, CountryGroup, the taxonomy models, Brand, VendorContract and LegalFieldRequirement have no vendor, as specified. ShippingZone allows an empty vendor and member reads leave platform zones out: SHP.6 and the vendor area must show "own or platform" zones |
| Audit | Sensitive mutations write AuditLog through services, except the non-price product fields (finding 4) |
| Code vs today's spec | Findings 1 to 7. No deemed-supplier branch exists, as D-21 requires; the FatturaPA party block stays valid as data under the no-invoicing principle |
| Spec vs spec | Spec 04 said completeness never blocks a status change while spec 11 makes the DSA article 30 data a condition of activation (finding 2, text fixed). §91.2 said 1,435 h of P1 while the backlog had 1,469 h since #54 (finding 9). #62, merged during this check, answered Q-72 to Q-75 and moved vendor closures from EVT.8 to the new VEN.20; the Vendor model has no closure field yet, so nothing in the code contradicts it |
| Anonymization | No vendor names, legacy hostnames, order numbers or credentials in the new text |
| Migrations | One leaf per app; `makemigrations --check` clean |
| Gates | `make docs` (214 tasks, P1 1,494 h, critical path 147 h) and `make check` green; `make test` 2605 passed (e2e deselected); e2e not run (no interface change) |

Findings and actions:

1. Major. RF20 will be refused by the tax app. VEN.18 adds RF20 to the vendor regimes, but
   `apps/tax/migrations/0002_regime_change_request.py` hard-codes RF01 to RF19 in the
   `tax_regime_request_fiscal_regime_valid` constraint, and `profile_errors` in
   `apps/tax/engine.py` has no RF20 rule, so an RF20 profile would pass with any VAT regime. No
   exemption reason fits the cross-border franchise, which applies per member state. Action: VEN.18
   now depends on TAX.2, covers both constraints and an RF20 consistency rule (2 → 3 h); the VAT
   mapping needs the tax advisor.
2. Major. Activation ignores the DSA article 30 duties. `change_status` in
   `apps/vendors/services.py` checks only the accepted contract. Spec 11 makes the article 30
   trader data and the article 30(1)(e) self-certification conditions of activation, but no
   done-when tests it: CMP.10 does not, and CMP.11 does not depend on VEN.5. CMP.13 already owns
   the establishment gate. Action: new row CMP.25 (4 h) and spec 04 reworded. Spec 11 also lists
   Stripe KYC as a condition of activation, while D-19 lets a vendor without Stripe sell on request:
   owner question.
3. Major. No row owns the vendor API key policy. Any valid scope can be granted to a vendor key
   (`apps/api/services.py`), keys of a suspended or closed vendor still authenticate, and deleting
   a vendor drops its keys without a per-key audit entry. EVT.5 will add vendor event scopes on top
   of CORE.13. Action: new row CORE.14 (3 h), written on the recommended answers of the two open
   CORE.13 questions.
4. Major. Product edits that drive tax and tenancy are not audited. `ProductAdmin.save_model`
   (`apps/catalog/admin.py`) saves `fiscal_category`, `sales_mode`, `weight_kg` and the vendor
   through Django's default save, with no dry-run and no audit entry; `catalog/apps.py` exempts
   Product with a reason that covers prices only, and CAT.10 covers only upsert, publish and
   retire. Action: new row CAT.12 (4 h).
5. Minor. E-invoicing leftovers. `PlatformOperator.einvoice_channel` with its choices, service,
   admin, factory and three tests, and a comment in `apps/vendors/engine.py` saying that CMP.2
   writes the XML. Spec 01 kept the field as informational while the owner decision drops
   `EInvoiceChannel`. Action: new row ARC.17 (1 h); spec 01 notes the proposal; owner question.
6. Minor. Tax territories still use the `a-b` postcode ranges (`apps/tax/engine.py`,
   `apps/tax/models.py`), while SHP.12 built `apps/core/postcodes.py`. TAX.7 is blocked by D-02,
   and SHP.14 links its extra-VAT areas to Territory. Action: new row TAX.11 (2 h), ready now;
   TAX.7 and SHP.14 depend on it. The register question on the grammar now recommends the row.
7. Minor. `ShippingMethod.tax_treatment` can already be set to `standard_rate` or `exempt` by
   staff, while D-03 asked only about `force_vat`. Action: D-03 now covers the per-method
   treatment; the SHP.12 question (only "follows the goods" until D-03) is still open.
8. Minor. Architecture rules still rest on convention: no import-linter contract forbids
   `django.db` in engines (check 2 finding 4, carried since), and the services without dry-run
   (`record_heartbeat`, `record_vat_validation`, `start_totp_enrolment`, `use_recovery_code`,
   `authenticate_passkey`, `api.authenticate`, the throttle counters, `cms.sync_locales`,
   `record_access_change`) are not listed as exceptions in spec 01. Action: new row ARC.18 (2 h).
9. Minor. Stale rows and totals. SHP.2 asked for `match_postcode` and `resolve_zone`, which SHP.12
   built: narrowed to legacy cases 1 to 7 plus the SHP.12 engine findings (country normalisation,
   patterns parsed once, the spaced dash range), 8 → 6 h. §91.2, §91.3 and the spec README now
   give 1,494 h of P1 (after #62) and ≈ 1,873–2,098 h to go-live.
10. Minor. Notes for later rows, no change now: VEN.20's `delivery_window` needs a rule for
    shipping methods whose delivery days are empty; VatValidation has no source field while CMP.14
    adds HMRC checks behind the same interface (CMP.14 or TAX.5); the CMP.11 identity documents
    need the SEC.3 field encryption when their model exists; TOTP secret encryption still waits on
    its owner question; the spec 12 §12.3 ID collision (check 2 finding 2) is still open; the
    "invoices issued before the date" rationale in `apps/tax/engine.py` goes with the TAX.2
    effective-date question.

#### Backlog health

READY P1 tasks (dependencies done, no open D-xx), 29: ARC.10, ARC.13, ARC.17, ARC.18, CAT.3,
CAT.5, CAT.9, CAT.12, CMP.3, CMP.5, CMP.13, CMS.5, CORE.8, CORE.14, EVT.12, I18N.3, ING.1, MIG.1,
OPS.2, ORD.1, SEC.0, SEC.5, SHP.2, SHP.5, SHP.13, TAX.5, TAX.11, THM.1, VEN.18. Dependencies done
but an open decision: CAT.8 (D-07), CORE.9 (D-14), TAX.3 (D-01), VEN.2 (D-04).

Wave 7. The planned SHP.13, SHP.14, SHP.2 and SHP.3 cannot run in parallel: SHP.13 and SHP.2 are
ready; SHP.14 needs SHP.13 (and now TAX.11); SHP.3 needs SHP.2 and SHP.14 and is blocked by D-06.
The plan's "IndicativeRates with requires_quote" above the last band is D-06's proposal ("quote"),
not yet recorded as decided (owner question). Proposal: **SHP.13, SHP.2, TAX.11, CORE.14 and
CAT.12** (shipping template and import/export; shipping engine and `apps/core/postcodes.py`; tax
territories; `apps/api`; catalog services and admin).

Wave 8. **SHP.14** (after SHP.13 and TAX.11), **CMP.13** (PlatformOperator and the activation
service), **THM.1** (new `apps/themes`, settings, import-linter), **ORD.1** (`apps/cart`) and
**ARC.13** once the Hetzner account exists, otherwise **TAX.5**. Next in line: SHP.3 when D-06 is
recorded, CMP.25 after CMP.13, VEN.18.

Blocked P1 work (tasks downstream of each blocker, overlapping):

| Blocker | Direct | Downstream P1 tasks | Hours |
|---|---|---:|---:|
| ARC.13 STAGE (needs the Hetzner account, an owner action) | ARC.15, ARC.16, OPS.1, CORE.7 and I18N.1 STAGE checks | 51 | 489 |
| D-07 tag URLs | CAT.8, MIG.6 (and through CMS.2 the events module) | 29 | 241 |
| D-04 plan fees | VEN.2 | 20 | 239 |
| D-06 weight above the last band | SHP.3 (and VEN.20) | 21 | 173 |
| D-14 manual edits of fed products | CORE.9 | 11 | 124 |
| D-01 B2C place of taxation | TAX.3 | 12 | 106 |
| D-03 shipping VAT | TAX.4 | 10 | 86 |
| D-08, D-09 PSD2 and payment methods | PAY.1 | 8 | 74 |
| D-20, D-05, D-13, D-10 | MIG.9, PAY.4, ING.5, SEO.5 | 2, 2, 3, 3 | 28, 22, 20, 16 |
| D-16, D-15, D-19, D-02 | MIG.4, CMP.7, VEN.6, TAX.7 | 1 each | 10, 8, 8, 4 |

#### Minor findings, consolidated

The 50 minor review findings of waves 5 (24) and 6 (26), deduplicated, plus one from this check.
Ten left the list: the TAX.1 dash ranges (TAX.11), the TAX.1 PR wording (no action), the CORE.13
key scope question and per-key audit on vendor deletion (CORE.14), the OPS.4 backlog rows and the
TAX.2 past effective date (owner questions), the SHP.12 spec 06 D-03 note (already fixed), the
SHP.2 narrowing (done here), and the SHP.12 country normalisation, pattern pre-parsing and spaced
range (now in SHP.2). The [cleanup wave](#cleanup-wave-2026-11-07) fixed 39 of the 41 items; the
Status column gives the PR, and the two skipped items are listed under the table.

| # | Batch | Task | File | Issue | Fix | Size | Status |
|---:|---|---|---|---|---|---|---|
| 1 | A | TAX.1 | `apps/tax/admin.py` | Legal notes can be moved to another profile or edited on a closed profile | Profile read-only on change; refuse edits on closed profiles | S | done (#66) |
| 2 | A | TAX.1 | `apps/tax/models.py`, `apps/core/models.py` | `Versioned` copies PlatformOperator's versioning code | Move it to `apps/core` and use it in both | M | done (#66) |
| 3 | A | TAX.1 | `apps/tax/engine.py` | `oss_registered_since` is not checked against the regime | Add the rule to `profile_errors` | S | done (#66) |
| 4 | A | TAX.2 | `apps/tax/engine.py`, `apps/tax/services.py` | "Approve on or after" compares the UTC date | Pass the business-time local date | S | done (#66) |
| 5 | A | TAX.2 | `apps/tax/services.py` | `request_regime_change` keeps caller-set review fields | Clear them | S | done (#66) |
| 6 | A | TAX.2 | `apps/tax/admin.py` | The change page runs `regime_history` twice | Compute it once | S | done (#66) |
| 7 | B | SEC.3 | `apps/core/encryption.py` | Associated data binds a value to its column, not its row | Bind model and primary key; re-encrypt in a migration | M | skipped |
| 8 | B | SEC.3 | `apps/core/fields.py` | Ordering by an encrypted field is not blocked | Raise on ordering and lookups | S | done (#69) |
| 9 | B | SEC.3 | `apps/core/checks.py` | The migrate check decrypts one sample per key id | Check every row in a command, keep the sample at migrate | S | done (#69) |
| 10 | B | SEC.3 | `apps/vendors/engine.py` | `mask_tax_code` shows the last 4 characters of a short TIN | Show at most a third of the characters | S | done (#69) |
| 11 | B | SEC.3 | `mpmv/settings/env.py` | With the key variable and its `_FILE` form both set, the file wins silently | Refuse both | S | done (#69) |
| 12 | B | SEC.3 | `apps/vendors/migrations/0008_encrypt_iban_and_tax_code.py` | Imports live helpers, assumes an `id` primary key | Freeze the helpers in the migration | S | done (#69) |
| 13 | B | VEN.5 | `apps/vendors/services.py` | Two first contract versions saved at once end in an IntegrityError | Lock or catch and return a clear error | S | done (#69) |
| 14 | B | VEN.5 | `apps/vendors/services.py` | `submit_application` keeps `vies_status`, `vies_checked_at` and `legacy_wp_user_id` | Reset them | S | done (#69) |
| 15 | B | VEN.5 | `apps/vendors/tests/integration/test_onboarding.py` | No test for an inactive owner accepting a contract | Add it | S | done (#69) |
| 16 | B | VEN.5 | `apps/vendors/admin.py` | The change page computes the current contract twice | Compute it once | S | done (#69) |
| 17 | B | ARC.8 | `apps/vendors/models.py`, `apps/vendors/admin.py` | An `enabled=True` override of a kill switch is accepted and has no effect | Refuse it in `clean()` | S | done (#69) |
| 18 | B | CORE.13 | `tests/tenancy.py` | The harness skips a marker that is a substring of the request path | Match whole path segments | S | done (#69) |
| 19 | C | ARC.8 | `apps/core/tests/integration/test_feature_flags.py` | The TTL test bypasses the cache | Let the TTL expire under a frozen clock, or rename it | S | done (#70) |
| 20 | C | ARC.8 | `apps/core/flags.py` | Registration in `AppConfig.ready()` is not required | System check and docs | S | done (#70) |
| 21 | C | ARC.8 | `apps/core/services.py` | `flag_enabled` accepts any model as vendor | Take a vendor id or check the model label | S | done (#70) |
| 22 | C | CMS.1 | `apps/cms/tests/factories.py` | ContentType patch for `.build()` that no test uses | Remove it | S | skipped |
| 23 | C | CMS.1 | `apps/accounts/middleware.py` | MFA middleware does not exempt Wagtail's sign-out | Exempt it | S | done (#70) |
| 24 | C | CMS.1 | `apps/cms/services.py` | Seeded menus of other languages are not translations of the EN menu | Seed them as translations (same translation key) | M | done (#70) |
| 25 | C | CMS.1 | `mpmv/settings/base.py` | `WAGTAILADMIN_BASE_URL` falls back to localhost on live | Require it on live | S | done (#70) |
| 26 | C | CMS.1 | `apps/cms/apps.py` | Locale sync after migrate always writes to the default database | Honour `using` | S | done (#70) |
| 27 | C | CMS.1 | `apps/cms/models.py` | Editors can create a second HomePage in a locale | One HomePage per locale | S | done (#70) |
| 28 | C | CMS.1 | `apps/cms/tests/integration/test_pages.py` | FR and ES pages are never requested over HTTP (check 5) | Parametrize over every configured language | S | done (#70) |
| 29 | D | CAT.2 | `apps/catalog/tests/integration/test_taxonomy_admin.py` | No test moves a node under a same-slug sibling or inside a flat taxonomy | Add both | S | done (#67) |
| 30 | D | CAT.2 | `apps/catalog/taxonomy.py` | `Term.clean` raises KeyError on an invalid taxonomy schema | Validate the schema first | S | done (#67) |
| 31 | D | CAT.2 | `apps/catalog/taxonomy_admin.py`, `apps/catalog/admin.py` | Term reference and `Product.terms` widgets do not scale | Autocomplete filtered by taxonomy | M | done (#67) |
| 32 | D | CAT.2 | `apps/catalog/taxonomy.py` | Descriptions are raw HTML | Document that CAT.8 and CMS rendering sanitize them | S | done (#67) |
| 33 | D | CAT.2 | `apps/catalog/taxonomy.py` | A direct `Term.move()` skips the placement rules | Override `move` | S | done (#67) |
| 34 | D | CAT.2 | `apps/catalog/tests/unit/test_taxonomy_engine.py` | Name breaks the ADR-0006 convention | Merge into `test_engine.py` | S | done (#67) |
| 35 | D | SHP.12 | `apps/shipping/services.py` | `zone_snaps` counts zones served only by disabled carriers | Filter on active carriers | S | done (#67) |
| 36 | D | SHP.12 | `apps/shipping/models.py` | `CountryGroup.code` accepts capitals that zones cannot reference | Lower-case it with a constraint | S | done (#67) |
| 37 | D | SHP.12 | `apps/shipping/models.py` | `ShippingZone.vendor` is PROTECT while methods CASCADE | Align them and test vendor deletion | S | done (#67) |
| 38 | E | OPS.4 | `.github/workflows/release.yml` | The whole job holds `contents: write`; no `persist-credentials: false`; `anchore/sbom-action` pinned by tag | Small attach job with write access; set the flag; pin by SHA | S | done (#65) |
| 39 | E | OPS.4 | `docs/dev/dependency-updates.md` | The Python SBOM has no hashes, the doc says it has | Drop "and hashes" | S | done (#65) |
| 40 | E | OPS.4 | `.github/dependabot.yml` | The uv image is in the docker minor/patch group; 0.x minors break (#60 already does it) | Exclude it from the group | S | done (#65) |
| 41 | E | OPS.4 | `tests/unit/test_dependency_updates.py` | `open-pull-requests-limit` and "majors never grouped" are not asserted | Assert both | S | done (#65) |

Skipped in the cleanup wave:

- 7 (SEC.3, row binding). Needs an owner decision. Django's `from_db_value` gets no row, so
  decryption would move into a model-level descriptor: `QuerySet.update()` and `bulk_update()`
  could no longer write the field, `values()` would return tokens, rotation and the migrate check
  would need the row id, and every stored value would need a re-encryption migration. That is a
  design change, not a cleanup. Owner question Q-93 recommends a separate SEC.3 follow-up task of
  about 6 h before VEN.6.
- 22 (CMS.1, factory ContentType patch). The finding is wrong:
  `tests/unit/test_factories.py::test_every_factory_builds_without_the_database` calls `.build()`
  on every factory, and without the patch the HomePage and StandardPage builds hit the database.
  The patch stays and its docstring now says why.

Cleanup batches (disjoint files, one agent each):

| Batch | Scope | Items | Size |
|---|---|---:|---|
| A | Tax (`apps/tax`, `apps/core/models.py`) | 6 | 5 S, 1 M |
| B | Vendors, encryption, tenancy harness (`apps/vendors`, `apps/core/encryption.py`, `fields.py`, `checks.py`, `mpmv/settings/env.py`, `tests/tenancy.py`) | 12 | 11 S, 1 M |
| C | Flags and CMS (`apps/core/flags.py`, `apps/core/services.py`, `apps/cms`, `apps/accounts/middleware.py`, `mpmv/settings/base.py`) | 10 | 9 S, 1 M |
| D | Catalog taxonomy and shipping models (`apps/catalog`, `apps/shipping/models.py`, `services.py`) | 9 | 8 S, 1 M |
| E | CI and dependency updates (`.github`, `docs/dev/dependency-updates.md`, `tests/unit/test_dependency_updates.py`) | 4 | 4 S |

Run the cleanup wave before wave 7: batch D touches the shipping models and the catalog admin that
SHP.13 and CAT.12 change, and batch E should land before the next Dependabot run.

Owner questions (Q-85 to Q-89 in the private register):

1. Remove `PlatformOperator.einvoice_channel` (ARC.17) or keep it as information?
2. Is the D-06 proposal ("quote": an indicative rate that requires a quote above the last band)
   the decision, so SHP.3 can start after SHP.2 and SHP.14?
3. RF20 for the tax advisor: which VAT regime and exemption reason does an RF20 vendor get, per
   member state (VEN.18)?
4. Activation gate (CMP.25): confirm that the DSA article 30 data and the self-certification block
   activation, and say whether the Stripe account must be enabled too, given D-19.
5. Confirm the rows added by this check: ARC.17, ARC.18, CAT.12, CORE.14, TAX.11 and CMP.25, and the
   VEN.18 and SHP.2 changes (+15 h of P1).

### Check 6 (2026-11-09, after waves 8 and 9, #63 to #157)

Scope: main at 8e9a525, then 51a8878. Six read-only reviewers: commerce code vs spec, platform and
content code vs spec, done-when and tests, module dependency graph, docs drift, redaction. About 40
tasks had merged since check 5. Fix PR: #158 (redaction).

| Area | Result |
|---|---|
| Done-when criteria | Met for the merged tasks, with tests, except the partial ones in the Tasks table (CMS.5, VEN.25) and finding 4 |
| Spec 01 conventions | Engines pure, services with dry-run and AuditLog, money in Decimal; two guard gaps (finding 3) |
| App dependency graph | import-linter 8/8 kept; the contracts are looser than spec 01 §1.3 (finding 3) |
| Redaction | Specs described the live legacy system in too much detail (finding 2) |
| Docs drift | progress.md, README, spec README and spec 91 stale (findings 1 and 6) |
| Gates | `make check` green. The local `make test` was not conclusive: 5499 passed, 1 xfailed (TAX.12), 684 errors, all "connection is closed" from PostgreSQL on a saturated machine (load 35 on 12 cores); CI on main was green. The markdown test skipped (fixed by the check 7 follow-up) |

Findings and actions:

1. Major. `docs/progress.md` stopped at wave 8 (#120): no task rows after CAT.12 and a stale "not
   merged" line. Action: caught up by the check 7 follow-up.
2. Major (redaction). Specs 00, 02, 05, 06, 08, 10 and 12 described weaknesses and internals of
   the live legacy system. Action: generic wording in #158, details moved to the private folder.
   Git history keeps the old text; rewriting it is an owner decision.
3. Minor (graph). `apps.seed` and the planned `apps.manager` are missing from the spec 01 §1.3
   tree; the "Domain layers" contract lets ingest, seo, ledger, payments, events and api import
   more than §1.3 draws; the engine-purity contract (`apps.*.engine`) and the dry-run guard miss
   the nested `apps/core/geocoding/`. Action: lane-a at the next spec 01 edit; contract and guard
   changes as a lane-b follow-up.
4. Minor (done-when). ARC.10's seed is used only by its own tests; the I18N.9 readiness command
   runs in no gate; the CMS.5 recruitment link waits for VEN.22; the VEN.25 EX number is not in
   the FatturaPA party data. Action: recorded in the Tasks table; the gates go in the MIG cutover
   checklist.
5. Minor (code vs spec). The CORE.15 Continue page and the CORE.4 MFA templates are English only
   (spec 09); `MPMV_MAP_TILE_URL` accepts any https host while spec 15 forbids third-party tiles;
   model labels in themes, core, accounts and api are not translatable. Action: lane-b follow-ups.
6. Minor (docs). Spec README totals and decision count, spec 91 cross-cutting formulas, the media
   storage text after #152, the README status line, ADR-0002's orchestration note and the branch
   naming in parallel-lanes.md are stale. Action: lane-a, next spec edit (the syft fallback and
   the README layout were fixed in #172).
7. Earlier follow-ups now resolved: TAX.5 test VIES client and `HTTPException`; CORE.12 scoped
   buckets (#136); the THM.2 and THM.3 notes (#108, #114, #119); release images amd64 only
   (#146); the SEC.1 `/healthz` redirect. Still open: SHP.14 seed gaps, area lower-casing and
   dry-run delete; ORD.1 orphaned guest cart; TAX.5 full VAT number in the log; TAX.2
   `requested_by`; CAT.12 non-atomic admin deletes; the CAT.3 docstring; the CMP.13 constraint
   test; the SHP.2 error without the zone; SHP.13 float refusal and formula escaping (Q-95). The
   SHP.14 rows above name migration 0009; the seed is 0010.

### Check 7 (2026-11-09, after #141, #162, #163, #169, #167, #166, #171)

Scope: main at 46d586b. Four read-only reviewers: code vs spec and done-when, spec vs spec and docs
drift, redaction, hygiene and migrations, gates. Fix PR: #172. Follow-up with lane-a's decisions:
"Coherence check 7 follow-up" (this progress update).

| Area | Result |
|---|---|
| Done-when criteria | Met for VEN.24, CORE.15 and its follow-ups, CAT.9, CORE.20 and the merged SEC.5 items; every item has a test |
| Spec 01 conventions and ADRs | Held; lint-imports 8/8; the new dry-run exceptions are in the spec 01 §1.3 table. ADR-0006 does not mention Hypothesis or the per-topic test files |
| Spec vs spec | `make docs-check` clean. The SEC.5 row did not describe the merged work; strict xfails cited rows that did not exist; VEN.24 and MQ-1 said "legal email only" while the code also allows the PEC |
| Docs drift | audit-trail.md, background-jobs.md, dependency-updates.md and `mpmv.env.example` (10 variables) behind the code |
| Redaction and hygiene | Clean in all 7 diffs and PR texts; secret scans pass; one leaf per app; lock and ruff clean; unused claim constants and one key-shaped test literal |
| Migrations | #129, #156 and #168 each added `cms/0010`; #168 merged first, #156 and #129 renumber |
| Gates | `make check` green. `make test` 9579 passed, 30 skipped, 7 strict xfails, 0 failed (37 min locally, 19.5 min in CI, same counts as CI). `make test-e2e` 82 passed, 3 skipped |

Findings and actions:

1. Major. The SEC.5 row and the strict xfails had no owning rows. Action: SEC.5 rewritten; SEC.11,
   SEC.12, CMS.12 and I18N.10 registered in #172.
2. Major. VEN.24 destination. Action (lane-a decision): keep the code; the claim link may go to
   the legal email or the registered PEC. VEN.24, MQ-1 and specs 02 and 04 updated by the
   follow-up.
3. Major. Docs drift (audit trail, background jobs, env example). Action: fixed in #172, with
   system checks `core.E501` and `core.E502` refusing a console mail sender or a localhost sender
   on LIVE.
4. Major. Open PRs colliding on one migration number. Action (lane-a decision): the rule is in
   [parallel-lanes.md](dev/parallel-lanes.md): the PR merged later renumbers.
5. Major. progress.md about 55 PRs behind, check 6 not recorded. Action: this update.
6. Minor (hygiene). The integration fixture `page` shadowed pytest-playwright's `page`, and
   `tests/unit/test_build_docs_eof.py` never ran (`markdown` was not a dev dependency). Action:
   fixed by the follow-up. Deprecations (Django 2028 `MAILERS`, action-tuple indexing, Wagtail 9
   image permission policy) and the slow seed and migration tests: proposed row OPS.7.
7. Minor, for lane-a: the dry-run exception categories (`issue_action_token`,
   `confirm_claim_identity`); a new language needs a search configuration entry; D-14 should block
   CAT.19; MQ-12 and D-01 do not cross-reference; spec 91 and README totals; the CAT.9 follow-ups
   (stored tsvector, `SkuAlias` search, typo threshold); which database role runs `migrate`
   (`pg_trgm`); SEC.5 hours.
8. Minor, for lane-b: adapters that run read queries in the vendor views and admin; the CORE.20
   done-when on non-numeric block seconds; a developer doc for rate limits and the SEC.5
   registries; the vertical-specific styleguide sample.

Earlier findings still open: the SEC requirement and task ID collision (checks 2 and 3, owner
question); 3.5 compromised-password check has no row; 3.6 spec 02 sketch fields; 3.7 no system
check on `MFA_REQUIRED_POLICIES`; 4.3 ADR-0005 citations in deploy files; 4.5 OPS.2 dependencies;
5.5 `einvoice_channel` (ARC.17); the SEC.3 row binding (check 5 cleanup item 7, Q-93); the
check 6 items above.

### Check 8 (2026-11-10, after #156, #147, #149, #174, #183)

Scope: main at a35b1ff, five merges (76 files, about 8k lines). Two read-only reviews: code vs spec,
and spec, docs and hygiene. Fix PR: #187.

| Area | Result |
|---|---|
| Done-when criteria | Met for CAT.6, EVT.3 and CMS.3; services/engine split, `dry_run`, AuditLog and the `"IT"` rule kept; no blocker or major finding in the code |
| Docs drift | audit-trail.md lacked `catalog.redirect.*` and the EVT.3 actions, background-jobs.md lacked `cms.publish_scheduled` (now 7 periodic jobs). Fixed in #187, with a guard test that every audit action is listed |
| Release gate | The owner's rule (a coherence check on the exact commit before every STAGE release) is in [release.md](dev/release.md) |
| Hygiene | No credential-like literals or vendor names; 2 pre-existing TODOs tied to CORE.18 and CMP.4; the #183 flaky pattern swept, clean |
| Migrations | One leaf per app, contiguous (cms 0011, catalog 0009, events 0002). #156 renumbered its blog migration twice (0010, then 0011, after #168 and #129) |
| Gates | `make check` green, `makemigrations --check` clean, last 5 CI runs on main green; full suite not run (CI is the gate) |

Findings and actions:

1. Major. progress.md missed the five merges. Action: this update.
2. Minor. Docs drift above. Action: fixed in #187.
3. Minor. `cms.services.import_article` writes no AuditLog (Wagtail history covers it) and has no caller yet. Action: noted in audit-trail.md; the MIG owner uses it.
4. Minor. `RemovedInWagtail90Warning` at `apps/cms/admin_views.py:30` (image permission policy). Action: proposed row (with OPS.7 deprecations).
5. Minor. No developer pages for redirects (CAT.6) or event moderation (EVT.3). Action: proposed rows.
6. Minor, for lane-a: check whether #129 is still open and will renumber again (cms is at 0011), and that the CAT.6 follow-ups are registered as rows or answered.

### Check 9 (2026-11-10, after #175, #164, #191, #129, #192)

Scope: main at a99d71e, five merges (90 files, about 8.9k lines). Two read-only reviews: code vs spec
and gates, and docs, hygiene and migrations. Fix PR: the one that carries this entry.

| Area | Result |
|---|---|
| Done-when criteria | Met for SEC.2 and CMS.9. CMP.3 meets its Done-when but is `partial`: recall procedure, Safety Gate checks and repeat-offender suspension are not built. Services/engine split, `dry_run`, AuditLog and the `"IT"` rule kept; no blocker in the code |
| Docs drift | audit-trail.md had no `compliance.*` rows. The check 8 guard did not see them: its prefix list skipped `compliance`, and it ignored annotated constants (`X: Final = "..."`), which the compliance app uses. It also missed `shipping.shippingtemplate.*`. Fixed here: the guard now scans every app under `apps/` and annotated constants. No developer pages for uploads, GPSR or the countdown block |
| Hygiene | No credential-like literals, vendor names, new TODOs or sleeps; 2 pre-existing TODOs tied to CORE.18 and CMP.4 |
| Migrations | One leaf per app, contiguous (cms 0012, events 0003, compliance 0002); #129 settled |
| Gates | `make check` green, `makemigrations --check` clean; main CI green, but the Security STAGE scan failed once (ZAP medium 1, low 3) and its rerun on a99d71e passed; full suite not run (CI is the gate) |

Findings and actions:

1. Major. progress.md lacked CMP.3, SEC.2, CMS.9, CMP.9 and six PR rows. Action: this update.
2. Major. CMP.3 status honesty (see above). Action: marked `partial`; proposed rows below.
3. Minor. audit-trail.md drift and the blind guard. Action: fixed here.
4. Minor. `RemovedInWagtail90Warning` at `apps/cms/admin_views.py:30` still open. Action: row proposed in check 8.
5. Minor. No dev docs for uploads (SEC.2), GPSR (CMP.3), countdown (CMS.9); final SEC.2 caps not in spec 12; the SEC.2 ID collision with the spec 12 requirement row. Action: proposed rows.
6. Minor, ops. STAGE scan medium alert not triaged (the report stays on the runner). Action: for lane-a after #194.
Open from check 8: item 6 answered (#129 merged, cms settled at 0012).

### Check 10 (2026-11-10, after #170, #197, #186, #185, #196, #203, #199)

Scope: main at 2a3b8b3, seven merges since check 9 (91 files, about 3.5k lines). Review by lane-b in four
dimensions. Fix PR: the one that carries this entry.

| Area | Result |
|---|---|
| Code vs spec | CORE.17 and SEC.12 meet their Done-when (xfails removed, 404 gate, sweep test, closed vendors). CORE.8 is 1 of 3, marked `partial`. Impersonation (CORE.10) is not built, so platform staff always get 404 in the shop space (stated in #170). Manager code has no `"IT"` literal; views, lists and actions are thin adapters; bulk actions run with `dry_run` and an audit context. No new audit action strings, so audit-trail.md needs no rows (guard test green) |
| Gates | `make check` pass, `makemigrations --check` clean, 39 targeted tests (audit guard, manager unit) pass. One leaf per app (vendors 0021). Main CI: 15908d2 green, 2a3b8b3 running when checked; cancelled runs are superseded pushes. Security nightly: failure on 29bed6d, green rerun on a99d71e. Security STAGE scan: last two runs failed (2c09781, d46ce88), both before #197 merged; no run since, so OPS.9 stays open |
| Docs | progress.md lacked CORE.17, CORE.8, SEC.12 and #170, #197, #186, #185, #195, #196, #203, #199 (added here). release.md carries the platform-staff note; CONTRIBUTING has the parallel-tests section. No developer page for the Manager shell |
| Hygiene | No credential-like literals (test passwords come from `secrets.token_urlsafe`), vendor names, hostnames, new TODOs, prints or sleeps in the diff. Still the `RemovedInWagtail90Warning` (OPS.7) |

Findings and actions:

1. Major. progress.md missed seven merges and four tasks. Action: this update.
2. Minor. No developer docs for the Manager shell (navigation registry, gate, list views, vendor switcher). Action: proposed row.
3. Minor. OPS.9 needs a STAGE scan run on main after #197 to close. Action: for lane-a (run the workflow).
4. Minor. The progress.md PR tables skip about 25 older PRs (mostly spec and docs, numbers under 100). Action: none, they are listed by task where it matters.
Open from check 9: items 1 to 3 closed; dev docs, SEC.2 caps and the ID collision became OPS.8, spec 12 and SEC-R2 (#196); CMP.3 remainder is CMP.27.

### Check 11 (2026-11-10, release candidate v0.1.3, main at bd01f8e)

Scope: main at exactly bd01f8e (the release candidate), eight merges since check 10 (#205, #188, #189,
#179, #209, #181, #182, #184; 89 files, about 5.5k lines, counted from a20c9e7). Review by lane-b in four
dimensions. Fix PR: the one that carries this entry.

| Area | Result |
|---|---|
| Code vs spec | Done-when met for CORE.8 (all three parts: platform staff permission, unlock service with audit and reset clearing the lockout, editor roles with a CMS-only admin menu), TAX.7 (T15 green, most specific pattern wins, alias countries fail closed), CMP.25 (activation purpose seeded, append-only self-certification, DAC7 still payout-only), VEN.10, THM.6 (no file under `themes/ride` references `private/`; `LICENSES.toml` lists Barlow, Barlow Condensed, Lucide and the placeholder art with their licence texts, all files present). Services/engine split, `dry_run`, AuditLog and the operator-country rule kept: the only `"IT"` literal in `tax` is the postcode-length table, marked as territory data; `tax` and `shipping` import nothing from `orders` |
| Gates | `make check` pass (ruff, mypy strict, import contracts, backlog freshness, Django checks), `makemigrations --check` clean. One leaf per app, contiguous: cms 0013, vendors 0023, tax 0006, events 0003, core 0017, catalog 0009, api 0007, accounts 0005, compliance 0002, shipping 0010, themes 0001, cart 0001. Main CI on bd01f8e: success (and on 9bc1acf, dc8a025, 48bbd24). Full suite not run locally (CI is the gate). Security nightly: failure on 29bed6d, green on a99d71e, no run since. Security STAGE scan: still the two failures (2c09781, d46ce88) from before #197; no run on the new main, so OPS.9 stays open |
| Docs | progress.md lacked CORE.8 3/3, TAX.7, CMP.25, VEN.10, THM.6, the EVT.3 fix and eight PR rows (added here). audit-trail.md already has `accounts.user.unlocked`, `accounts.user.unlocked_by_reset` and `vendor.self_certified`; `docs/dev/editor-roles.md` and `themes.md` exist. release.md lacked notes for the editor-role migration, the activation gate and the new tax territories (added here) |
| Hygiene | No credential-like literals, vendor names, hostnames, new TODOs, prints or sleeps in the diff. Still the `RemovedInWagtail90Warning` at `apps/cms/admin_views.py:30` (OPS.7) |

Findings and actions:

1. Minor. progress.md missed the merges above. Action: this update.
2. Minor. release.md had no STAGE effects for v0.1.3 (editor roles, activation gate, territories). Action: added here.
3. Minor, ops. OPS.9: no STAGE scan and no nightly since the last green gate; run both on bd01f8e before the tag if lane-a wants them as evidence. Action: for lane-a.
4. Minor. The seeded special-territory postcodes are marked for owner review (fewer rather than wrong); the platform owner extends them in the tax admin. Action: none.
5. Minor. The home page belongs to no editor role (a page grant inherits downwards), so only superusers edit it until CORE.18. Action: documented in editor-roles.md already.

Release candidate verdict: GO for v0.1.3 from bd01f8e. No blocker or major finding in the code, gates are green, and the STAGE effects are in release.md. Conditions: no other merge between this PR and the tag; apply the release notes on STAGE (grant `vendors.view_all_vendors` to existing staff, assign publisher roles after the migrate).
Open from check 10: item 1 closed; item 2 (Manager developer docs) still a proposed row; item 3 (OPS.9) open.

### Check 12 (2026-11-10, main at 734b4cc)

Scope: main at 734b4cc, seven merges since check 11 (#202, #201, #198, #200, #212, #213, #204; 95 files, about 4.9k lines, counted from 5522579). #221 (CAT.5, D-25) had not merged when the check ran. Review by lane-b in four passes.

| Area | Result |
|---|---|
| Code vs spec | VEN.22: form without password (`apps/vendors/application_views.py:81`), confirm and cancel act only on POST (`:113`, `:142`), reminder at 48 h and purge after 3 days as jobs (`apps/vendors/tasks.py:30`, `:49`, `services.py:2454`, `:2501`), MFA at first login left to CORE.4. CMS.12: sanitiser in `apps/cms/engine.py` and `apps/cms/services.py:961-1022`, `pre_save` receiver `apps/cms/signals.py:40`, connected at `apps/cms/apps.py:92`; F1 and F2 are plain tests (`tests/integration/test_hostile_input_outputs.py:183`, `:197`), only the F3 xfail (I18N.10) remains. ARC.17: no `einvoice_channel` field in models, admin, factory or seed; the only mentions are the guard test, migrations 0003 and 0018 and spec text. CORE.9 1/3 and 2/3: `ShopDashboardView` uses `self.scoped(Product)` (`apps/manager/views.py:93`), the profile service refuses non-owners (`apps/vendors/services.py:1866`) and suspended or closed vendors (`:1882`), audit `vendor.profile_changed` and the moderator email (`:1938-1940`); the staff revert (3/3) is not built. CORE.17 fixes: CSRF token passed to the language switcher (`apps/manager/templates/manager/layout.html:36`), `/manager/` redirect and 403 in `apps/manager/middleware.py:81-83`; CORE.23 command with tests. OPS.10: the workflow has cron and `workflow_dispatch`, writes the job summary (`.github/workflows/coverage-nightly.yml:82`) and uploads `coverage-report` (`:87`), no fail-under. 364 targeted tests green locally (application, manager fixes, shop profile, rich-text paths, hostile inputs) |
| Spec and docs | `make docs` leaves no diff. D-24 is recorded in 92-open-decisions; D-25 (#221), D-26 and D-28 (#218), D-27 (#222) and D-29 (#226) are still only in open PRs; D-30 to D-32 are in no open PR. audit-trail.md, background-jobs.md, rich-text.md and release.md were updated by the merges; the spec 01, 02, 04 and 12 tables match the code. No vendor names, hostnames or credentials in the diff (only `example.*` domains and `owner@example.org` in release.md). progress.md lacked these tasks and PRs (added here) |
| Hygiene | `makemigrations --check` clean. One leaf per app: accounts 0005, api 0007, cart 0001, catalog 0009, cms 0013, compliance 0002, core 0018, events 0003, shipping 0010, tax 0006, themes 0001, vendors 0024 (ingest, ledger, mcp, orders, payments, seo have no migrations). No new TODO, FIXME, print, breakpoint or sleep in non-test code. Test password literal `Attacker-chosen-1!` at `apps/vendors/tests/integration/test_application.py:167-169`. Still the `RemovedInWagtail90Warning` at `apps/cms/admin_views.py:30` (OPS.7) |
| Gates | `make check` pass (ruff, mypy strict over 920 files, 8 contracts kept and 0 broken, backlog freshness, Django checks, theme gates for base and ride). Main CI: success on e81a205, e4bd252, 167604f, 5610c6e and 068fd90 (e501e06 cancelled by the next merge); the run on the head 734b4cc was still in progress when the check ran. Full suite not run locally (CI is the gate). The coverage nightly has not run yet |

Findings and actions:

1. Minor. progress.md missed these merges. Action: this update.
2. Minor. The test password literal in the application tests is credential-like; ADR-0006 says to use `secrets.token_urlsafe()`. Action: lane-b replaces it in the next vendors PR.
3. Minor. CORE.9 3/3 (staff revert from the platform space) is open; the moderator notice goes to the operator support address until CORE.18. Action: none, tracked in the row.
4. Minor. D-25 to D-29 are decided but only in open PRs (#221, #218, #222, #226); D-30 to D-32 are in no open PR. Action: for lane-a, merge them or confirm the register.
5. Minor, ops. OPS.10: run `gh workflow run coverage-nightly.yml` once to close the done-when. OPS.9: a Security STAGE scan on 5522579 succeeded, so lane-a can close it.
Open from check 11: item 3 (OPS.9) can close on lane-a's word; the others are closed or accepted.

Verdict: GO with follow-ups. No blocker or major finding; the follow-ups are minor and none blocks a tag. Condition: CI on 734b4cc green.

### Coherence check 13 (release gate v0.1.4, main at 81a2f11)

Scope: main at 81a2f11, the merges since check 12's code base (734b4cc): #221 (CAT.5, D-25; merged before #228 but after check 12 ran, so not reviewed there), #220 (I18N.11) and #180 (EVT.5); #228 is docs. 32 files and about 1.5k lines from a8bf1a1 (check 12 merge), plus the #221 delta (15 files). Short release-gate check, delta only.

| Area | Result |
|---|---|
| Code vs spec | CAT.5 D-25: `build_product_slug` has no language or SKU (`apps/catalog/slugs.py:66`), migration `catalog/0010_d25_product_slugs.py` runs on historical models with a frozen slug copy pinned by a test (`tests/integration/test_migrations.py:168`), writes the audit entry, rerun is a no-op, reverse is `noop` (`:157`); old `{title}-{lang}` and `{title}-{lang}-{sku}` slugs answer a 301 through `OldProductSlugRedirectMiddleware` (`apps/catalog/middleware.py:3`); `rebuild_product_slugs --dry-run` exists (`management/commands/rebuild_product_slugs.py:23`). I18N.11: one mapping `{language: URL}` in `apps/core/i18n.py` (`hreflang_alternates:57`, `switcher_links:70`), used by catalog, CMS and event pages; secret-bearing URLs opt out with `@language_switch_home` (`apps/core/i18n.py:91`; `apps/accounts/action_link_views.py`, `apps/vendors/views.py:186`, `apps/vendors/application_views.py:113`, `:143`) and a guard test checks every such URL pattern (`apps/core/tests/integration/test_language_switch_secrets.py`); e2e `tests/e2e/test_language_switch_object.py`. EVT.5: six routes with `events:read` and `events:write` (`apps/api/v1/events.py:36-37`, `:211-303`), router mounted at `apps/api/v1/api.py:66`, vendor scoping through `get_for_actor_or_404` (`:28`), `dry_run` returns the changeset with 200 and writes nothing (`:239-251`), StreamField limited to vendor-safe blocks and sanitised drafts, no userinfo in `online_url` (`apps/events/engine.py:530`, `services.py:310`); 430 lines of API tests in `apps/api/tests/integration/test_v1_events.py`; `tests/tenancy_registry.py` extended |
| Spec and docs | Backlog and spec 09 and 03 updated by the PRs; D-25 recorded in 92-open-decisions. progress.md lacked I18N.11, EVT.5 and the #221 and #228 rows (added here). release.md already has the CAT.5 note; EVT.5 and I18N.11 effects added here |
| Hygiene | `makemigrations --check` clean; the only migration in the delta is `catalog/0010`. One leaf per app: accounts 0005, api 0007, cart 0001, catalog 0010, cms 0013, compliance 0002, core 0018, events 0003, shipping 0010, tax 0006, themes 0001, vendors 0024 (ingest, ledger, mcp, orders, payments, seo have none). No print, breakpoint, TODO, FIXME or sleep and no credential-like literal added in the delta. No new env variable or setting. The `RemovedInWagtail90Warning` at `apps/cms/admin_views.py:30` is still open (OPS.7) |
| Gates | `make check` pass (ruff, mypy strict over 928 files, 8 contracts kept and 0 broken, backlog freshness, Django checks, theme gates for base and ride). Main CI on 81a2f11: success (run 42876101506); the run on #220's merge also succeeded. Full suite not run locally (CI is the gate) |

Findings and actions:

1. Minor. progress.md lacked I18N.11, EVT.5 and four PR rows. Action: this update.
2. Minor. Check 12 did not cover #221 (it merged after the check ran). Reviewed here; no finding.
3. Minor, ops. `catalog/0010` changes every product URL: run `rebuild_product_slugs --dry-run` on STAGE before the deploy and check the count (release.md).
4. Minor. Carried over from check 12: the credential-like test password in the application tests (item 2 there) and CORE.9 3/3 are still open; none touches this delta.

Verdict: GO for v0.1.4 from 81a2f11. No blocker or major finding. Conditions: no other merge between this PR and the tag, run the slug dry run on STAGE before deploying, and have the owner run the STAGE checks listed in release.md ("v0.1.4 on STAGE"). EVT.5 is event code and needs human review.


### Coherence check 14 (main at f56549f)

Scope: main at f56549f, the merges since check 13 (81a2f11): #211 (THM.12 test), #229 (VEN.22 test), #217 (CMP.7), #237 (I18N.12), #214 (ORD.2), #240 (SEO.8 1/2) and #208 (CORE.9 2b); #232 is docs. 88 files and about 6.6k lines, mostly ORD.2 (10 migrations, models, engine, services, 7 test modules).

| Area | Result |
|---|---|
| Code vs spec | ORD.2 done-when "illegal transitions impossible (test)": `ORDER_TRANSITIONS`, `VENDOR_ORDER_TRANSITIONS` and `PRIVACY_TRANSITIONS` in `apps/orders/engine.py:112,127,224`, tests `test_an_illegal_order_transition_cannot_be_saved` and the vendor-order twin (`apps/orders/tests/integration/test_models.py:70,98`), plus database triggers and checks (`orders/0003`, `0005`, `0008`, `0010`) with `test_db_guards.py`. SEO.8 1/2: `FORCE_NOINDEX` setting (`mpmv/settings/base.py:60`, `stage.py:9`, env example), `ForceNoindexMiddleware` (`apps/seo/middleware.py:11`), `apps/core/indexing.py`, tests for listing, signed-in Manager page, `/robots.txt`, demo mode and the not-forced case (`apps/seo/tests/integration/test_force_noindex.py`); 2/2 correctly left open. I18N.12: `apps/accounts/middleware.py`, tests `test_preferred_language.py`, e2e switch test in `tests/e2e/test_manager_shell.py:202`. CMP.7: `tests/e2e/test_axe_audit.py`, any violation fails, artifact in CI; manual review and statement are human steps and stay open. CORE.9 2b: `apps/vendors/services.py` and `apps/manager/views.py`, `vendors/0025_vendor_logo`, `test_vendor_logo.py`, `test_shop_logo.py`; 3/3 (#231) open |
| Spec and docs | `make docs` leaves no diff and `make check` backlog freshness passes. D-35 is recorded in 92-open-decisions. D-29 (order retention) and D-30 are not: ORD.2 implements them (`tax_retention_until`, `privacy_status`, scrub gates, `docs/dev/audit-trail.md` cites D-29 and D-30) but they are only in the open PR #226, so spec 07 has no text for those fields yet. progress.md lacked ORD.2, SEO.8, I18N.12, CMP.7 and the PR rows (added here) |
| Hygiene | `makemigrations --check` clean. One linear chain per app, no duplicate numbers: accounts 0005, api 0007, cart 0001, catalog 0010, cms 0013, compliance 0002, core 0018, events 0003, orders 0010 (new, 0001 to 0010 chained, 0002 depends on catalog 0009 and vendors 0024), shipping 0010, tax 0006, themes 0001, vendors 0025 (new, depends on core 0017 and vendors 0024); ingest, ledger, mcp, payments and seo have none. No print, breakpoint, TODO, FIXME or credential-like literal added in the delta; no legacy or private names. One `time.sleep(1.0)` in the concurrent-counter test (`apps/orders/tests/integration/test_services.py:455`), see finding 3. New setting `MPMV_FORCE_NOINDEX` is documented in `deploy/server/mpmv.env.example`. The `RemovedInWagtail90Warning` at `apps/cms/admin_views.py:30` is still open (OPS.7) |
| Gates | `make check` pass (ruff, mypy strict over 967 files, import contracts kept, backlog freshness, Django checks, theme gates for base and ride). Main CI on f56549f: success. Full suite not run locally (CI is the gate) |

Findings and actions:

1. Major, spec. ORD.2 ships D-29 and D-30 behaviour (fiscal immutability, retention dates, privacy lifecycle, scrub gates) while the decisions are only in open PR #226 and no merged spec text describes them. Action for lane-a: merge #226 (and the D-30 text) so spec 07 and 92-open-decisions match the code. Not a code defect.
2. Minor. progress.md lacked ORD.2, SEO.8, I18N.12, CMP.7 and eight PR rows. Action: this update.
3. Minor. `time.sleep(1.0)` in the orders concurrency test makes the test slow and timing-dependent; replace with an event or a lock-wait query. Proposed row: ORD.2 follow-up.
4. Minor. CMP.7 `KEY_PAGES` lacks cart, checkout and product detail (they do not exist yet): add when ORD.4 and the product page land.
5. Minor. SEO.8 2/2 (owner toggle) and CORE.9 3/3 are open. The owner sets `MPMV_FORCE_NOINDEX=true` in STAGE's env (the STAGE settings already force it).
6. Closed from check 13: the credential-like test password (#229) is fixed.

Verdict: GO with follow-ups. No blocker; one major documentation gap (finding 1) that does not break the code. ORD.2, SEO.8 and CORE.9 2b are security or money code and need human review. Condition: CI on f56549f green.


### Coherence check 15 (main at 907fedd558d648dd1924b59232e78a6267d7d762)

Scope: main at 907fedd (merge of #234), the merges since check 14 (f56549f): #238 (VEN.29), #239 (I18N.11 sweep), #218 (TAX.3), #230 (CMP.5 2/4), #227 (spec D-31, D-32 rows) and #234 (CORE.25). #222, #236 and #245 (merged around check 14) also lacked progress rows and are added here. 185 files and about 11.4k lines, mostly TAX.3, CMP.5, CORE.25 and the I18N catalog.

| Area | Result |
|---|---|
| Code vs spec | VEN.29: `apps/vendors/tests/integration/test_storefront_rich_text.py` (legacy plain text as paragraphs, `plain_to_html` and the data migration idempotent), sanitiser and embed refusal in `apps/vendors/engine.py` and `services.py`, `vendors/0026`. TAX.3: `resolve_goods_tax` and `is_outside_vat_territory` in `apps/tax/engine.py`, `test_goods_tax.py` (T15 included; T4 and T10 to T13 wait for TAX.4, T16 to T18 belong to later rows, the T18 test is skipped by design). CMP.5 2/4: `apps/compliance/tests/integration/test_notice_and_action.py` (good-faith statement, reasoned decision with a known outcome, reviewer workflow); the statement of reasons for vendors and the 30-day termination notice (3/4 and 4/4) are not built, so the row stays partial. CORE.25: `apps/accounts/tests/integration/test_session_levels.py` covers refusal in restricted mode, no elevation without a passkey or with TOTP, idle (16 min) and cap end, logout end, stolen session, do-it-once spent once (parallel requests included), grant bound to action and parameters, tuned timers without code change, group 3 with UV passkey; friction report test; url sweep test. I18N.11 sweep (#239): prefixed Sell with us, anonymous language change, reserved prefixes, rate limit tests |
| Spec and docs | D-26, D-28, D-34, D-35 and D-32 are in 92-open-decisions (D-27 and D-24, D-25 too). D-29, D-30, D-31 and D-33 are still only in open PR #226: code (`apps/orders`, `apps/orders/tz.py`) and spec 01 ARC.28 cite them. `make docs` check passes (backlog and public forms register fresh). progress.md lacked VEN.29, TAX.3, CMP.5, CORE.25, SEC.13, I18N.7 and nine PR rows (added here) |
| Hygiene | `makemigrations --check` clean. Chains linear: core 0018 to 0020 (0019 CMP.5, 0020 session policy), accounts 0005 to 0007, tax 0005 to 0007, vendors 0024 to 0026, compliance 0001 to 0003; no duplicate numbers in any app (orders 10, shipping 10, catalog 10, cms 14, events 3, api 7, cart 1, themes 1). No TODO, FIXME, breakpoint or sleep added; `print` only in `tools/`. No credential-like literal added (one `SIGNED_TOKEN` label with a noqa, not a secret). No legacy or private names found. `tools/i18n_catalog.py check` runs inside `make check` and passes |
| Gates | `make check` pass (ruff, mypy strict, import contracts, theme gates, backlog and forms freshness, i18n check, Django checks, makemigrations). Main CI on 907fedd: see the PR for the final state. Full suite not run locally |

Findings and actions:

1. Major, docs (carried over from check 14, wider now). D-29, D-30, D-31 and D-33 are cited by merged code (ORD.2 retention and privacy lifecycle, ARC.28 time zones) but live only in open PR #226. Action for lane-a: merge #226.
2. Minor. CMP.5 is partial: 2 of 4 done; statement of reasons and the 30-day termination notice remain. No row change needed, the PR rows record 1/4 and 2/4.
3. Minor. progress.md lacked six task rows and nine PR rows. Action: this update.
4. Minor, carried over: `time.sleep(1.0)` in `apps/orders/tests/integration/test_services.py` (proposed ORD.2 follow-up), CMP.7 `KEY_PAGES` lacks cart, checkout and product detail, SEO.8 2/2 and CORE.9 3/3 open, `RemovedInWagtail90Warning` at `apps/cms/admin_views.py:30` (OPS.7).
5. Info. CORE.25 is a trial (D-32 review after STAGE): the owner reviews the friction report once STAGE runs it. TAX.14 (vendor territory in `TaxContext`, D-26) is a P2 follow-up, not in this delta.

Verdict: GO with follow-ups. No blocker. VEN.29 (rich text), TAX.3 (tax), CMP.5 2/4 (compliance) and CORE.25 (sessions, passkeys) are security, tax or compliance code and need human review.

### Coherence check 16 (release gate v0.1.5, main at dc668e15364bc347d850766cb8d0ff815e027705)

Scope: main at dc668e1 (merge of #250), the merges since check 15 (907fedd): #226 (spec D-29), #251 (SEC.14), #224 (TAX.5, D-28), #233 (CMP.5 3/4), #250 (OPS.2); #247 (ORD.4) also lacked rows.

| Area | Result |
|---|---|
| Code vs spec | SEC.14: `apps/cms/middleware.py` (per-IP and per-restriction rate limit, then captcha) with `apps/cms/tests/integration/test_page_restriction_protection.py`. TAX.5 / D-28: VIES consultation log with requester, tests in `test_vies_consultation_log.py`, Madeira and Azores seed (`tax/0009`). CMP.5 3/4: `StatementOfReasons`, art. 18 notification, tests in `test_statement_of_reasons.py`. OPS.2: `apps/core/errortracking.py`, JSON logs, readiness and alerts, tests `test_errortracking.py`, `test_observability_logs.py`. ORD.4: `test_place_order.py`. |
| Spec and docs | D-29, D-30, D-31, D-32, D-34, D-35 are all in 92-open-decisions now (carried-over major finding from checks 14 and 15 is closed by #226). D-33 appears in no spec or code file, only in earlier progress notes: no action. Task rows and PR rows for the six merges added here. |
| Hygiene | `makemigrations --check` clean. Chains linear: core ..0020, accounts ..0007, tax ..0009, vendors ..0026, compliance ..0004, orders 0001..0011. No credential-like literal, no private name in versioned files; `python tools/i18n_catalog.py check` clean. |
| Gates | `make check` pass on dc668e1. Main CI on dc668e1: see the PR. Full suite not run locally. |

Findings and actions:

1. Info. The v0.1.5 STAGE notes (user-visible changes, migrations, new environment variables, data migrations, deploy steps) are in the PR body.
2. Minor, carried over: CMP.5 stays partial (3 of 4, 30-day termination notice open), `time.sleep(1.0)` in the orders concurrency test, CMP.7 `KEY_PAGES` lacks cart, checkout and product detail, SEO.8 2/2 and CORE.9 3/3 open, OPS.7 Wagtail deprecation warning.
3. Info. CORE.25 is a trial (D-32): the owner reviews the friction report after STAGE.

Verdict: GO. No blocker. SEC.14, TAX.5, CMP.5 3/4, ORD.4 and OPS.2 are security, tax, compliance or order code and need human review.

### Coherence check 17 (main at 1e6dab6b37dc62467dfcc45898fc8cc45133fac1)

Scope: main at 1e6dab6 (merge of #255), the merges since check 16 (9d50a3a): #248 (VEN.31 1/2), #253 (CORE.18 1/2), #249 (ORD.6), #254 (SHP.3), #255 (TAX.4).

| Area | Result |
|---|---|
| Code vs spec | ORD.6: engine, service and Manager pages with unit, integration and e2e tests, tenant isolation and audit covered. SHP.3: cases 8 to 13 in `test_quote_shipping.py` (unit and integration). TAX.4: T8 to T13 in `test_shipping_tax.py` (27 tests, written first). CORE.18 1/2: roles, matrix, seeding and drift check with tests; the 200/404 per page cell check waits for the platform sections (part 2). VEN.31 1/2: images with 404 and audit tests, but the spec row is not on main (see finding 1). |
| Spec and docs | D-03 (TAX.4) and D-06 (SHP.3) exist in 92-open-decisions and are still open: TAX.4 is on the D-03 default and fails closed on other treatments; SHP.3 reads the over-band policy from the method. D-34 is decided. `WeightBand.surcharge_net` is used by SHP.3 but not defined in spec 06. |
| Hygiene | `makemigrations --check` clean. Chains linear: manager 0001..0002 (new app), orders 0001..0011, shipping 0001..0010, tax 0001..0009, vendors ..0026, core ..0020, accounts ..0007, compliance ..0004, cms ..0014. No credential-like literal, no private name found in the five diffs. |
| Gates | `make check` pass on 1e6dab6. Main CI on 1e6dab6: success. Full suite not run locally. |

Findings and actions:

1. Major. VEN.31 code is on main but its spec row is only in the open PR #244, so the task has no row in `docs/spec` or the backlog (spec first rule). Merge #244 soon; the VEN.31 row here points to it.
2. Minor. SHP.3 needs `WeightBand.surcharge_net` defined in spec 06 (next spec PR). ORD.6 lacks a customer notification on shipped: propose a follow-up task. VEN.31 follow-ups: page-scoped CSP hash for Draftail styles (lane-a), documents chooser (2/2), a link dialog for external links.
3. Minor, carried over: CMP.5 3 of 4, `time.sleep(1.0)` in the orders concurrency test, CMP.7 `KEY_PAGES` lacks cart, checkout and product detail, SEO.8 2/2 and CORE.9 3/3 open, OPS.7 Wagtail deprecation warning.
4. Info. TAX.4 and SHP.3 are not wired to checkout yet: ORD.3 and SHP.4 pass the shipping rate and goods decisions. D-03 and D-06 stay open for the advisor and the owner.

Verdict: GO with follow-ups. One major spec-gap finding (VEN.31 row in #244), no blocker. VEN.31 (media, tenancy), CORE.18 (permissions), ORD.6 (orders) and TAX.4 (tax) need human review.

### Coherence check 18, release gate v0.1.6 (main at 76ae69322d4c9032ef98afac895816605122958c)

Scope: main at 76ae693 (merge of #244), the merges since check 17 (1e6dab6): #256 (CMP.4 1/2), #252 (OPS.5), #262 (check 17), #244 (spec: VEN.31 row, ARC.29 row).

| Area | Result |
|---|---|
| Code vs spec | CMP.4 1/2: export and erasure on seed orders covered by 22 integration tests in `test_gdpr.py` (concluded orders restricted, open order keeps the request pending, dry run, idempotent, audit without personal data, outbox, sessions, staff rights, a guard test that every user foreign key has an export and erasure decision), plus `test_audit_actor_pseudonymised.py` for `core/0021`. The closed vendor owner (Q-16), the sub-processor register and the retention job with a frozen clock are part 2. OPS.5: app-level check tested (unit and integration, trusted proxy and tunnel cases, the normal 404, no slash redirect, Wagtail admin unaffected); the proxy matcher is in the Caddyfile; the STAGE half of the done-when (404 from outside, works through the tunnel) is verified at deploy. |
| Spec and docs | Check 17's major finding is closed: the VEN.31 row is in spec 04 and in `90-backlog.md` and `backlog.csv` (#244); #244 also adds ARC.29 (spec 01, not started). `make docs` freshness passes inside `make check`. `docs/dev/admin-access.md`, `docs/dev/release.md` and `docs/dev/audit-trail.md` match the code. |
| Hygiene | `makemigrations --check` clean. New since v0.1.5: compliance 0005, core 0021, manager 0001..0002 (new app). Chains linear: compliance 0001..0005, core 0001..0021, manager 0001..0002, orders 0001..0011, vendors ..0026, accounts ..0007, tax ..0009, shipping ..0010, cms ..0014, catalog ..0010; no duplicate numbers. Only documentation (RFC 5737) and private test addresses, no credential-like literal, no private name in the four diffs. |
| Gates | `make check` pass on 76ae693. Main CI on 76ae693: success. Full suite not run locally. |

Findings and actions:

1. Minor. CMP.4 1/2 is services only: no customer page, staff action or command calls `export_customer_data` or `anonymise_customer` yet. Action: part 2 or a follow-up row adds the adapter.
2. Minor. `PRIVACY_PEPPER` (key of the lockout audit hash) is read with a `SECRET_KEY` fallback but is not in `settings/base.py` or the env example, so rotating `SECRET_KEY` changes new lockout keys. Action: declare it as an optional variable in a later PR.
3. Minor. VEN.31 row asks for the page-scoped CSP hash for the editor styles; still open with the documents chooser (2/2).
4. Minor, carried over: SHP.3 `WeightBand.surcharge_net` not in spec 06, ORD.6 customer notification on shipped, CMP.5 3 of 4, `time.sleep(1.0)` in the orders concurrency test, CMP.7 `KEY_PAGES`, SEO.8 2/2 and CORE.9 3/3 open, OPS.7 Wagtail deprecation warning.
5. Info. `core/0021` disables the audit append-only trigger inside its transaction (`ALTER TABLE`), so the migrating database role must own `core_auditlog`; take a backup first. If the SSH tunnel still gets 404 on STAGE, check that the address Caddy sees for a loopback connection is loopback (container port publishing can change it).

Verdict: GO. No blocker, no major. CMP.4 (compliance, audit rewrite) and OPS.5 (security) need human review.

### Coherence check 19 (main at c9ac9d9b09f7782768dcee4dd335a4c4b00834b7)

Scope: main at c9ac9d9 (merge of #274), the merges since check 18 (01c21487): #269 (CORE.18 fix), #257 (ORD.9 1/2), #258 (ING.1), #259 (CAT.20 1/3), #274 (CORE.18 follow-up).

| Area | Result |
|---|---|
| Code vs spec | ORD.9 1/2: every done-when item maps to a test in `test_notifications.py` (once per event, language and EN fallback, rollback and dry run send nothing, rendering in all languages, non-fiscal label, no payment data, restricted orders get no email); `place_order` now calls `notify_order_placed`, so the ORD.4 follow-up in the PR is closed. The PDF confirmation is part 2. ING.1: seven done-when items mapped to tests (ambiguous SKU becomes a blocking review item, run machine, mapping precedence, tenant isolation, import contract). CAT.20 1/3: Wikidata and https refusal, pending brand 404, idempotent data migration, duplicate detection at model level; merge and redirect, proposals and limit, audit, JSON-LD are 2/3 and 3/3. CORE.18 fix: regression test for the missing groups before the seed migration; #274 adds themes check tests and the CI upgrade-path step. |
| Spec and docs | Rows ORD.9, ING.1 and CAT.20 exist in specs 07, 08 and 03 and in `90-backlog.md`. D-30 (ORD.9 non-active orders) is decided. `docs/dev/audit-trail.md` lists the ingest actions and the ORD.9 outbox templates; `docs/dev/platform-roles.md` unchanged and still generated. No decision referenced by the delta is open. #274 has no backlog row (see finding 2). |
| Hygiene | `makemigrations --check` clean. New: catalog 0011, ingest 0001 (new app). Chains linear, no duplicate numbers: accounts ..0007, api ..0007, catalog ..0011, cms ..0014, compliance ..0005, core ..0021, events ..0003, ingest 0001, manager ..0002, orders ..0011, shipping ..0010, tax ..0009, themes 0001, vendors ..0026. No TODO, FIXME or print in the delta; no private name. One credential-like literal in a test (finding 3). |
| Gates | `make check` pass on c9ac9d9 (exit 0). Main CI on c9ac9d9: success. Full suite not run locally. |

Findings and actions:

1. Minor. ORD.9 1/2: de, fr and es mail bodies are English; vendors have no language, so vendor mails use the platform language; delivered, quote, return, refund, withdrawal, payout, reminder and staff anomaly emails wait for their flows. Action: I18N.7 slices and the follow-up rows.
2. Minor. #274 and the CORE.18 migrate-checks fix have no backlog row. Action: lane-a adds the proposed CORE row (database checks never block migrate while the app has pending migrations; a `roles.py` change ships a sync migration). Review findings 1 (manager skip while any manager migration is pending) and 3 (full migrate test for manager) from #269 stay open. Parity smoke that deploys the previous release first is a longer-term item.
3. Minor. `apps/ingest/tests/integration/test_services.py:323` builds a `token-xxxxxxxx` literal; CI secret scans pass, but project rules want `secrets.token_urlsafe()`. Action: replace in the next ING PR.
4. Minor. CAT.20 1/3 leaves `Manufacturer` unaudited until 2/3 (stated in `apps/catalog/apps.py`); ING.1 write runs need `SupplierSource.write_enabled` and nothing sets it yet (adapters in ING.2).
5. Minor, carried over from check 18: CMP.4 1/2 services have no adapter; `PRIVACY_PEPPER` is not declared in settings or the env example; VEN.31 page-scoped CSP hash and documents chooser (2/2); SHP.3 `WeightBand.surcharge_net` not in spec 06; ORD.6 customer notification on shipped (now covered by ORD.9 `vendor_order_shipped`, to close once confirmed on STAGE); CMP.5 3 of 4; `time.sleep(1.0)` in the orders concurrency test; CMP.7 `KEY_PAGES`; SEO.8 2/2 and CORE.9 3/3 open; OPS.7 Wagtail deprecation warning (still printed by `make check`).
6. Info. #269 was a deploy blocker for v0.1.6: CI missed it because the test database is fully migrated before any check runs; the "Upgrade path" step of #274 now covers that path.

Verdict: GO with follow-ups. No blocker, no major. ORD.9 (orders, personal data) and the CORE.18 check changes (permissions) need human review.
