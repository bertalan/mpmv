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
| CORE.4 | done | #22, #32, #270 | Mandatory MFA for staff, superusers and every vendor member (owners and staff since #32, owner decision): TOTP, passkeys, recovery codes, step-up. #270: a POST that is sent to the MFA step keeps a safe return URL (`apps/accounts/http.py` `return_url`, 15 hostile Referers, auth and MFA deny-list, GET self-return). Redirect target, security, needs human review |
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
| OPS.4 | done | #48, #59, #524, #525, #526, #527 | Weekly Dependabot update PRs for uv, GitHub Actions and Containerfile images (minor and patch grouped, majors and Django/Wagtail alone, cooldown), full CI on update PRs, CycloneDX SBOMs of the Python environment and the image in CI and attached to releases; Dependabot bumps: actions/cache 6, uv 0.13.8, python image digest, python-minor-patch group |
| OPS.15 | spec only | #670 | Automated updates for what Dependabot misses (Quadlet images by tag and digest, the CI and `make db` `postgres` image, pre-commit hooks) by a scheduled workflow with the repository token and no third-party app (owner decision Q-82); `docs/dev/dependency-updates.md` points to it |
| OPS.16 | spec only | #670 | Release image SBOM and vulnerability scan (a fixable critical or high finding fails the release unless an exception has an expiry date), signed SBOM and provenance attestation, weekly re-scan that opens issues, evidence kept per release (owner decision Q-82) |
| ARC.8 | done | #51 | Feature flag registry with code, environment, staff and per-vendor values; kill switches for checkout, import and payments seeded on; `flag_enabled` helper; audited services with dry-run; admins with step-up on kill switches |
| CAT.2 | done | #53 | Category and term trees (treebeard), configurable taxonomies with typed term attributes, brands, per-language names, slugs and SEO texts; product links; admin CRUD with translations |
| SHP.12 | done | #54 | Several shipping methods per vendor and zone (VendorZoneRate renamed in place), vendor zones overriding platform zones, country groups, shared postcode grammar in apps/core/postcodes.py, per-method free-shipping threshold and stored VAT treatment |
| TAX.2 | done | #52 | Regime change workflow: owner request, staff approval or rejection with the review permission checked in the service, step-up, one transaction for the new profile version and fiscal regime, history. Tax code, flagged for careful human review |
| TAX.11 | done | #74 | Tax territory postcode patterns use the shared grammar in apps/core/postcodes.py; data migration rewrites old `a-b` ranges to `a..b`; `postcode_in_territory`. Tax code, flagged for careful human review |
| SHP.2 | done | #73 | Legacy zone cases 1-7 as regression tests, destination aliases, ISO-validated `Destination`, postcode patterns parsed once per zone snapshot |
| SHP.13 | done | #77, #926, #944 | Shipping configuration file format v1 (JSON and CSV), audited export/import with per-row report and all-or-nothing writes, `ShippingTemplate` with one standard template, `shipping_config` command |
| CORE.14 | done | #75 | Vendor API keys limited to vendor-grantable scopes, suspended vendors read-only and closed vendors refused, per-key audit when a vendor is deleted. Security code, flagged for careful human review |
| CAT.12 | done | #76 | Audited product and variant edit and delete services (fiscal category, sales mode, weight, dimensions), vendor and price rules enforced in the service, admin routed through them |
| CAT.14 | done | #80, #94, #116 | Catalogue integrity rules |
| CAT.3 | done | #84, #96, #116 | Typed attributes, translated values and variant axes |
| CAT.5 | done | #81, #116, #221 | SKU-free product slugs, SlugHistory and 301 redirects. #221 (D-25): the slug is the title without the `-{lang}` suffix, `catalog/0010` reslugs existing products on the historical models with audit, old slugs answer one 301 |
| CAT.21 | done | #221 | Formerly CAT.5b: `catalog/0010` reslugs existing products to the D-25 slug through the slug service (dry run, audit, history rows, one 301 per old slug) |
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
| SHP.15 | done | #519 | Special areas completed (shipping 0014): Ionian islands, Sporades and Skyros, Kythira and Antikythira, Saronic islands (Methana stays mainland), Northern Ireland `BT*` as remote |
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
| ARC.10 | done | #150, #480 | Synthetic data seed (`make seed`); #480 publishes one sales terms version per demo vendor and onerous clauses on the demo Terms of sale pages |
| CMS.10 | done | #126, #275 | Product grid block. #275 adds the SEO.7 `ItemList` through `graph_nodes` and places the grid in the blog, term landing, vendor landing and event bodies |
| CMS.11 | done | #275 | `product_cards` block (grid or row of hand-picked products, listed and page-language only, no price for on-request items) and the product chooser with search (name in any language, SKU, brand, vendor) and vendor, brand and category filters, scoped to the member's own vendors; vendor event bodies allow cards of own products and grids sourced from the vendor itself. Migrations cms 0015, events 0004. Security-relevant scoping, needs human review |
| CORE.15 | done | #140, #163 | Secure action links, review follow-ups |
| ARC.23 | done | #153 | Release gate with production parity |
| EVT.13 | done | #144 | Map picker for event locations |
| I18N.9 | done | #142 | Customer legal and checkout pages per language |
| I18N.11 | done | #220, #239 | Language switch stays on the same object, from the one `{language: URL}` mapping shared with hreflang (`apps/core/i18n.py`); untranslated object goes to the target home with a notice once; pages with secret-bearing URLs opt out |
| EVT.5 | done | #180 | Vendor events API `/api/v1/events` (list, get, create, update, submit, cancel), scopes `events:read` and `events:write`, vendor-scoped keys, dry run, RFC 9457 errors. Event code, needs human review |
| ORD.2 | done | #214 | `Order`, `VendorOrder`, `OrderLine` with price, tax and commission snapshots, per-vendor numbering with dry-run preview, order and vendor-order state machines in `apps/orders/engine.py` enforced by model validation and database triggers (illegal transition cannot be saved), fiscal snapshot immutability (D-29), tax retention and privacy lifecycle with scrub gates (D-30), `paid_at`, legacy id for MIG.4. Migrations `orders/0001` to `0010`. `OrderEvent` and `ReturnRequest` are not in this row. Order and money code, needs human review |
| SEO.8 | done: 1/2 (#240), 2/2 in parts (#271, #292, #899, #901) | #240, #271, #292, #899, #901 | #899 (check 47): per-page fields as the pure `apps/seo/meta_engine.py` (title: SEO title, page title; description: search description, the page `summary`; image: page `seo_image`, main image, `SeoSettings.default_image`, operator logo), the `page_social_meta` and `page_description_meta` tags print og and twitter meta in the base layout (ride inherits), `noindex` stays the per-page robots override and `MPMV_FORCE_NOINDEX` still wins (migrations `cms/0028`, `seo/0003`). #901: `services.opening_checklist()` lists the open go-live steps on the SEO settings page and as a panel on the editors' home page, empty under the environment floor; one audit entry per toggle is pinned by a test. #292: go-live indexing warning and the operator `Organization` node (logo, `sameAs`, `contactPoint`) from `current_operator()`; the operator settings page has the CORE.25 session gate (D-32). 2/2 (#271, partly): per-site `SeoSettings` indexing toggle and panel, the environment floor and demo mode win over a stored allow, a disallowed Host fails closed, audit `seo.settings.configured`, sitemap line in `robots.txt` only when indexing is allowed (migration `seo/0001`). 1/2 `MPMV_FORCE_NOINDEX` (true in STAGE settings, implied by demo mode): robots meta, `X-Robots-Tag` header (`ForceNoindexMiddleware`) and `/robots.txt` all noindex, listings keep their rules when not forced. Security-relevant, needs human review |
| I18N.12 | done | #237 | A signed-in user's `preferred_language` applies on unprefixed areas (Manager, account, admins) through a middleware after `AuthenticationMiddleware`; prefixed public URLs ignore it. Manager strings stay English until I18N.7 |
| CMP.7 | partial: automated audit in CI; manual review and statement open | #217 | axe (wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa) on the key public pages in the e2e suite (`tests/e2e/test_axe_audit.py`), any violation fails, report uploaded as the `e2e-axe` artifact. Cart, checkout and product detail are not yet in `KEY_PAGES` |
| VEN.29 | done | #238 | Storefront description and the three policies are Wagtail rich text (D-34): sanitised on save and on render, embeds of another vendor's media refused and hidden, legacy plain text rendered as paragraphs, idempotent data migration (`vendors/0026`), editors scoped to the Manager form, e2e screenshot. Security (rich text), needs human review |
| TAX.3 | done | #218 | `resolve_goods_tax` pure function in `apps/tax/engine.py` with the T1 to T15 goods case table (`apps/tax/tests/unit/test_goods_tax.py`; T4 and T10 to T13 wait for TAX.4, T16 to T18 belong to their own rows), exemption-nature codes (D-26, D-28), T15 through `is_outside_vat_territory`; migration `tax/0007`. Tax code, needs human review |
| CMP.5 | done: 4 of 4 (contacts, terms and ranking pages; notice and action; statement of reasons; 30-day termination notice) plus the versioned terms clauses (a) to (h), reworded after the owner's legal review of 2026-11-13 | #222, #230, #233, #343, #291, #426, #616, #668 | #426: clause (f) states the refund fee as the provider's actual cost plus the plan surcharge, rounded up to the euro, and has no sentence letting the vendor pass it on to consumers (Codice del consumo artt. 56-57); the spec rows now require the separate specific approval of the onerous clauses (art. 1341 comma 2 c.c.) at vendor onboarding (spec only, VEN.13 and VEN.14 build it). #291: the 30-day termination notice for business users (`compliance/0010`), orders of a terminated vendor frozen. Draft wording that still needs counsel (CMP.24). Earlier: `compliance/terms_clauses.py` (#343), 1/4 migration `core/0019`, 2/4 `compliance/0003`, 3/4 `compliance/0004`. #616 (spec only): vendor clause (m) "Limitazione, sospensione e cessazione" and the "(m)" reference in clause (l); the Italian wording of (m) was a proposal; #668 (spec only): counsel approved (m) on 2026-11-18 with one addition in paragraph 3 (the reasons reach the vendor on a durable medium without undue delay, also when the 30 days' notice does not apply, P2B art. 4(4)); (m) is a clausola vessatoria listed by letter and title in the second signature block next to (l); one new contract version carries (l) and (m) with 15 days' notice and one new double signature (the code that builds the version is VEN.13 and VEN.14); CMP.40 (placeholder publish guard) is spec only. Compliance code, needs human review |
| CORE.25 | done | #234 | Two session levels (D-32): `restricted` default, a passkey window (then called operational mode; renamed by #393, see the CORE.25 wording row) from a fresh passkey, per-action "do it once" grants stored in the database and spent once (`accounts/0006`, `0007`), group 3 actions need their own fresh user-verified passkey, timers and action lists in `SessionPolicy` (`core/0020`), start, end and prompts audited, per-user per-day report. Security code, needs human review |
| SEC.14 | done | #251 | Rate limit (per IP and per restriction) and captcha on Wagtail page view restriction forms; the interim block is lifted. Declared in the public forms register. Security code, needs human review |
| OPS.2 | done | #250 | JSON logs, error tracker interface (`MPMV_ERROR_TRACKER`: none or log), `/healthz` and `/readyz`, alert hooks (disk space, WAL archiving, backup age). New `MPMV_OPS_*` variables in the server env example |
| ORD.4 | done | #247 | Transactional `place_order` with `StockReservation` (migration `orders/0011`) and price verification, concurrency test on stock 1. Order code, needs human review |
| VEN.31 | partial: 2 of 2 built (images, documents), but a document cannot be linked from a storefront text until ARC.15 (owner decision 2026-11-11) | #248, #244, #264 | 2/2 (#264): documents are chosen and uploaded into the vendor's own collection (`upload_storefront_document`, `vendor.media_uploaded` with `after.document`), the editor has no document link and a save refuses one, external links get `rel="noopener noreferrer nofollow ugc"`. Spec row now depends on ARC.15. 1/2: Manager image chooser and upload into the vendor's own collection `vendor-<id>` (`apps/manager/media.py`, section `shop.media`, owner only): crafted collection or foreign image id answers 404 and is audited (`vendor.media_denied`), `upload_storefront_image` with SEC.2 checks, own-work licence, hourly limit, `vendor.media_uploaded` audit; `link` feature dropped from the editor and `linktype="page"` refused on save. Spec row added by #244 (spec 04). Security code (media, tenancy), needs human review |
| CORE.18 | done: 2 of 2 (roles and matrix, then the assignment service and the Staff and roles page) | #253, #265, #737, #852 | 2/2 (#265): `manager/staff_roles.py` assigns and revokes platform roles (incompatible pairs refused unless the audited one-person-team flag is on, the last owner never removed, a reason and an email to the other owners when an owner gives themselves a role, step-up in the view, audit `manager.staff_role.grant` and `.revoke`) and the page `manager:platform:staff_roles` (in the help sweep). Permissions code, needs human review. 1/2 (#253): `apps/manager/roles.py` (five roles, incompatible pairs, 17-section matrix, role membership never `has_perm`), unmanaged `PlatformAccess` permissions (`manager/0001`), idempotent `sync_role_groups` data migration (`manager/0002`), system checks `manager.E003` (static) and `manager.E004` (group drift; #269: silent until `manager/0002` is applied, so it cannot block the migration that seeds the groups; #274: the themes check also tolerates pending migrations and CI has an "Upgrade path" step), generated `docs/dev/platform-roles.md`. Part 2: Staff and roles UI, assignment service, step-up, audit, one-person team. Permissions code, needs human review; #737: the vendor-approval gate (`platform.vendor_approval`) on `approve_claim`, checked before the review page and the decision |
| ORD.6 | done | #249 | Vendor fulfilment from the Manager shop space: orders list and detail, accept, ship with tracking, delivered (`fulfil_vendor_order`, engine rules, tracking validation, audit row per step), tenant isolation tests, e2e screenshots. No model change. Customer notification on shipped is not in the row. Order code, needs human review |
| SHP.3 | done | #254 | `quote_shipping` pure engine and service with Rates, IndicativeRates, NeedsQuote and Blocked outcomes, over-band policy, pickup, free-shipping threshold per method, special areas; cases 8 to 13, 15, 16, 19. No migration. `WeightBand.surcharge_net` is not yet defined in the spec |
| TAX.4 | done | #255 | `resolve_shipping_tax` with proportional split by goods net (ROUND_HALF_UP, remainder on the largest share), `TaxPortion`, fail-closed `UnsupportedShippingTreatment` for non-default treatments; T8 to T13 plus T1, T7, T14. D-03 stays open for the advisor (default: shipping follows the goods). Tax code, needs human review |
| CMP.4 | partial: 2 of 3 (customer export and anonymisation; sub-processor register, retention job, self-service and staff views); erasure for a closed vendor's owner (Q-16) open | #256, #266, #337 | #337 (follow-up, L-3): `PRIVACY_PEPPER` (`MPMV_PRIVACY_PEPPER`) is declared in the settings and both env examples, the check `core.E901` refuses an empty or reused value on LIVE and STAGE, and the email suppression list `SuppressedEmail` (`compliance/0009`). 2/3 (#266): `apps/compliance/retention.py` and `run_retention [--dry-run]` with the nightly task `compliance.run_retention` (uses `restrict_order`, `anonymise_order` and `gdpr.anonymise_customer`, one `compliance.retention_run` audit row with counts only), reviewed register `subprocessors.py` and public page `/privacy/sub-processors/` (wording to be confirmed before LIVE), `/account/privacy/` for export and erasure (own account only, POST, rate limit, fresh second factor for erasure when the account has one) and a staff list with a fixed reason list (`compliance.erasure_processed`); no migration. Vendor-linked users are refused (`VendorLinkedUser`) until part 3. The frozen-clock test only lists candidates (the database trigger checks its own clock). 1/3 (#256): `apps/compliance/gdpr.py`: `export_customer_data` (articles 15 and 20, JSON) and `anonymise_customer` (D-29, D-30) with dry run, idempotent, audit rows without personal data; concluded orders go to `restricted_tax_only` through ORD.2, open orders keep the request pending (`ErasureRequest`, `compliance/0005`), queued mail cancelled, sessions and staff rights purged. Audit actor label by public id; lockout audit key as a keyed hash; `core/0021` rewrites old customer actor labels and lockout keys (one-off, the documented append-only exception). Services only, no UI or command yet. Compliance code, needs human review |
| OPS.5 | done (STAGE verification at deploy) | #252, #278 | Technical Django admin loopback only (Q-18, MQ-24): Caddy `@admin_denied` matcher plus `AdminNetworkAllowlistMiddleware` (fail closed, the normal 404, no slash redirect), `MPMV_ADMIN_ALLOWED_NETWORKS` in both env examples, system checks `core.E003` and `core.W002`, SSH tunnel procedure in `docs/dev/admin-access.md`. Security code, needs human review |
| ORD.9 | done: 3 of 3 (emails, delivered and cancelled emails, then the PDF per-vendor non-fiscal confirmation: ADR-0010 #298, built in #304) | #257, #276, #298, #304 | 3/3 (#304): `orders/documents.py` and `pdf_render.py` (WeasyPrint on the fly, ADR-0010) with a URL fetcher that refuses unsafe hosts, and a guard that resets Pillow `LOAD_TRUNCATED_IMAGES` after WeasyPrint sets it process-wide. 2/3 (#276): delivered and cancelled emails (customer, vendor, vendor notice) hooked to the transitions, rollback, dry run and vendor-leak tests. 1/3 (#257): `apps/orders/notifications.py`: order received, payment confirmed, one non-fiscal confirmation per vendor, vendor new order, vendor order shipped. Idempotent per event (outbox key), queued in the caller's transaction (rollback or dry run sends nothing), nothing for non-`active` orders (D-30), language from the order snapshot with EN fallback. Hooks in `transition_order(... "paid")` and `transition_vendor_order(... "shipped")`; `place_order` calls `notify_order_placed`. Only the non-fiscal label is translated in all five languages; the de, fr and es body text is English (I18N.7 slices). Orders code with personal data, needs human review |
| ING.1 | done | #258 | `apps/ingest` (`ingest/0001`): `SupplierSource`, `SourceItem`, `SourceObservation`, `ProductDraft`, `ImportRun`, `ReviewItem`, `MappingTable`; pure `engine.py` (run status machine, SKU match exact, alias, ambiguous, new; mapping precedence; payload hash); audited services with `dry_run`; staff-only admin; import contract "ingest depends only on catalog, vendors, accounts and core"; tenancy registry. Adapters, jobs and publish are ING.2 and later |
| CAT.20 | done (the product-side JSON-LD that reuses the brand `@id` is SEO.7) | #259, #280, #284 | 3/3 (#284): brand page content, JSON-LD `@graph` with the Brand and Manufacturer nodes (https links only, one `@id` for all languages), staff logo upload with sanitising, page redirects in one hop after a merge (catalog `0014_brand_page_help_texts`). 2/3 (#280): `apps/catalog/brands.py` with vendor proposals, the pending limit (`MPMV_BRAND_PENDING_LIMIT`, default 20, an environment setting, not a stored platform setting), batch proposals per import run with size caps (`MPMV_BRAND_BATCH_LIMIT`, `MPMV_BRAND_BATCH_PENDING_LIMIT`), approve, reject (rejection notice to every affected vendor through the outbox), merge with permanent redirects, advisory lock on brand names, all audited, `BrandBatch` (catalog `0013_brand_proposals`). 1/3 (#259): `Manufacturer`; `Brand` gains `status`, `wikidata_id` (format only, unique when set), `url`, `same_as`, `logo` and alt, optional `manufacturer`, `name_key` (catalog `0011_brand_entities`, data kept, rerun a no-op); pure rules in `catalog/engine.py`; `brand_anchor` serves approved brands only; staff admin. `Manufacturer` is audited since 2/3. Catalog moderation, needs human review |
| I18N.13 | done | #305, #664, #672, #698 | `manage.py i18n_export --lang --scope emails\|vendor-help\|all --format xlsx\|po` and `i18n_import <file> [--dry-run] [--skip-invalid]` (`apps/core/i18n_review`, pure rules in `engine.py`): protected XLSX with Read me sheet and frozen banner in EN, IT, DE, FR and ES, checksum per row, import validates placeholders, HTML tags, new URLs, length, language allow-list, row-numbered errors, audit `core.i18n.imported` with the file hash; reviewer steps in `docs/dev/translations.md`. CMS pages go through wagtail-localize (not built here). No migration. #664 (register fixes): the customer privacy account page is informal in it, de, fr and es (gettext context "customer privacy page" for "Your data", the vendor page keeps the formal wording), its two templates joined the catalog scopes, and the second-factor pages are formal in it, de and es (fr already was); `docs/dev/translations.md` Tone section documents both rules. #672: the vendor application flow (`vendors/apply` templates, the three application emails, `apps/vendors/forms.py`) joined the catalog scopes, 39 entries in it, de, fr and es in the formal register |
| SEC.13 | done | #236 | Anonymous POST form sweep: a system check fails for an undeclared anonymous POST endpoint, `@public_form(...)` declarations and the generated public forms register (`tools/build_public_forms.py`). Security code, needs human review |
| I18N.7 | partial: Italian catalog (#245); German, French and Spanish AI-draft catalogs for the whole interface scope (#588, slice 2); the professional review is I18N.19 | #245, #588 | Italian catalog for the Manager and account strings through `tools/i18n_catalog.py` (extract, compile, check; `make check` runs the check). #588: `de`, `fr` and `es` catalogs cover the Italian scope (`tests/unit/test_language_catalogs.py`); production serves `en,it` only until I18N.19 lifts the gate |
| CAT.19 | done | #154 | Vendor products in the Manager shop space |
| VEN.24 | done | #141 | Claim an imported vendor (legal email or registered PEC) |
| SEC.5 | partial: item 9, item 10 part 3a-2 and 3b open; items 6 and 7 done (#489), item 10 parts 1 (#490), 2 (2a to 2d) and 3a done | #162, #169, #166, #171 | Security headers by response type, endpoint sweep, hostile-input corpus, rate-limit tests. #924: outbound transports (the Stripe default transport, VIES and the other adapters) share `core.outbound.no_redirect_opener` and refuse every followable redirect |
| CAT.9 | done | #167 | Product search: full-text per language, trigram, SKU prefix |
| CORE.20 | done | #171 | Per-IP limit on refused API keys |
| CMS.3 | done | #156 | Blog: index and post pages, scheduling, translatable authors with author pages, ProfilePage/Person and BlogPosting JSON-LD, choosers, `import_article` |
| CMS.4 | done | #168 | Term and vendor landing pages |
| THM.12 | done | #151, #211, #402 | WebP and AVIF renditions, sanitised SVG logos. #211 de-flaked the SVG sanitiser timing test (load-robust ratio); #402 made it linear-time rather than ratio-based |
| CMP.3 | partial: Safety Gate checks, repeat-offender suspension and recall procedure not built | #175 | Done-when met (spec 11, row CMP.3): GPSR data (`GpsrInfo`, vendor defaults, per-language safety texts), publication gate in `apps.catalog.publishing`, write guards on published products, nightly check, `SafetyCase` with working-day deadlines and overdue staff alert, per-country warnings languages (owner decisions 2026-11-09). Not built, though the spec row names them: recall procedure, Safety Gate checks, repeat-offender suspension (CMP.26 covers only checkout and sale blockers). Compliance code, needs human review |
| SEC.2 | done | #164 | Secure uploads (spec 12 task row; not the "Tenant isolation" requirement of the same ID): content-checked, re-encoded uploads with app-made names on every upload path (`apps/core/uploads.py`); caps on bytes, pixels, side and animation frames; originals over 40 megapixels downscaled; bounded decode memory and one decode per process; MPO and ICC handled; malicious-upload corpus. The final caps are in code only, not yet in spec 12. Security code, needs human review |
| CMS.9 | done | #129 | Launch countdown block: pure `countdown_state`, server-rendered absolute date and expired state, page cache capped at the target, CSP-safe ES module with visitor time zone, minute updates, pause button, single polite announcement |
| CMP.9 | done | #177 | WCAG 2.2 SC 3.3.8 on the authentication templates: paste allowed, `autocomplete` tokens, passkeys offered, Turnstile without a puzzle |
| CORE.17 | done | #170, #212 | Manager shell (`apps/manager/`): two gated spaces by URL prefix, deny-by-default gate with 404s, navigation registry, vendor picker and switcher, base list view with bulk actions (dry-run preview), closed vendors refused in the shop space, sign-in page meeting CMP.9, URL sweep test. Staff access to the shop space through impersonation is CORE.10. #212 fixes: the language switcher posts through a real CSRF check, `/manager/` redirects to the user's home, a signed-in user without the space's right gets an explanatory 403 page (anonymous still goes to the login), `bootstrap_platform_owner` (CORE.23). Access code, needs human review |
| CORE.8 | done | #185, #188, #189, #209 | 1/3 platform staff is the model permission `vendors.view_all_vendors`, not `is_staff` (migration vendors 0021, no data grant; release note in release.md). 2/3 account unlock service with audit (`accounts.user.unlocked`, `accounts.user.unlocked_by_reset`) and a completed password reset clears the lockout. 3/3 own editor roles per page-tree part replace Wagtail's default Editors and Moderators groups (migration cms 0013, edit-only editor roles, empty publisher roles, nobody approves their own submission), Wagtail admin menu limited to CMS entries (`docs/dev/editor-roles.md`). #209 makes the seeding safe during partial migrations (main was red). Permissions code, needs human review |
| SEC.12 | done | #199 | Reverse proxy header ownership: Caddy drops `Server`, media carry the transport and framing headers, CORP on the HTTPS 301; the three strict xfails are plain tests; documented in security-headers.md |
| OPS.9 | partial: triage decided and rules file merged, no STAGE scan run on the new main yet | #197 | STAGE scan triage (SEC.5): no wildcard CORS, CORP header, ZAP rules file with documented accepts. ZAP 10202 (consent form CSRF-exempt by design) accepted by the owner on 2026-11-17 (#607): rules file entry, `security-scans.md`, and the sweep `tests/unit/test_post_forms_csrf.py` that fails on any other POST form without `{% csrf_token %}` |
| TAX.7 | done | #179 | Special territories by postcode (initial dataset, T15 green): most specific pattern wins (FR 97150 is Saint-Martin, not Guadeloupe), alias countries (MF, BL, AX) fail closed, seed migration tax 0006 never overwrites staff rows, Northern Ireland not seeded (D-02). Tax code, needs human review |
| CMP.25 | done | #181 | Activation gate for platform duties: `activation` purpose in `LegalFieldRequirement` seeded with the DSA article 30 trader data, append-only `SelfCertification` (version, language, text hash, no IP), checked by the VEN.5 activation next to CMP.13; DAC7 data still blocks payouts only (migrations vendors 0022, 0023). Compliance code, needs human review |
| VEN.10 | done | #182 | Public vendor storefront per language: bio, policies, products and store categories, category sidebar correct in IT |
| THM.6 | done | #184 | First vertical theme `ride`: operator palette tokens (light and dark), type scale and shapes, self-hosted Barlow and Barlow Condensed (OFL 1.1), Lucide icon subset (ISC), header, hero and home blocks, placeholder art, `LICENSES.toml` complete |
| VEN.22 | done | #202, #229 | Public "Sell with us" application at `/vendor/apply/`: account and applicant vendor in one form with no password (the person who opens the emailed link chooses it), Turnstile through the CORE.5 verifier, confirmation mail plus one reminder at 48 h each with a cancel link, GET never changes state and a second POST is idempotent, unconfirmed applications deleted after 3 days (D-24, hourly jobs), MFA at first login (CORE.4); migration vendors 0024. Account and compliance code, needs human review. #229: the attacker-chosen test password is generated, no literal |
| CMS.12 | done | #201 | Server-side rich-text sanitiser: `engine.sanitise_rich_text` on the editor's own allow-list, applied by a `pre_save` receiver to every rich-text field and StreamField block, so any write path is covered; the editor opens stored HTML it would not write; the F1 and F2 strict xfails are plain tests. Security code, needs human review |
| ARC.17 | done | #198 | `PlatformOperator.einvoice_channel` and its choices removed (migration core 0018, admin, seed, factory, spec 01); a guard test; no comment says the platform writes FatturaPA XML |
| CORE.9 | partial: 3 parts (dashboard, owner-only profile with logo, platform review and revert #231); the D-14 field lock for pipeline-fed products is open | #200, #204, #208, #231 | Open: the D-14 field lock for pipeline-fed products (a vendor edit locks the field, the feed stores its latest value but does not overwrite, lock state and feed value shown in the Manager, lock and unlock audited, cross-vendor refusal); no lock or feed-value model or service exists yet. 3/3 (#231, D-33): `vendors.services` `profile_history`, `revert_profile_change` (reason required, `dry_run`, audit `vendor.profile_reverted`, email to the owners), `revert_logo_change`, `confirm_logo_change` and the daily `purge_retained_logos` job (previous logo kept for `VENDOR_LOGO_REVIEW_DAYS`), permission `vendors.revert_storefront_profile` (`vendors/0030`, role groups `manager/0005`), Manager page `/manager/platform/profile-changes/` with POST revert as a CORE.25 `profile_revert` (sensitive) action; the other languages go stale after a source-text revert. Security and moderation code, needs human review. Earlier parts: 1/3 shop dashboard with product KPIs on a queryset scoped by `for_actor` and the selected vendor. 2/3 `save_storefront_profile`: owners only, plain text with length caps, rate limit, post-moderation email to the operator support address, `vendor.profile_changed` audit with before and after, form at `/manager/shop/profile/` that vendor staff cannot reach. #208 (2b): owner-only vendor logo upload through `MediaImage` validation into a per-vendor collection, replaced image deleted, audited; migration `vendors/0025`. Security (file upload), needs human review |
| CORE.23 | done | #212, #852 | `manage.py bootstrap_platform_owner EMAIL [--create] [--dry-run]`: idempotent, ChangeSet, audit `core.platform_owner.grant`, step in release.md. #852 (check 45 finding 1, closed): the directly granted `core.platform_owner` is the owner role in every section check (`apps/manager/access.py`, `wagtail_hooks.py`), so a bootstrapped owner with no role group approves a vendor claim and a return policy while a superuser without the permission is still refused (`test_direct_owner_sections.py`). Permissions code, needs human review |
| OPS.10 | partial: the workflow has not run yet (manual run after merge) | #213 | Nightly line-only coverage workflow (`coverage-nightly.yml`), report only, `coverage-report` artifact and job summary table |
| CMP.6 | partial: price history and reference price (#263), online withdrawal (#273); the strikethrough, the trader status and the guarantee notice open | #263, #273 | Append-only `PriceHistory` (migration `catalog/0012`), the 30-day Omnibus reference-price engine, service writes and the variant override rule (owner Q1=A, `SALE_APPLIES_TO_OVERRIDDEN_VARIANT`, a module constant not yet read by checkout). Catalog and pricing, needs human review. Withdrawal (#273): withdrawal flow with an append-only `WithdrawalStatement` (`compliance/0007`, `0008`), pure engine for the period, order lookup form, acknowledgement and vendor notice emails, audit entry `compliance.withdrawal.submitted`, e2e screenshots (`tests/e2e/test_withdrawal.py`). Consumer-law code with personal data, needs human review |
| ARC.29 | done | #285 | System check `core.E801` fails `check --deploy` when demo mode or forced noindex is on in a LIVE environment, one unit test per flag |
| ARC.30 | done | #277 | Field-help sweep test (`tests/unit/test_field_help_sweep.py`), allow-list with reasons, counted baselines that only shrink, `docs/dev/field-help.md` |
| ARC.31 | done: 5 of 5 (events and CMS, catalog, vendors, Manager, remaining apps) | #287, #301, #302, #307, #308 | Part 1 (events and CMS, #287, migrations `cms/0017` and `events/0005`) merged last; both ARC.30 baselines are at count 0 now, and `tests/e2e/test_event_field_help.py` keeps the event editor screenshot. Earlier parts: Plain-language field help with Italian entries. 2/5 catalog (#301, migration `catalog/0015`), 3/5 vendors (#302, `vendors/0027`), 4/5 Manager shell and forms (#307), 5/5 accounts, compliance, core, ingest, shipping, tax and themes (#308: migrations `accounts/0008`, `compliance/0006`, `core/0022`, `ingest/0002`, `shipping/0011`, `tax/0010`, `themes/0002`; shared `apps/core/translation_fields.py`). The two ARC.30 baselines now list only `cms` and `events` fields (358 and 113 lines on main, from 572 and 235). Parts were merged out of the stated order (1 is still open) |
| ARC.32 | parts 1 to 3 merged (storefront and shell, account and Manager, Wagtail and Django admins; #279, #289, #283, fix #378); axe check per space to confirm | #279, #289, #283, #378 | Parts 2 and 3 add the help button to the account and Manager pages (`test_help_button_account_manager.py`) and to both admins (`test_help_admins_sweep.py`, e2e `test_help_admins.py` with screenshots; `cms/help_admin.py`, `admin-help.js`); #378 made the stale-answer e2e test deterministic. Part 1: Contextual help button in the signed-in header (`HelpEntry` snippet in `cms/0016`, `help.py` lookup with cache, modal fetched from `help_views`, `help.js` and `50-help.css`, user-menu item, "Fields on this page" from the form definitions), `MPMV_VENDOR_GUIDE_URL` for vendor pages. Sweep `tests/integration/test_help_button_sweep.py` and e2e `tests/e2e/test_help_button.py` (desktop, mobile, menu and form screenshots). Wagtail admin and Django admin parts are open |
| ARC.36 | done | #647, #666 | Help files under `help/`, `sync_help`, coverage sweep: `HelpEntry.source` (migration `cms/0022`), `apps/cms/help_files.py`, `help_sync.py`, placeholders from an allow-list, snippet read-only for file rows, `seed` and the migrate step call the sync, `tests/help_coverage_baseline.txt` (shrinks with ARC.37), general entry in 5 languages, `docs/dev/help-content.md`. #666 (part 2): `HelpEntryCreateView` carries the session gate on post and put, a sweep over the add, edit, delete and copy views of the registered Manager snippet viewsets fails without it; the statements help points to the compliance contact `operator.compliance_email` (falls back to the support address) |
| ARC.37 | partial: the seller guide sections for catalogue and images, shipping, and API and accounting merged (#929); parts 3 to 7 merged (customer account, cart, checkout and withdrawal screens #661; Wagtail and Django admin screens, page types, general entry #662; vendor Manager screens #675; platform staff Manager screens #677, baseline at 0) and the customer help section (#689); the vendor guide pages and the review checklist per part against STAGE are open | #661, #662, #675, #677, #689, #824, #929 | Help files `help/<lang>/route/` and `page_type/` in en, it, de, fr, es: 17 customer keys (informal for the customer screens, formal for the second-factor, vendor application and staff privacy requests pages) and 9 admin routes and 10 page types (formal); `tests/help_coverage_baseline.txt` 72 to 20 (#675: 16 vendor Manager routes `manager:shop:*`, formal); labels quoted from the effective catalogs, `docs/dev/help-content.md` says how to quote screen labels; the general entry reviewed in 5 languages. #677: 20 platform staff Manager routes (`manager:platform:*`, formal), baseline count 0. #689: customer help centre `/help-centre/` and `/help-centre/<section>/` (six sections, help kind `guide`, `GUIDE_SECTIONS`, informal), linked from the help dialog (`ROUTE_GUIDE`) and the footer and header of both themes when `MPMV_HELP_URL` is empty, canonical, hreflang and sitemap per language with its own file, the slug `help-centre` reserved under a language home (`cms.E004`). DE, FR and ES are AI drafts behind I18N.19. #770 (lane-a): the seller guide, seven sections in five languages, as pages with sitemap entries, declared in the tenancy and security registries; #929 adds `catalogue`, `shipping` and `api` to `GUIDE_SECTIONS["vendor"]` in five languages and states which screens do not exist yet; the comparison with the live STAGE screens stays open |
| VEN.2 | done | #225 | `VendorPlan` and versioned `CommissionRule` (migration `vendors/0029`, `engine.py` rule lookup, audited services with `dry_run`): no implicit zero rule, overlapping periods refused, a new rule closes the previous version, changes need an operational session (action `commission_change`) and a role from the CORE.18 matrix. Per-vendor plan assignment stays in VEN.19. Commission code, needs human review |
| I18N.15 | done: `MPMV_LANGUAGES` setting, checks, non-English CI job, `change_languages` command with language redirects | #321, #578, #582, #597, #614, #834, #920, #927, #946, #959, #962, #963, #964, #965, #966 | `mpmv/settings/languages.py` feeds `LANGUAGES`, `LANGUAGE_CODE` and the Wagtail languages; the tests, the help sweep and the e2e suite (#920, #927, #946, #959, #962 to #966, in seven slices) pass under any primary language; `language_checks.py` (system check I911 for a language without an interface catalog); the cart, order and account `language` defaults became callables (`accounts/0009`, `cart/0002`, `orders/0012`); CI runs the unit suite and `makemigrations --check` with `it,de`; #834: the API catalog language defaults to the primary language and its tests build attributes in the configured languages |
| OPS.12 | done (temporary, remove before LIVE) | #381 | `/_links/` Site links page for platform staff (`apps/seo/site_links.py`, pure `site_links_engine.py`), header menu item, `noindex` and `no-store` |
| CORE.25 (banner, security page, elevate roles) | done: follow-ups of #234 | #382, #389 | Passkey window button in the banner and an account security page (#382); the owner, support and finance roles hold `core.elevate` (#389, `manager/0003`, `0004`, owner decision 2026-11-12), `platform-roles.md` updated. Security code, needs human review |
| CORE.25 (wording) | done | #393 | The user-facing name is the passkey window: "Open passkey window", banner "Passkey window open: N min left", no "Secure mode" or "Operational mode" text (a test asserts it); action groups `everyday`, `sensitive` and `critical` (old keys still read; old audit action names still read by the friction report); `core/0023` renames the `elevate` permission name. Spec 02, 04, 12 and D-32 reworded. Security code, needs human review |
| CAT.7 | done: 1 (product page: variants, companion box, visible SKU, on request) and 2/2 (#621: gallery images from the CAT.4 renditions, related products; e2e axe and CSP in both themes); no rendition lookup in `gallery_items` (the fallback to the original is #635, open) | #386, #621 | Route `product`, `catalog/product_pages.py`, pure `engine.product_node`, page and JSON-LD with the brand `@id` (no `offers` until TAX.12), `PRODUCT_PAGES_ROUTED` is now True in base settings, e2e screenshots (`tests/e2e/test_product_page.py`); no migration. Public data exposure, needs human review |
| CAT and CORE (fixes) | done | #384, #383 | Product detail links behind `PRODUCT_PAGES_ROUTED` (stopgap until the product pages are routed; True in tests) and `MFA_LOGIN_URL` so an anonymous visitor is sent to `/account/login/` and not the loopback-only admin login (#384); the event publish e2e test closes the date pickers deterministically (#383) |
| ORD.7 | partial: My orders list, detail, timeline and tracking; quotes not built | #357 | `orders/customer_views.py` and `engine.py` timeline, account URLs, e2e screenshots `list` and `detail` (`tests/e2e/test_customer_orders.py`). Orders code with personal data, needs human review |
| VEN.27 | done | #216 | `NoTinCountry` table with effective dating (`vendors/0028`, six countries seeded), audited add and end with dry run behind an operational session (CORE.25), `engine.no_tin_countries_on`, DAC7 reads the list of its period and snapshots it; no overlap per country by constraint. Notifying re-blocked vendors is not built. Vendor and DAC7 code, needs human review |
| OPS.11 | done (first run on a tag in CI only) | #295 | Parity smoke deploys the previous release tag, then the candidate, with the no-previous-tag fallback; the upgrade path is required on main and release tags |
| OPS.8 | partial | #215 | Manager developer documentation |
| FIT.1 | done: 1 of 2 data model and services (#286), 2 of 2 Manager pages for the scheme, nodes and categories (#572) | #286, #572 | New app `apps/fitment` (migration `fitment/0001`): `FitmentScheme`, `FitmentLevel`, `FitmentNode` tree with aliases, merge history and notes, pure `engine.py`, audited services with `dry_run`, a reference registry for FIT.2 and FIT.5; `FitmentCategory` rows replace a `Category` field so the catalog never depends on fitment (deviation from spec 17, stated in the PR); import contract that tax, shipping, cart, orders and themes never import it |
| VEN.4 | done | #350 | New app `apps/ledger` (migrations `ledger/0001`, `0002`): append-only `LedgerEntry` with database triggers against UPDATE and DELETE, signed `Decimal` amounts, an idempotency key, `record_entry` (audit `ledger.entry_recorded`, dry run) and the balance and the statement as the sum of entries. Ledger code, needs human review |
| VEN.33 | partial: the pure refund fee engine and the surcharge on the plan rule; the vendor terms clause is in CMP.5 | #318, #343 | `vendors/refund_fee.py`: provider cost plus surcharge, `ROUND_CEILING` to the whole euro (the one explicit exception to `ROUND_HALF_UP`), the owner's regression cases. Nothing charges the fee yet (PAY.4). Money code, needs human review |
| VEN.35 | done (part 1; the PDF is VEN.36) | #448 | Versioned seller sales terms and pre-contractual data (`vendors/0033`, `0034` append-only `SalesTermsVersion`), the `require_sales_terms` operator rollout setting (`core/0025`). Landed before check 28 and missing from this file until check 29 |
| PAY.5 | done | #326 | `PaymentProvider` interface, registry that refuses a provider missing a capability, system checks `payments.E001` to `E003`, fake provider, contract tests and `docs/dev/payment-providers.md` |
| PAY.1 | partial: the Stripe provider; checkout creates one PaymentIntent per order with the real per-vendor split; the transfers are PAY.3 | #351, #429, #452 | #351: `payments/stripe_provider.py`. #429: one PaymentIntent per order. #452: the intent is built from `payments.settlement` |
| PAY.2 | done | #351, #429, #452, #465 | Signed, idempotent webhook (`payments.processedevent`), the audit row, the order marked paid, and the paid order posts the ledger and queues the transfers (#452). #465: the Connect webhook accepts its own signing secret (`MPMV_STRIPE_CONNECT_WEBHOOK_SECRET`), the platform secret stays for customer payments |
| PAY.3 | done (D-08, D-41) | #452 | `apps/ledger/settlement.py`: a paid order posts one ledger line set per vendor and queues one transfer per vendor package as a transactional outbox job (a rollback leaves no job); `payments/settlement.py` computes each vendor net; a refunded or partially refunded package is never transferred (`ledger.transfer_skipped_refunded`); a platform with `commission_vat_mode` not `none` refuses to settle (`ledger.settlement_blocked_vat_mode`, until VEN.34); a vendor with no ready account stays a receivable. `PAYMENTS_TRANSFERS_WIRED` is True, so `payments.E004` no longer stops LIVE. Needs human review |
| PAY.4 | partial: refunds through and outside the platform, duplicate payment refunds, stray-capture alerts with manual close and the 14-day auto-close (#931, #938); open: fixes 4 to 6, chargebacks, the vendor refund action (PAY.7), the Manager alert list and the Check-now throttle (#943, #954) | #318, #476, #931, #938 | `ledger/refund_engine.py` (pure) and `ledger/refunds.py` (service: `dry_run`, `AuditLog`, replay by key, reverse transfer from the vendor's share, refund fee with the L-10 exclusion flag, outside mark with declared date and method); `payments/duplicates.py` refunds a second payment in full with no fee; `PlatformOperator` refund settings (core 0026); ledger 0003 to 0005, payments 0003. #931 (`payments/stray.py`): a capture on a cancelled or mismatched order raises the `payments:stray-capture` alert and moves no money. #938 (owner decisions 2026-11-21): an unmatched capture or a wrong order reference raises the same alert; `close_stray_capture` closes it by hand with a reason (audited with the reason's length and hash only); the nightly `payments.sweep_stray_captures` auto-closes after 14 days only when the provider confirms a full refund, otherwise one reminder per business day; the state is read from the audit trail, no migration. Ledger and payments code, needs human review |
| PAY.7 | spec only | #485 | Vendor refund action through the platform (PAY.4 part b): `vendor_refund` critical action, the owner's isolation tests (404 not 403, grant bound to action, digest and shop). No code |
| SHP.4 | done | #354 | Shipping cost per vendor package through `quote_shipping` and the tax engine |
| ORD.3 | done | #354 | `build_checkout`: per-vendor packages, tax, shipping, blocking messages, preview; mixed-rate shipping VAT regression test. Money and tax code, needs human review |
| ORD.17 | done (customer terms clause lists pending counsel) | #463 | Checkout legal step (D-40, variant A): seller recap, box 1 and box 2, order terms snapshot in `terms_acceptance`, terms in the confirmation email, confirming poll after the provider redirect. Box 2 lists only seller clauses until counsel enters `onerous_clauses` in the CMS |
| ORD.17b | done | #473 | ORD.17 follow-ups: version stamps on the terms snapshot, kept ticks, `orders/0015` makes `terms_acceptance` not editable, confirmation email text. Orders code, needs human review |
| CORE.25, VEN.6 (shop owner passkey) | done: follow-ups open | #474 | D-32 amendment (2026-11-14): on a shop they own, a vendor owner with a passkey may "Do it once" and open a passkey window scoped to that shop for `payout_details` and `sales_terms_publish`; the owner check is `settings.SHOP_OWNER_CHECK`, re-read on each call. Found on STAGE: no vendor owner could connect a payout account. Security code, needs human review |
| PAY.3 (transfer key) | done | #477 | The transfer key names destination, amount and currency; a transfer of the same payment to another account is blocked and audited once (`ledger.transfer_blocked`); demo payout accounts (`acct_demo_`) are skipped before any provider call; `StripeError` keeps Stripe's error type and code. Closes the STAGE incident of 2026-11-14. Payments code, needs human review |
| VEN.38 | spec only | #479 | D-43: vendor configuration form with Collection costs and Commissions tabs, fixed fees once per vendor order; VEN.19 charges per billing period; VEN.33 order-handling fee defined. No code |
| ORD.19 | done | #469, #915 | #915: an item with no decidable consumer price (`cart.services.has_price`, for example a vendor with no valid tax profile) is refused with a message and creates no line. `cart_add` on the product page: POST with CSRF, cart rate budget, HTMX fragment and no-JS redirect; the sales mode is checked again by `add_to_cart` (`effective_sales_mode`) |
| ORD.10 | partial: the switch and the alert; nothing yet reports STAGE | #353 | `ensure_checkout_open` in `place_order` (the `checkout` and `payments` switches, platform and every vendor), `manage.py check_payments` alert on a payment without transfer; the done-when "verified on STAGE" is open |
| ORD.5 | partial: the cart and checkout UI, B2B fields, VIES, place order, the payment page | #360, #429 | `orders/ui` (HTMX): cart, review and checkout, conditional B2B fields, VIES, captcha, the confirmation page. #429: `place_order` starts the payment after the transaction commits and the payment page (`orders/static/orders/payment.js`) confirms it with the browser-side secret. Orders code, needs human review |
| TAX.17 | done | #323 | Shipping VAT always follows the goods (`shipping/0012`, `0013` data and schema); #323 was closed and its content merged through #325 |
| TAX.18 | partial: Northern Ireland goods rules; XI VAT numbers in `VatValidation` are TAX.19 | #325 | `BT` postcodes resolve to `XI`, UK rate for OSS vendors on NI goods, the vendor's country VAT for a business with the vendor's own number, `XI` kept out of `VIES_COUNTRIES`. Tax code, needs human review |
| CMP.2 | partial: the pure commission statement lines and the carry-over credit; no statement, export or ledger wiring | #334 | `compliance/commission_statement.py` (T-1, T-5, T-8): `ROUND_HALF_UP` per line, the refund fee as its own line. Money code, needs human review |
| CMP.33 | done | #296 | The data export needs a fresh second factor when the account has one; refusals have their own limit and do not spend the export budget |
| SEO.7 | partial: the Organization JSON-LD schema check and the operator provenance tests (#345); part 1 (#612): one site-wide `@graph` per page from `seo.page_graph`; part 2 (#673) adds `SearchAction`, `CollectionPage` and `ItemList` and the `SearchResultsPage`; the `Event` nodes (EVT.10) and #914 (1/2) adds the related-products and other-sellers `ItemList`s to the product graph; #917 (2/2) adds one named `ItemList` for `FeaturedLinksBlock` and `StripBlock` cards (public cards only, no fallback-language items) and #875 the `Event` offers; whether every item of the long Done-when is met (the sweep, the schema check, the product checklist) is for lane-a to confirm before the row becomes `done`; the blog and vendor widget lists are open | #345, #612, #673, #914, #917 | #345: tests only, the operator `Organization` node. #612: `seo/page_graph.py` registry, `graph_engine.py` (pure), `page_builders.py` (home, CMS pages, blog index and post, product, listing, event index), `site_nodes.py`; one JSON-LD script per page, a hostile product name inert, a sweep over the registered page types; no migration. #673: `graph_engine.item_list_node` and `search_template`, `WebSite` `potentialAction` to the language home `?s=`, listings (category, term, brand, vendor storefront) as `CollectionPage` with the products of the shown page, the vendor `ProfilePage` with the vendor `Organization` as `mainEntity` (same `@id` as `seller`), events index, blog index and author `ItemList`, `SearchResultsPage` with one mixed `ItemList` (fallback-language items left out, none for an empty query or no match), the product grid and product cards `ItemList`s of a CMS page reach the graph; 19 integration tests incl. a query count that does not grow, a hostile query |
| ORD.16 | partial: order emails in DE, FR and ES with the informal register, the vendor mail language (`vendors/0031`); the field-help sweep checks the Italian catalog only | #299 | The five languages for the ORD.9 emails; the DE and FR register is informal (owner decision) |
| ARC.15 | partial: design only (ADR-0009 accepted); SEC.15 and OPS.13 are the build rows | #297 | `docs/adr/0009-private-vendor-documents.md`, rows SEC.15 and OPS.13 (the scanner row was renumbered from OPS.12) |
| VEN.3 | partial: the pure `compute_commission` (paid-time commission, collection cost, vendor net) and `refund_lines` (one refund event: commission and collection cost reversed pro rata, the refund fee charged to the vendor only, never passed on to the customer, no fee for a platform error); nothing posts them to the ledger yet | #409 | `vendors/commission.py`, spec 04 section 4.4 cases and the pseudonymous real-case suite. Money code, needs human review |
| VEN.6 | partial: Stripe Express onboarding, the `account.updated` webhook, the effective sales mode derived at read time (D-19), the vendor Payments page, a test payout account in the demo seed | #356 | `payments/connect.py`, `payments/0002`, `vendors/0032`, `manager/shop/payments.html`; checkout uses the effective sales mode. Payments code, needs human review |
| FIT.6 | partial: the anonymous cookie (#331), the merge service at sign-in and the sign-out clearing (#910, #913); the merge notice with Add and Not now is FIT.7 UI and open, so regression case 8 ("sign-in shows the merge notice once") is not yet end to end | #331, #910, #913 | Anonymous garage cookie (`fitment/garage.py`, `fitment/views.py`): up to 3 vehicles of make, model and year, extra keys make the cookie invalid, `read_garage` re-validates every vehicle against the catalogue, an invalid cookie is cleared on the next garage action, storage `off` deletes the cookie. #910 (1/2): `garage_merge.py`, a `user_logged_in` receiver stages the cookie's vehicles the account lacks in the session entry `fitment_garage_pending` and adds nothing; `merge_pending` (Add) creates profiles through `garage_profiles` within the limit, `dismiss_pending` (Not now) merges nothing and keeps the cookie; audit `fitment.garage.merged` holds counts only (`docs/dev/audit-trail.md`). #913 (2/2): `merge_pending(dry_run=True)` returns a `ChangeSet` and writes nothing; `GarageSignOutMiddleware` clears the cookie at sign-out when it equals the active profile (same node and year), any other cookie stays; spec 17 §17.4.5 documents the staged entry. Privacy-relevant, needs human review |
| FIT.5 | done in three slices: 1/3 model and services (#896), 2/3 "My garage" page (#898), 3/3 GDPR export and erasure with the 24-month cleanup (#905); #933 makes the retention a setting | #896, #898, #905, #933 | `GarageProfile` (migrations `fitment/0005`, `0006`) with the pure `engine.py` rules and `garage_profiles` services (one active at most, reset keeps profiles, rename, duplicate, delete with confirmation, limits, merged and retired nodes). #898: `/account/garage/` (signed in only, every action POST with CSRF, another account's profile is a 404, `contains_special_category` makes the page a 404), ride override, help in five languages, site map `ACCOUNT_PAGES`, tenancy and endpoint-security registry entries, e2e with axe. #905: export section `garage_profiles` and erasure in `apps/compliance/gdpr.py` (GDPR handled-map entry `fitment.garageprofile`), nightly job `fitment.cleanup_inactive_garage_profiles` (notice 30 days before 24 months of non-use, delete after both periods, `dry_run`, audit `notice_sent` and `expired`), email templates in four languages. #933 (closes check 47 finding 3): the 24 months and the 30 notice days are `FitmentScheme` fields (`garage_inactivity_months`, `garage_notice_days`, `fitment/0007`), validated in `save_scheme` and the Manager scheme form; the engine constants stay as defaults. A 36-month cap is open in #958. The chooser that creates profiles is FIT.7. Personal-data code, needs human review |
| FIT.17 | partial: the order vehicle snapshot (`orders/0013`) from the active garage vehicle; the fit-answer annotation on the order line is open | #331 | `orders/engine.py` snapshot, shown on the confirmation and the two order emails. Orders code, needs human review |
| MIG.1 | partial: part 1, the read-only legacy source connector (SELECT only, authorizer, `mode=ro`) and the fixture schema | #336 | `ingest/legacy_source.py`, `legacy_engine.py`; reads no real data, tests use a synthetic schema |
| MIG.3 | partial: part 1, the catalog loader (vendors, catalog, taxonomies) from the intermediate format; a rejected record leaves no trace in the report, ChangeSet or audit trail | #341 | `ingest/catalog_loader.py`, audit `ingest.catalog_load.run`; images and IT-text twins are open |
| MIG.6 | partial: part 1, the pure redirect map engine (one hop, chains collapsed, loops refused, open redirects refused) | #333 | `ingest/redirect_map.py`; the sitemap check and the Django wiring are open |
| SEO.5 | partial: our own consent banner with a consent log, Consent Mode v2 signals and the consent state kept off cacheable pages; e-commerce events are open; the Matomo consent-free conditions are in the spec (#625, counsel 2026-11-17), not built | #330, #625 | `seo/0002` (`ConsentLog`, append-only, no IP), `seo/views.py` `consent_save` is `csrf_exempt` with a same-site Origin or Referer check |
| SEO.9 | done | #330 | `ConsentProvider` interface (`seo/providers.py`), `docs/dev/consent-providers.md`, `consent.js` lazy-loaded, unknown provider fails `seo.E001` |
| SEO.10 | done in two parts | #420, #792, #794 | `Vendor.default_product_role` and `Product.vendor_role` (`vendors/0036`, `catalog/0019`) with forms, help and the product role save button (1/2, #792); the vendor role in the product JSON-LD (2/2, #794): a producer without a CAT.20 Manufacturer is the `manufacturer`, a reseller or unknown role never is, the vendor stays the `seller`, tests in `test_product_jsonld.py` and `test_product_role.py` |
| OPS.14 | done | #421 | Staff-only release indicator: `MPMV_VERSION` in the Manager footer and the account menu, nothing for anyone else |
| CORE.30 | done | #484 | Manager platform map at `/manager/platform/map/` (section `platform.map`, same roles as the dashboard): links of the storefront, account, each vendor's shop space (plain text for staff), platform space and the editors' admin |
| CORE.24 | done (part 1 #491, part 2 #520) | #491, #520 | Session index, panic button, Support end (part 1); role-based idle and absolute lifetimes on `PlatformOperator.session_policy` (`customer_days` 14, `staff_idle_minutes` 30, `staff_absolute_hours` 12, ceiling 72), `accounts.session.expired` audit. Authentication code, needs human review |
| ORD.21, ORD.23 | spec only | #534 | Order amendments (D-47): proposals with the customer's acceptance, quote-priced shipping. No code (ORD.20 and ORD.22 have their own rows) |
| ORD.24, ORD.25 | spec only | #538, #570 | Amendment message templates from the counsel-approved wording; business details after the order (VAT number and invoicing details until invoiced or shipped, VIES re-check, audit, vendor notice). No code |
| TAX.22 | spec only | #570 | Net-price view only for buyers with a verified VAT number; the opt-in is a switch in the account Preferences (ORD.11). Code change on top of TAX.12, not started |
| CMP.34 | spec only | #570 | RF19 thresholds: first warning at EUR 75,000, then 85,000 and 100,000 (owner decision 2026-11-16). No code |
| ORD.11, TRU.3, PAY.5 | spec only (scope changes) | #570 | Preferences section at launch (MQ-43); review invitation 7 days after delivery (`TrustRules.invite_delay_days`); payments layer provider-neutral, Stripe only at launch. No code |
| VEN.13, VEN.16, CMP.5 | spec only | #580 | Clause (l) "Modifiche agli ordini pagati" approved by counsel with the second signature; re-acceptance of older versions by double signature plus 15 days' notice or the optional P2B art. 3(2) waiver. The suspension clause letter "[x]" is still to be set |
| SEO.5, MIG.22 | spec only | #585 | Matomo (operator's own instance, first-party proxy, cookieless) replaces Google Analytics (MQ-46); domains, mail and TLS at cutover (MQ-47). The consent-free operation is an open legal point. No code |
| CAT.22, CAT.23, VEN.40, SEO.13 to SEO.15, I18N.16 to I18N.18, CMS.17 | spec only | #589 | Follow-up rows from the SEO.1, I18N.6 and THM.15 reviews: product attributes, variant identifiers, vendor return policy, seller name rule, sitemap invalidation, x-default tracking, empty `site_root_url` check, footer columns and social profiles. No code |
| SEC.5 (items 6 and 7) | done | #489 | Supply chain (`tools/audit_ignores.py`, every pip-audit ignore with reason and an expiry of at most 90 days; `S` rules on `tools/`) and no hard-coded secret fallback on STAGE and LIVE (`env_str` fails closed on a blank, `live.py` refuses an "insecure" secret key). Settings and secrets, needs human review |
| SEC.5 (item 10, part 1) | partial: accounts, passkey window and request tampering scenarios in `tests/security`; abuse and business scenarios are open | #490 | `test_passkey_window_attacks.py` and the request tampering suite; no production code. The two strict xfails on sessions after an email change or an MFA reset were closed by #491 |
| SEC.5 (item 10, part 2) | done in four slices: 2a #850 (resource abuse), 2b #853 (ReDoS), 2c #858 (information leaks, API 413), 2d #860 and #864 (AI-agent injection) | #850, #853, #858, #860, #864, #878, #892, #893 | `tests/security/test_resource_abuse.py`, `redos.py` and `test_redos.py` (a static inventory of every regex under `apps/`, fast sweep per PR, 100 KB sweep marked `security_slow` and nightly), `test_information_leaks.py` with `leak_urls.py`, `test_ai_agent_injection.py`. Fixes found by the scenarios: an event body nested 1000 levels deep no longer crashes (422 past 20 levels, `apps/events/services.py`); `vendors.terms_engine.parse_clauses` is linear (it backtracked for over a second on a 1 000 character line); an oversized API v1 body answers a 413 problem and other `SuspiciousOperation` a 400 problem (`apps/api/v1/errors.py`); `apps/core/hidden_text.strip_hidden` removes zero-width, bidi, tag-block, variation-selector and control characters from `llms.txt`, ICS and RSS text (the RSS feed no longer fails on a control character), `llms.txt` titles and summaries are capped. #878 and #893 (check 47): `strip_hidden` deletes every Cf and Cc character plus U+2028 and U+2029, the Mongolian free variation selectors, the variation selectors other than VS16 and the whole tag block, from a table built at import from `unicodedata`; private use is kept, and ZWJ and ZWNJ are stripped (owner question). #892: `test_*_parity.py` files in events, ingest, themes, core and vendors pin the old output of the ReDoS rewrites on real valid input. Still `xfail(strict)`: API arrays without `maxItems`, the free-form event `body` schema, an HTML 405 on API v1. The ReDoS slow sweep is red in the security nightly (check 46 finding 1, still open in check 47). Security code, needs human review |
| SEC.5 (item 10, part 3a) | partial: 3a-1 done (lane-b areas, #856); 3a-2 in part (#923, payment and refund scenarios); coupon stacking and payout after an IBAN change are not written | #856, #923 | `tests/security/test_business_logic_attacks.py` (70 cases: price and VAT never from the caller, quantities, not-for-sale products by forged id, per-vendor split, cart isolation, shipping import, last-unit race in carts); the cart refuses a non-whole quantity (`engine.is_whole_quantity`). #923 (3a-2, 13 cases in `tests/security/test_payment_attacks.py`, Stripe doubled only behind `StripeClient`): repeated, mismatched and out-of-order capture events, refunds over the capture, concurrent refunds, refunds by another vendor's or an inactive member, a refund grant from another session, the amount from the stored order total, a cancelled order. Still not written: coupon stacking (no coupon feature yet, PRM) and a payout after an IBAN change; 3b (integrations) is open. Security code, needs human review |
| CORE.25 (hardening) | done | #497, #510 | #474 follow-ups: `SHOP_OWNER_CHECK` fails closed, shop offers dropped after use, the audit names the shop, system check `accounts.E003`. #510: an unscoped passkey window covers only while the user may elevate (`core.elevate` and a passkey), re-checked per request; group role changes end members' sessions. Security code, needs human review |
| ARC.34 | done | #496, #507 | `Unit + integration` in 4 crc32 shards (`MPMV_TEST_SHARD`), `tools/check_test_shards.py`, docs-only PRs skip image and E2E, stable required check names, `ready_for_review` trigger. Documented in CONTRIBUTING and `release.md` by check 32 |
| ARC.20 (lane accounts) | done | #498, #500 | Lane-b works from its own collaborator account (trusted for coordination and reviews only); "Lane review" check (`.github/workflows/lane-review.yml`) required on main after the owner's approval; `parallel-lanes.md` |
| THM.8 | done: cart, checkout and confirmation (1/2), customer area and sign in (2/2) | #503, #502 | `ride` CSS `40-cart-checkout.css` and `50-account-auth.css`, e2e at 360, 768 and 1280 px. THM.16 moves these pages onto Bootstrap 5.3 |
| THM.13 | done | #514, #515, #516, #522 | Vendored Bootstrap 5.3.9 (`foundation` key, `@layer bootstrap`, hash-checked `vendor/`), token to `--bs-*` map, per-theme budgets (ride CSS 48 KB and JS 70 KB), CSP lint (tooltip, popover, data-bs-html), selector-collision test, components with focus ring and axe; follow-up: helper-class contrast lint |
| THM.16 | done in two parts (1/2 cart and checkout, 2/2 account and auth) | #528, #530, #820 | THM.8 pages on Bootstrap 5.3 classes; #820: the Bootstrap grid wins over the base grids in the `ride` listings, search and cart; passkey prompt rebuilt from the current base page. #558: every moved page (review, payment, confirmation, login, MFA verify, TOTP, recovery codes, levels prompt) is under the e2e gates at 360, 768 and 1280 px. Customer sign-up and password-reset pages do not exist yet (CORE.32, CORE.33) |
| THM.15 | partial: 1/3 navbar and offcanvas menu, 2/3 search overlay and mini-cart, 3/3 page banner, footer chrome and back-to-top; the catalog pages include the banner since THM.7 (#628, #646); the content pages wait for THM.9, the footer widgets and social links have no data source until CMS.17 | #532, #536, #541, #584, #610 | `ride` header on a Bootstrap navbar with dropdown sub-menus and an offcanvas menu below 64rem (one switch point), `<html class="no-js">` removed by the external `ride.js` (empty `html_attrs` block in the base layout), search modal and mini-cart offcanvas fed by HTMX from the `orders:cart_mini` fragment (`#cart-count` live region), e2e at 360, 768 and 1280 px. 3/3 (#584): `components/page_banner.html` (title h1 or h2, lead, breadcrumb, optional image under a scrim), footer with up to four widget columns, labelled social links and a back-to-top `#top` link (visible without JavaScript, shown after 400 px with it), e2e `test_ride_chrome.py`; ride theme.css 43052 B gzip of 46080 |
| EVT.4 | done | #543, #549, #555 | Vendor area `/manager/shop/events/`: list, form with recurrence presets (`events.engine.build_rrule`), submit and cancel (1/3); block editor with a stale-edit guard, image library with licence fields, map picker, product and category choosers, read-only preview (2/3); staff hide and restore, `VendorEventPolicy` and the Done-when e2e (3/3). Event code, needs human review |
| EVT.6 | done | #576 | Public event pages: list, filters, calendar, archive, event page, ICS (`apps/events/listing.py`). #576 shows as closed on GitHub because train #605 merged its commits |
| EVT.8 | done | #586, #683 | Multilingual events: `original_locale`, `add_event_translation` (Manager page `events/<id>/translate/`, API), original-language fallback with a notice, `hreflang` only for existing languages, events in the per-type sitemap; audit action `events.event_translation_added`. #586 shows as closed because train #627 merged its commits | #683: regression tests that a vendor suspended after authentication cannot add a translation through the API (403 `VENDOR_SUSPENDED`) or the shop translate page, and the translate page text no longer promises a body field (msgid changed in it, de, fr, es).
| EVT.9 | done | #629, #640, #654 | Feeds per language: `events/feeds.py` (RSS 2.0 and ICS for the platform, per vendor, per type, single-event ICS), pure ICS serializer in `events/engine.py`, field allow list, ETag, Last-Modified, 304, `Cache-Control`, per-IP rate limit (31st request in a minute gets 429 with `Retry-After`, 304s count), alternate links, the Subscribe block with `webcal://`, feed URLs in `llms.txt` (`events/discovery.py`, `event_feeds` tags); regression cases 12 to 16 and 20. #654 (review minors of #640): feed links computed once per request, distinct RSS and ICS alternate titles, tests for the language without a live index, the Shop line, ICS escaping, a suspended vendor, non-primary language URLs, a foreign Host header, `llms-<lang>.txt`, and an e2e on the Subscribe block (axe, CSP, 3 widths, both themes) |
| EVT.7 | done | #630 | Vendor storefront "Upcoming events" section: `events_tags` template tag (next 3 public events of the vendor, EVT.8 language rule, `lang` and language name for a fallback event, link to the vendor's events), read live on every request (no shared-cache lifetime), bounded queries, a suspended vendor shows none; `test_storefront_events.py` and an axe e2e |
| EVT.11 | done | #617 | Event emails through the ARC.16 outbox (approved, changes requested, hidden, restored, daily staff review digest `events.review_digest`), Wagtail's own moderation emails to the vendor off, secure CORE.15 action link on the changes request, event page as a CMP.5 notice target with a hide through `hide_event`, and the Manager statements page (`manager/shop/statements`, own shop only, no notifier identity); audit action for the digest in `audit-trail.md`. Event code, needs human review |
| CAT.27 | done | #634 | Public search results page on the language home (`/?s=`, `/<lang>/?s=`): `catalog/search_page.py` (products through CAT.9 with the CAT.8 filters and pagination, events, articles and vendors groups registered by their apps, suggestions on no match), `noindex, follow`, never cached, query cut to 100 characters, 30 per minute per IP (`SEARCH_RATE_LIMIT`, 429 above it), works without JavaScript, both themes. The `SearchResultsPage` JSON-LD node stays with SEO.7 part 2; the events filter runs in Python until CAT.28 |
| CAT.29 | done | #671 | Blog article and author pages without request-time renditions: `{% responsive_image %}` with the new presets `article` (800, 1200, 1600) and `avatar` (square 240, 480) in `apps/core/images.py`, made by the `core.render_image` job; JSON-LD keeps `width-1200` and `fill-240x240`; staff pages keep `{% image %}`; `test_blog_renditions.py` (rendition count unchanged for both pages). Existing images get the presets when their job next runs; THM.9 ride blog templates must use the same tag |
| CAT.25 | done | #686, #697 | Orphan `MediaImage` cleanup: `catalog/media_cleanup_engine.py` (pure: orphan ids, 24 h grace period) and `media_cleanup.py` (`reference_sources`, `orphan_images`, `delete_orphans` with `dry_run`, `ChangeSet`, one `AuditLog` row per image, action `catalog.media_image.orphan_deleted`, rows locked and every source re-read inside the transaction); sources are every reverse relation of `MediaImage`, Wagtail's `ReferenceIndex` and the latest, live and scheduled revisions (reads the private `ReferenceIndex._extract_references_from_object` of Wagtail 8.1); Wagtail admin screen `admin/images/unused/` for the image permission, delete gated by the `bulk_edit` session level, first 200 orphans, no pagination; `docs/dev/audit-trail.md`. Owner questions open in the PR (24 h grace, vendor collections) |
| THM.7 | done: part 1 listings (#628), part 2 product page and storefront profile (#646) and the search results page (THM.17, #667) on Bootstrap; part 3 (#676) adds the specifications and the fitment summary | #628, #646, #667, #676 | `ride` overrides for the listing (banner, filter form and chips, cards, numbered pagination, store categories), the product page (gallery carousel with thumbnails and zoom dialog, tabs, buy box card, related strip) and the vendor profile (policies in an accordion); copied from the base templates with the same ids, hooks and i18n; the ride listing drops its own JSON-LD (one graph from SEO.7); e2e at 3 widths, keyboard-only carousel, tabs, accordion and filters, 44 px targets, reduced motion | Part 3 (#676): specifications (attributes, weight, dimensions, GTIN, MPN) and an active-nodes-only fitment summary on the product page in both themes and as `ride` tabs, through the `catalog_product_fitment` hook (the catalog does not import `fitment`); the FAQ and reviews tabs are not built (no data model, needs a decision); the new strings are English until I18N.20 |
| THM.17 | done | #667 | `ride` override of the CAT.27 search results page (`themes/ride/templates/catalog/search.html`, copied from the base template: same blocks, ids, hooks, parameters, `noindex, follow`): page banner with the only h1, Bootstrap input group, THM.7 product cards with the filter column, list groups for events, articles and vendors, numbered pagination; 44 px targets and reduced motion in `45-catalog.css`; render parity test base against ride (6 cases) and e2e (axe wcag22aa and CSP at 360, 768, 1280 px, keyboard only, empty and no-match) |
| THM.14 | done | #571, #579, #591, #681 | Ride carousels, motion components and CMS wiring (3/3, `cms/0019` to `0021`). #681: the video embed origins of the CSP `frame-src` are the validated setting `MPMV_VIDEO_EMBED_ORIGINS` (default YouTube no-cookie and Vimeo player); `VideoEmbedCspMiddleware` adds them only on a response that renders a video, the video block refuses an address whose embed origin is not listed, system check `cms.E003` (`engine.video_origin_problem`) refuses `http://`, `*`, wildcard hosts, paths, queries, ports and credentials; `deploy/server/mpmv.env.example` documents it. No migration |
| CAT.11 | done | #685 | Manager platform space catalog section: `/manager/platform/catalog/products/` (filters by shop, status, category, translation status and text; per-language translation state from the pure `catalog.engine.translation_states`), bulk publish, unpublish and retire with a dry-run preview behind the `bulk_edit` session level (`catalog.services.set_products_status`, one `catalog.product.status_changed` AuditLog row per product, the batch refused when a check fails), product preview and the audited sales mode and fiscal category through `save_product` (`catalogue_edit`). New matrix row `platform.products` (Catalog edit, Owner read; Finance, Support and Configuration 404). Owner questions on a separate hidden state and on the new matrix row are open in the PR |
| EVT.14 | done | #684, #710, #718, #731 | Tile proxy `/maps/tiles/{z}/{x}/{y}.png` (`apps/core/maps`: pure engine, services, `HttpTileUpstream` without redirects, `FakeTileUpstream`): zoom 2 to 18 and `0 <= x, y < 2**z` else 404 before any upstream call, cache in the `MAP_TILE_CACHE_STORAGE` alias for at least 7 days keyed by a hash of the upstream template, errors never cached, per-IP limit (429), daily upstream counter and cap (503), `TileResponseNeutralMiddleware` (no cookie, no language `Vary`). `MAP_TILE_URL` now defaults to the proxy; `docs/dev/maps.md` and `deploy/server/mpmv.env.example` updated |
| THM.9 | done: part 1 (#680) content and system pages; part 2 (#706) carousels, consent banner and consent-gated video modal; #900 fixes the flaky ride video-modal e2e (the Escape key is sent to the modal) | #680, #706, #900 | `ride` overrides of the blog index, post and author, standard, legal and archived legal, contact points, term and vendor landing, vendor recruitment pages and an error layout (404, 500, 400, 403, CSRF) with the title in the page banner; `page_trail` tag; blog images through `{% responsive_image %}` (`article`, `avatar`); parity, axe, CSP, focus and reduced-motion tests at 360, 768 and 1280 px; #706: `ride` overrides of the consent banner and its actions (Bootstrap alert look, same ids and hooks, Reject and Accept equal weight) and `css/52-consent.css`; the carousels and the video modal shipped with THM.14 and are proven here at 360, 768 and 1280 px (axe, CSP, keyboard, reduced motion, no iframe before consent) |
| CMS.13 | done | #687 | Editor-built `FormPage` on `wagtail.contrib.forms` (`cms/0023`): Turnstile through `CaptchaVerifier` before the form is read (no token: 400, nothing stored), `FORM_PAGE_RATE_LIMIT` and `FORM_PAGE_MAX_SUBMISSION_BYTES`, per-page `retention_days` (default 90, at most 365) and the nightly `cms.purge_form_submissions` (dry run, audit counts only), listed in the SEC.13 register by its page type. Base template only (no ride override); its strings are not in the catalogs yet (I18N.20). Owner questions on retention and on who reads submissions are open |
| SEO.15 | done | #691 | Pure `seo.engine.public_seller_name` and `reveals_person` (normalised equality with the legal name or first and last name, accents, case and order ignored), adapter `apps/seo/seller.py` used by the product graph (SEO.1), the storefront and the search results graph (SEO.7); an omitted seller leaves no `Organization` and no dangling `@id`; `schema_check` rejects an `Organization` named like a natural person. The application form already asks for the shop name (`business_name` to `display_name`). #807 follow-up: the public seller name is used on the product page, storefront, filters, events, CMS cards, search and site links, no invented storefront label (`test_seller_name_surfaces.py`); the catalog search vendor group filtered in Python until #835, which moved the filter by public name into the database |
| CAT.22 | done | #700 | `Product.length_mm`, `width_mm`, `height_mm` (positive, DB check), `country_of_origin` (upper-case ISO 3166-1 alpha-2) and translatable `material` and `colour` (`catalog/0017`); audited through `save_product` and `record_translation_facts_change`; optional in the import and API with the same checks; shown in the specification table of both themes. The table labels are English in every language until I18N.20; the country shows its code |
| CAT.24 | done | #704, #711, #744 | `apps/core/rendition_monitor.py` (images missing renditions, bounded scan with a 10 minute grace, failed jobs, audited `retry_render` with `dry_run`), `core.render_image` fails an undecodable file at once (`PermanentJobError`) and alerts once after the retry limit; staff screen Images > Image renditions with a Retry button per image and group (POST, image change permission, session gate). #744 removed the `catalog.render_product_image` job alias and its doc line |
| I18N.16 | done | #602 | `sitemap_version` (`core/0030`) bumped on vendor status, product publish and retire and language go-live changes; fresh sitemaps at the next request |
| I18N.18 | done | #602 | `site_root_url()` raises in production settings and never returns an empty string; the system check on the default Wagtail `Site` (silent before migrate); the seed syncs the default `Site` from `MPMV_BASE_URL` |
| I18N.19 | partial: the interim guard (default `en,it`, #588), the sensitive-string `review-export` (#588, #674), and the per-language review mark and gate check (#918); the staff preview on STAGE and the counsel sign-off are open | #588, #674, #918 | `tools/i18n_catalog.py review-export <lang>`, `SENSITIVE_GROUPS`; #674 adds the groups "DSA statements of reasons (EVT.11)" and "event moderation emails (EVT.11)" so the EVT.11 strings reach the professional review export. #918 (1/n): `tools/i18n_catalog.py mark-reviewed <lang>` and the check `core.E915` (LIVE) or `core.W915` (elsewhere) for a configured language other than en and it without the header mark; no real catalog is marked, so `make check` prints W915 for de, fr and es. Blocks LIVE |
| CAT.4 | done | #592, #635, #678 | `ProductImage` and `ProductImageTranslation` (`catalog/0016`) on `core.MediaImage`: gallery per product or variant, neutral or per-language images with the D-18 replacement rule (`engine.gallery_selection`), cover and cover lock, dedup by hash inside one vendor, alt text and caption with fallback, AVIF/WebP/JPEG renditions in the `catalog.render_product_image` job, actor check in every service. Services only: no vendor upload UI (CAT.19), variant galleries (CAT.26), rendition monitoring (CAT.24), orphan cleanup (CAT.25) are follow-up rows. Public data and media code, needs human review |
| FIT.2 | partial: declaration and matching; the publish gate for undeclared products is FIT.18, rows on proposed nodes are FIT.3 | #583 | `ProductFitmentState` and `ProductFitment` (`fitment/0002`), pure matching and declaration rules (`engine.py`, regression cases 1 to 3 of spec 17 §17.11 as unit tests), `set_product_fitment` with `dry_run`, one `AuditLog` entry per replace, the actor-vendor check, product rows kept in step with a node merge |
| ORD.20 | partial: 1/3 model and engine (#551), 2/3 money-moving changes, refunds and passkey (#564); 3a/3 Manager, customer UI, passkey prompt and settings (#577); 3b-1/3 notices, invoice notice and record, translated refusals (#624); 3b-2 (PDF and CSV downloads, the guest's signed link) open | #551, #564, #577, #624 | `VendorOrderVersion` (trigger-guarded, v1 written at payment), `OrderAmendment`, `recompute_vendor_order`, `classify_amendment` (orders 0016 to 0019); apply a lower price, a lower quantity, a removed line or an address correction with the refund in one transaction through `ORDER_AMENDMENT_REFUND_HOOK` (`ledger.refunds.refund_amendment`, ledger 0006), `due_by` = event + 14 days with the hourly alert, refund-fee exemption `customer_requested_amendment`, single-use passkey bound to the amendment digest, cancel-the-rest hold, withdrawal and idle-draft handling. Orders, payments, ledger and tax code, needs human review. CMP.14 UK lead check still deferred (no CMP.14 code). 3a (#577): the shop's amendment page (draft, preview, revise, discard, apply with the passkey prompt bound to the amendment), the shop's full cancellation with its cause, the three amendment settings on `PlatformOperator` (read-only Manager page), the customer's version timeline and cancel-the-rest answer. 3b-1: the notices of an applied change (A2 after an unavailable item, with the cancel-the-rest action and its deadline in the business time zone), of a package cancelled by cause (A1 wording for the shop's fault) and of the customer's cancel of the rest, each an outbox email and an append-only `OrderEvent` (orders 0020, trigger-guarded, scrubbed at anonymisation) with the exact text, the CMP.24 wording version (SHA-256 of the template in that language and branch) and the customer terms version in force, listed on the customer's order page; the vendor's notice of every change and cancellation with the CMP.24 invoice notice, first and marked important once the shop recorded `invoice_issued_at` (new POST on the shop's order page; the amendment screen shows it prominently); the service's refusals and the engine's classification reasons through gettext (the engine gets `gettext` at start-up and stays pure); the cancellation audit entries name their `kind`. The notice wording is counsel-approved (2026-11-17: A1, A2, the guest A2, the platform-error cancellation, the vendor invoice notice; customers informal, vendors formal); the A2 hold is never shorter than 24 hours; DE, FR and ES still wait for the translator's review (I18N.14). Adviser rules (#618 spec, #619 code, 2026-11-17): reductions reversed at the line's own rate, the shipping VAT of every version re-split over the final goods, a supplement takes the second payment's date, a change in law crystallises the paid part, chained amendments keep each payment's shipping part (spec 07 cases AM1 to AM11; tests `test_amendment_adviser_rules.py` in orders unit and ledger integration; `_shipping_portions` in `orders/amendment_engine.py`). Follow-up rows ORD.26 to ORD.28 |
| ORD.22 | partial: staff tools (#577) | #577 | Platform space "Order changes" for Support and the platform owner only (service check, other roles refused): carrier at the same or a lower price, address on the customer's request, cancelling a package with a full refund whose cause (vendor fault, customer request, platform error) decides the refund fee (`customer_requested_cancellation` or the L-10 flag) and the restock; mandatory note and `order_amend_cancel` passkey. 3b-1: the vendor's email for every staff change and staff cancellation (subject "platform support changed order"), and on the "Order changes" page the package cancellations of the last 90 days per shop and cause, the shop's own "the customer asked" (no refund fee) first and marked. The per-vendor price-increase metric waits for ORD.21 |
| TAX.12 | partial: cards, tiles, product page, variants, cart, checkout and shipping options; JSON-LD `offers` shipped with SEO.1 (#561); a business opt-in toggle and a geolocation header are open | #565 | Consumer prices include VAT: `apps/tax/prices.py` (`consumer_price`, `gross_amount`), the vendor's real rate at the delivery or estimated country (OSS vendor: destination; domestic-only: own country; exempt: none), the owner's note under every consumer price, `consumer_prices` tag so the catalog never imports tax (new import contract), checkout summaries "VAT included", `book_art74` alias fix. Tax code, needs human review |
| CORE.32, CORE.33 | spec only | #556 | Customer sign-up and password reset pages (CORE.32 names the privacy notice, the customer terms and a separate marketing consent). No code |
| CAT.23 | done | #714 | Variant identifiers (GTIN with check digit, MPN) and variant cover image |
| CMS.15 | done | #719 | Download block: file type and size, documents served through Wagtail's serve view as attachments with `nosniff`, collection privacy kept |
| CMS.16 | done | #724 | Accordion block on native `details` and `summary` |
| CMS.17 | done | #708 | Footer columns and social profiles edited in the CMS, footer fragment cache |
| ORD.29 | done | #636, #740, #759 | Packages with shipping split across VAT rates are placed: `VendorOrder.shipping_tax_rate` and `shipping_tax_reason` are nullable (NULL = split), the check is `rate IS NULL OR amount = round(net × rate / 100, 2)` (`orders/0021`, additive, no backfill), the `SHIPPING_VAT_SPLIT` block is gone, `PlaceVendor.shipping_portions` are kept in `shipping_choice["tax_portions"]` and the v1 snapshot, pure `engine.check_shipping_portions` (nets add up to `shipping_net`, VAT is their sum, half up); human review: tax, orders, ledger. #759: `apps/payments/tests/integration/test_split_shipping_settlement.py` settles a split package (finding 2 of check 42 closed) |
| ORD.30 | done | #717, #723 | `/manager/platform/orders/` lists only the packages staff can still act on: one predicate `staff_actionable` (vendor frozen, withdrawal registered) shared by the list and `_lock_amendable`, oldest first, 25 per page, filters by shop, state and placed dates, constant query count, help in five languages |
| ORD.31, ORD.32 | spec only | #717 | Owner decision 2026-11-18: customer "Order again" (ORD.31) and a staff draft from a past order with a one-time payment link (ORD.32; staff never place or pay). No code |
| CAT.26 | done | #733 | Variant galleries on the product page: pure `engine.variant_gallery` (own images by the D-18 rule, cover first, then the product gallery), `product_pages.variant_galleries` with constant queries, one `components/gallery.html` per variant in both themes, `catalog/variant-image.js` swaps galleries on selection |
| EVT.15 | done | #702 | Public event maps on the EVT.14 tile proxy (regression cases 21 to 23) |
| I18N.6 | done | #563 | hreflang, canonical and per-language sitemaps without fallback copies |
| SEO.1 | done | #561 | Product structured data, breadcrumb, return and shipping nodes, canonical; the offer is the TAX.12 consumer price |
| SEO.2 | done | #575 | Per-type sitemaps and generated `llms.txt` |
| THM.10 | done | #722 | Visual regression baselines for `base` and `ride`, browser clock frozen |
| TRU.1 to TRU.13 | spec only | #457 | Owner and counsel answers to Q-T1 to Q-T13 recorded in spec 16 and D-37 (no rating badge on cards, "Business purchase" label, labelled machine translation). No code |
| VEN.34, CMP.2, CMP.5, CMP.34, ARC.35 | spec only | #508 | D-46 (tax adviser answers, owner decisions of 2026-11-15): platform regime and fee VAT, statement content and wordings, vendor clauses (j) and (k), RF19 thresholds (CMP.34), takeover runbook (ARC.35); VEN.34 moves to P1. No code. Tax and legal wording needs counsel (CMP.24) |
| VEN.39, CORE.31 | spec only | #505 | Owner decisions of 2026-11-15: audit of every vendor text (Q-36), D-44 API keys of inactive vendors pinned with regression tests (Q-53); I18N.4 moves to P2. No code |
| ORD.26 | done | #766 | Order email outbox keeps the rendered subject, text and HTML (`core/0032`), the send uses them; `scrub_order_emails` (`dry_run`, audited, idempotent) leaves no personal data in outbox rows of an anonymised order; a restricted order keeps them encrypted; human review: orders, privacy |
| ORD.32 | done | #775 | Staff draft from a past order (`apps/orders/staff_drafts.py`, migration `orders/0022`, `core/0033` for the link lifetime): Support and the owner copy an order into a draft at today's prices (unavailable lines skipped with the reason, quantities capped at stock, never a price), send a one-time link only to the order's own email (3 sends a day, the send needs the passkey window, `order_draft_send`), the customer opens it with a POST, a GET changes nothing, and pays through the normal checkout (`test_staff_drafts.py`, `test_staff_draft_checkout.py`); new CORE.18 section `platform.order_drafts`, Manager pages in the help sweep (it, de, fr, es, en), registries and the GDPR map updated. Orders and permissions code, needs human review |
| CMP.41 | done | #837, #922 | Per-seller trader contact (name, registered office, phone, email) from one pure helper in `vendors/engine.py`, shown on the withdrawal pages, the legal recap and the order confirmation page and email, no phone line for a seller without one (`test_trader_contact.py`, `test_withdrawal_views.py`, `test_checkout_legal.py`, `test_notifications.py`); strings in it, de, fr and es. Follow-up #846 (open) points the withdrawal texts to the seller details of the order confirmation. #922 (owner decision 2026-11-19): a seller to consumers must give a phone number, in the activation check and as a publish gate (`vendor_phone`), normalised by `engine.normalized_phone` (E.164 length); `sells_to_consumers` is true for every vendor until a business-only flag exists |
| ORD.31 | done | #734, #765 | "Order again" from a past order: still-available items into the cart at today's prices and shipping, skipped items with the reason, merge with the cart capped at stock, another customer's order is a 404; `add_many_to_cart` audited with a bulk-add entry; follow-up #765 moved the claim approval check into the service (CORE.18). The N+1 in `add_to_cart` is on #757 |
| VEN.9 | done | #743 | Vendor money dashboard in the Manager shop space: balance, transactions, payouts, documents; periods cut in the business time zone; the money CSV export is a sensitive export (owner only, fresh passkey, own shop). Pending and available split and the PDFs stay open (#757); human review: ledger |
| VEN.36 | done | #769, #803, #926 | Seller sales terms PDF of every version through the versioned documents (an old version stays downloadable through the order snapshot), the flag for a bulky shipping class without a return-cost estimate (`shipping/0015`); #803 puts the terms PDF template in the I18N catalog scope. #926: `not_returnable_by_post` is in the SHP.13 configuration format (version 3; versions 1 and 2 still read; #944 makes the CSV booleans strict). #937: the terms PDF is cached per version and language with an ETag, and `SalesTermsVersion.vendor_name` snapshots the shop name (`vendors/0038`, no backfill). The bulky-goods notice is open until ORD.17 |
| ING.2 | done | #739 | Source state, change history and stock state ported to models (`ingest/0003`), with the existing state tests on the new storage |
| ING.3 | partial: the follow-up of the #739 review is done (#861); the HTML adapter and the generic Store API adapter are not built | #861 | `record_source_state` reads the variations of a batch in one query and writes them with `bulk_create` and `bulk_update` (the query count no longer grows with the number of variations, `test_source_state_queries.py`); `latest_item_fields` reads the latest flagged value per field in one `DISTINCT ON` query and `record_item_change` uses it. Done-when (dry run equivalent to the current one) is not met: no adapters yet |
| ING.4 | done | #767 | Extraction (step 5) and JSON mappings into `MappingTable` with foreign keys (`ingest/0004`), audited, dry-run; the same output as step 5 on the 50-product sample fixture. #863 maps the CAT.22 import fields in the legacy catalog loader: whole-millimetre dimensions (at least 1), `country_of_origin` checked with `catalog.engine.country_error`, translatable `material` (120) and `colour` (80); an invalid optional value is dropped and listed in `report.problems`, the product still loads (`test_catalog_loader.py`) |
| THM.18 | done | #785 | Product card placeholder image: base and `ride`, decorative (`alt=""`), same image class as a real image; baselines refreshed |
| CMS.14 | done | #750, #804, #823 | Reusable snippet block (`cms/0027`): unpublished renders nothing, translation per page language, usage list; GDPR decision for `ContentSnippet`; #804 fails closed without the snippet workflow and warns before deleting a used snippet; #823 closes the remaining publish paths |
| CMS.18 | done in two parts | #774, #789 | Page revision retention: pure engine (12 months plus the last 10 per page), service with `dry_run` and audit, protects the live, scheduled and open-workflow revisions and rechecks them per delete batch (1/2); nightly job and command (2/2) |
| CAT.30 | done in two parts | #777, #790 | Support read right on the staff Catalog section and service refusals for every catalog write (1/2); read-only catalog screens: Support gets 403 on every write route, Finance still 404 (2/2) |
| CAT.10 | done | #745 | `upsert_products`, `publish` and `retire` with `dry_run` and `ChangeSet`, pure upsert engine, fixed query count. A vendor guard in `publish` and `retire` waits for CAT.19 or ING.7 (#757) |
| CAT.15 | done | #756, #828 | "Other sellers of this product": product page block (base and ride) and API listing; same GTIN, or same brand and MPN. #828: ordering by the consumer price and variant identifier matching |
| CAT.28 | done | #738 | Event search filters title, summary, venue and city in the database, keeping the EVT.8 fallback; query count does not grow with the catalogue |
| SEO.11 | done | #779 | Return node built from `effective_return_policy`; link to the vendor returns page; country falls back to the vendor, then `current_operator()` |
| SEO.12 | partial: 1 of 2 | #802 | Shipping service name and extra weight bands in the product JSON-LD (`EXTRA_BANDS`, under the 30 KB budget). The handling time from the vendor dispatch settings and VEN.20 closures is not built (no `handlingTime` in `apps/`); the variant identifiers moved to SEO.13 |
| SEO.13 | done in two parts | #798, #800 | Product dimensions, weight and origin as `QuantitativeValue` and ISO country (1/2); variant `gtin`, `mpn` and image with no dangling references and constant queries (2/2) |
| SEO.14 | done | #797 | The product page states the vendor return terms and the JSON-LD matches them (`test_return_terms_match_page.py`); no return fees without an approved policy; product page baselines with the Returns section. SEO.11 and SEO.14 follow the shop-pays default through #799 (spec, open) |
| VEN.40 | done: the follow-up is complete in three slices (#847, #848, #851) | #754, #778, #808, #847, #848, #851, #888 | Vendor return policy versions (`vendors/0035`): window of at least 14 days (also a DB check), who pays, methods, public page, country; staff review through versions; the shop pays by default (#778); GDPR decision for `reviewed_by`. #847 (1/3): the dedicated CORE.18 action `platform.return_policy` (Catalog and moderation and the owner) replaces the profile reopen right for approving a policy (`vendors.services.can_approve_return_policy`, reached through `PLATFORM_SECTION_CHECK`); known gap, check 45 finding 1: that check does not honour an owner granted directly. #848 (2/3): migration `vendors/0037`, a trigger on `vendors_returnpolicyversion` refuses any UPDATE or DELETE of an approved version except setting `reviewed_by_id` to NULL (the FK is `ON DELETE SET NULL`, so deleting a staff account still works). #851 (3/3): the sensitive action `return_policy_review` (`DEFAULT_SENSITIVE_ACTIONS`) gates the admin actions approve and reject through `level_gated_action`: no window gives the passkey prompt and no change (`test_return_policy_admin_level.py`). #888 (check 46 finding 2, closed): `help/*/route/admin__index.md` tells staff that approving or rejecting a return policy needs a passkey, confirmed just now or in an open window. Permissions code, needs human review |
| TRU.1 | partial: engine, rules, snapshots and read-only Manager page merged | #752, #806 | `apps/trust` (`trust/0001`): versioned `TrustRules`, pure engine, append-only snapshots (§16.11 cases, hysteresis, a new rules version changes a result and the old snapshot keeps its version), import contracts extended; the Manager page lists the rules versions (#806). The new-version form with the dry-run impact preview is #817, in train #819 |
| FIT.3 | done | #748, #793, #796, #827 | Vendor Fits tab (`fitment/0003`): mode, 50-product bulk with preview, proposals (the 21st pending is refused), copy, data-quality report, cross-tenant negative tests; shop role tests (#793); cascading node picker with a no-script fallback and a JSON route that is not a page (#796); typed search in the picker and its help (#827) |
| FIT.18 | done | #762 | Publish gate for undeclared products (`fitment/0004`): per-category setting, registered check, the catalog has no `fitment` import; help and the bulk action select width |
| I18N.20 | partial: parts 1, 2, the part 3 guard, 3b slices 1 to 3 and 3c (emails), and the vendor self-certification texts in de, fr and es (#941, AI drafts for the professional translator, not exported by `review-export`); the admin and editor templates of 3b are open | #742, #772, #788, #825, #832, #839, #841, #846, #868, #872, #883, #902, #941 | Storefront, events, blog, CMS block and form strings in the interface catalogs; a unit guard fails for a template with translate tags outside `SCOPES` and the exclusion list. Part 3b slice 1 (#825): the consumer-law storefront pages are in the catalogs. #832: the German withdrawal information follows the official model text. #839 (3b 2/n): customer template overrides are in the catalog scope. #841: the model withdrawal form as a PDF in five languages (`apps/compliance/documents.py`, public route `withdrawal_form_pdf`, no personal data). #846: the seller-contact pointer in the model withdrawal information and the blank form lists name, address, telephone and email and points to the order confirmation (page and email) and the checkout summary; it, de, fr, es catalogs moved with it (`test_language_catalogs.py`). #868 (3b 3/n): the remaining CMS page templates. #872 (3c 3/n): the compliance emails (withdrawal acknowledgement and vendor notice, statement of reasons, termination, notice decision and received, GPSR staff mails) in it, de, fr and es; the de, fr and es legal wording is an AI draft behind I18N.19 and needs human review. #883: `test_fits_translated.py` checks every msgid, the register and exact matches. #902 (owner decision): the vendor recruitment page and the DSA notice pages use the formal register; the spec row now says so. The rest of 3b (admin and editor templates, kept in the tested `EXCLUDED` list) is open |
| I18N.10 | partial: 1/2 (lane-b forms, services and API, #830) and the admin forms (#870, #890); the lane-a half (#936: accounts, orders, payments, ledger and tax inputs, `NFCAdminForm`, `nfc_exact_fields`, never a password field) and the Manager staff forms (#940, `NFCForm`); the importers and the other `InlinePanel`s are open, so the Done-when is not met | #830, #870, #890, #936, #940 | User text normalised to NFC at the service boundary (`apps/core/text.py`, `nfc_forms.py`, API text schemas); the F3 pasted-text case is no longer an xfail. #870: the CMS page admin forms and StreamField blocks (`apps/cms/forms_pages.py`, `NFCInlinePanel`). #890: the site settings forms (Footer, SEO, Events). Open: the importers, and the other `InlinePanel`s on page models, which have the same child-form gap (switch them to `NFCInlinePanel`) |
| I18N.17 | done | #895, #904 | `apps/seo/tests/integration/test_hreflang_sitemap_rules.py` runs under base and ride: `x-default` only when the default-language page exists, CMS alternates reciprocate and list themselves, retired and suspended-vendor products are in no sitemap and no alternate set, and a sample of sitemap URLs agrees with its own hreflang. #904 (owner decision): a retired product page prints no hreflang at all (`apps/catalog/engine.py`, `views.py`); the spec row says so |
| EVT.10 | done | #747, #875, #930 | Event JSON-LD (`event_jsonld`) in the page graph for fairs and located or online events; script injection tests; hidden, pending and noindex pages are not in the sitemap. Follow-up in two slices: 1/2 (#874, check 47) adds the optional `ticket_url`, `price`, `is_free`, `postponed` and `previous_start` to `EventPage` (migration `events/0006`, synchronised across translations, validated in `clean()`, in the Wagtail panels and the Manager shop event form); 2/2 (#875) is the output (`seo/event_graph.py`, Cancelled wins over Postponed, `previousStartDate` only when postponed, `offers` only with a ticket link and price or free entry, the Postponed badge, `events/0007` for https ticket links), the `offers` node, `eventStatus`, `previousStartDate` and the Postponed badge. The offer and postponed model is an assumption awaiting the owner (an optional ticket link and price or free entry, no stock; postponed a checkbox, not a status; one currency) |

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
| SHP.14 | `apps/shipping/migrations/0009_seed_special_areas.py` | The seed covers less than the task row (UK Highlands and Islands, Ionian islands, Mount Athos, Saint-Barthélemy and Saint-Martin) (resolved by SHP.15, #519) |
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
| #281 | none | Coherence check 19 |
| #282 | none | CI: E2E Chromium install retries and Playwright cache |
| #288 | none | CI: E2E Chromium install fails fast when the apt mirror is down |
| #272 | none | Spec: WeightBand surcharge, VEN.32, OPS.11, CMP.33, ARC.30, ARC.31, ARC.32 (field help rule and help button) |
| #275 | CMS.10, CMS.11 | Product cards block and searchable product chooser, products in more StreamField bodies |
| #242 | none | Spec: D-38 garage and fitment (spec 17, FIT.1 to FIT.16) |
| #278 | OPS.5 | Caddy by systemd socket activation, so the proxy sees the client address |
| #263 | CMP.6 | Price history and the Omnibus reference price (1/2) |
| #285 | ARC.29 | Refuse demo mode and forced noindex on LIVE |
| #270 | CORE.4 | MFA redirect keeps a safe next for POST |
| #276 | ORD.9 | Delivered and cancelled order emails (2/3) |
| #271 | SEO.8 | SEO settings panel with the indexing toggle (2/2, partly) |
| #277 | ARC.30 | Field-help sweep, allow-list, baselines and guideline |
| #295 | OPS.11 | Parity smoke deploys the previous tag, then the candidate |
| #298 | none | ADR-0010: PDF library for order confirmations (ORD.9 3/3 step 1) |
| #215 | OPS.8 | Manager developer documentation |
| #290 | none | Test fixes: order-dependent endpoint sweep, a token literal and a sleep removed |
| #294 | none | Coherence check 20 |
| #303 | none | Spec: I18N review export and import, pre-LIVE native review (I18N.13, I18N.14) |
| #293 | none | Spec: wave 16 follow-ups (ARC.32, CORE.27, ORD.16, SEO operator profile) |
| #286 | FIT.1 | Fitment scheme and nodes, data model and services (1/2) |
| #328 | none | Coherence check 21 |
| #311 | ARC.4 | Draft PRs run the light CI jobs only |
| #309 | none | Spec: owner decisions 2026-11-11 (D-05, D-06, D-09, D-10, D-14, D-16, D-19, D-20), rows VEN.33, PAY.5, PAY.6, MIG.16, SEO.9, I18N.15 |
| #313 | none | Spec: adviser opinion 2026-11-11 (D-02, D-03, D-08, D-15, L-10, L-11, T-1 to T-8), rows TAX.17, TAX.18 |
| #314 | none | Spec 13: legacy map, rows MIG.17 to MIG.21 |
| #266 | CMP.4 | Sub-processor register, retention job, self-service GDPR and staff views (2/3) |
| #302 | ARC.31 | Plain-language field help for the vendor models (3/5) |
| #305 | I18N.13 | Export and re-import of texts for native-speaker review |
| #280 | CAT.20 | Brand proposals, pending limit, batch proposals, review, merge with redirects, rejection notice (2/3) |
| #284 | CAT.20 | Brand page content, JSON-LD graph, staff logo upload (3/3) |
| #349 | none | Coherence check 22 |
| #216 | VEN.27 | No-TIN jurisdictions as effective-dated staff data |
| #359 | none | Coherence check 23 |
| #301 | ARC.31 | Plain-language field help for the catalog models (2/5) |
| #307 | ARC.31 | Field help for the Manager shell and forms (4/5) |
| #264 | VEN.31 | Documents in the vendor's own collection, not linkable until ARC.15 (2/2) |
| #332 | none | Fix flaky redaction performance test |
| #279 | ARC.32 | Contextual help button, shared component, storefront, account and Manager (1/4) |
| #308 | ARC.31 | Field help for the remaining apps (5/5) |
| #368 | none | Coherence check 24 |
| #287 | ARC.31 | Field help for the events and CMS models (1/5) |
| #225 | VEN.2 | VendorPlan and versioned CommissionRule |
| #289 | ARC.32 | Help button in the account and Manager spaces (2/4) |
| #283 | ARC.32 | Help button in the Wagtail and Django admins (3/4) |
| #378 | ARC.32 | Wagtail help stale-answer e2e test made deterministic |
| #273 | CMP.6 | Online withdrawal (2/2) |
| #321 | I18N.15 | MPMV_LANGUAGES: configurable site languages |
| #381 | OPS.12 | Temporary staff Site links page |
| #382 | CORE.25 | Passkey window button in the banner and an account security page |
| #384 | CAT, CORE | Product detail links stopgap (PRODUCT_PAGES_ROUTED) and MFA_LOGIN_URL |
| #383 | EVT.1 | Event publish e2e test closes the date pickers deterministically |
| #304 | ORD.9 | Per-vendor confirmation PDF (3/3) |
| #357 | ORD.7 | Customer area: My orders, timeline, tracking |
| #389 | CORE.25 | Owner, support and finance roles hold core.elevate |
| #391 | none | Coherence check 25 |
| #231 | CORE.9 | Platform review and revert of storefront profile changes (part 3, CORE.9 stays partial) |
| #386 | CAT.7 | Storefront product page (1) |
| #393 | CORE.25 | Passkey window wording |
| #399 | none | Coherence check 26 |
| #223 | TAX.15, TAX.16, ORD.13, CMP.28 | Spec: D-28 B2B VAT rows (dispatch proof, triangulation, declaration of intent, special-territory vendors) |
| #241 | TRU.1 to TRU.11, CMS.13 to CMS.16 | Spec: D-37 trust badges and community section (proposal), D-36 CRX follow-up rows |
| #243 | PRM.1 to PRM.16 | Spec: D-39 coupons, abandoned-cart recovery and return incentive |
| #297 | ARC.15, SEC.15, OPS.13 | Design: private vendor documents (ADR-0009) |
| #345 | SEO.7 | Organization JSON-LD schema check and operator provenance tests |
| #292 | SEO.8 | Go-live indexing warning and operator Organization JSON-LD |
| #343 | CMP.5 | Versioned terms clauses (a) to (h), EN and IT |
| #360 | ORD.5 | Cart and checkout UI (HTMX), B2B fields, VIES |
| #337 | CMP.4 | Privacy pepper, LIVE and STAGE check, email suppression list |
| #265 | CORE.18 | Role assignment service and the Staff and roles page (2/2) |
| #334 | CMP.2 | Commission statement lines and carry-over credit |
| #296 | CMP.33 | Data export needs a fresh second factor |
| #325 | TAX.17, TAX.18 | Northern Ireland goods rules and shipping VAT (with the content of the closed #323) |
| #353 | ORD.10 | Checkout and payments kill switch, alert on a payment without transfer |
| #354 | SHP.4, ORD.3 | `build_checkout`: per-vendor packages, tax, shipping, blocking messages |
| #351 | PAY.1, PAY.2 | Stripe provider and the signed idempotent webhook |
| #350 | VEN.4 | Append-only `LedgerEntry`, balance and statement |
| #318 | VEN.33, PAY.4 | Pure refund fee engine |
| #326 | PAY.5 | `PaymentProvider` interface, registry, dev page |
| #402 | THM.12 | Linear-time SVG sanitiser test |
| #299 | ORD.16 | Order emails in DE, FR and ES, vendor mail language |
| #419 | none | Coherence check 27 |
| #291 | CMP.5 | 30-day termination notice (4/4) |
| #356 | VEN.6 | Stripe Express onboarding and the effective sales mode |
| #421 | OPS.14 | Staff-only release indicator |
| #420 | SEO.10 | Spec: vendor role in product structured data |
| #426 | CMP.5 | Clauses (a) to (h) after the owner's legal review |
| #336 | MIG.1 | Read-only legacy source connector (1) |
| #333 | MIG.6 | Legacy redirect map engine (1) |
| #430 | CMP.4 | De-flake the suppression audit test |
| #409 | VEN.3 | `compute_commission` and `refund_lines` |
| #429 | ORD.4, PAY.1, PAY.2, ORD.5 | Checkout creates the PaymentIntent, the webhook marks the order paid, `payments.E004` |
| #331 | FIT.6, FIT.17 | Garage cookie validation and the order vehicle snapshot |
| #341 | MIG.3 | Catalog loader (1) |
| #330 | SEO.5, SEO.9 | Consent banner and `ConsentProvider` |
| #437 | none | Coherence check 28 |
| #448 | VEN.35 | Seller versioned sales terms |
| #449 | VEN.35, VEN.36 | Spec: VEN.35 in two parts, `require_sales_terms` |
| #452 | PAY.3 | Ledger lines from the paid order and per-vendor transfers (D-08) |
| #454 | TAX, CORE, VEN | Spec: adviser rules for RF20 and XI, register decisions MQ-38 to MQ-44 |
| #458 | SEC.2 | SVG linear-time test retry |
| #460 | SEO.9 | Cookie settings: saved notice and checkbox sync |
| #462 | none | Coherence check 28 follow-ups |
| #463 | ORD.17 | Checkout legal step (D-40) |
| #465 | PAY.2 | Connect webhook signing secret |
| #467 | ORD.5, VEN.2 | Checkout takes the commission rule in force |
| #469 | ORD.19 | Add to cart from the product page |
| #472 | none | Coherence check 29 |
| #473 | ORD.17 | Checkout legal step follow-ups (ORD.17b) |
| #474 | CORE.25, VEN.6 | A shop owner confirms its own shop's owner actions (D-32 amendment) |
| #477 | PAY.3 | Transfer key names destination and amount |
| #479 | VEN.38, VEN.19, VEN.33 | Spec: vendor economic configuration (D-43) |
| #480 | ARC.10, VEN.35, ORD.17 | Demo seed publishes sales terms and onerous clauses |
| #482 | none | Coherence check 30 |
| #476 | PAY.4 | Refunds through and outside the platform, duplicate payment refunds |
| #485 | PAY.7, PAY.4 | Spec: vendor refund action with isolation tests |
| #487 | none | Coherence check 31 |
| #484 | CORE.30 | Manager platform map |
| #488 | CAT.21 | Renumber CAT.5b so the backlog lists it |
| #489 | SEC.5 | Supply chain and secret settings (items 6 and 7) |
| #490 | SEC.5 | Attack scenarios: accounts and request tampering (item 10, part 1) |
| #491 | CORE.24 | Sessions part 1: index, invalidation, account list, Support end and panic button |
| #496 | ARC.34 | CI speed: shard the test job, skip heavy jobs on docs-only PRs |
| #497 | CORE.25 | Harden shop-scoped passkey confirmations (#474 follow-ups) |
| #498 | ARC.20 | Lane-b's own GitHub account |
| #500 | ARC.20 | Lane review required check |
| #502 | THM.8 | ride theme: customer area and authentication |
| #503 | THM.8 | ride theme: cart and checkout |
| #505 | VEN.39, CORE.31, I18N.4 | Spec: owner decisions 2026-11-15 |
| #507 | ARC.34 | CI on ready_for_review |
| #508 | VEN.34, CMP.2, CMP.5, CMP.34, ARC.35 | Spec: tax adviser answers 2026-11-15 (D-46) |
| #510 | CORE.25 | Unscoped passkey window only for elevate |
| #512 | THM.13 to THM.16, THM.7, THM.9, THM.10 | Spec: ride theme on Bootstrap 5.3 (Q-98) |
| #514 | THM.13 | Bootstrap foundation (1/3) |
| #515 | THM.13 | Token map, budgets, CSP lint (2/3) |
| #516 | THM.13 | Components and a11y (3/3) |
| #517 | none | Coherence check 32 |
| #519 | SHP.15 | Special areas |
| #520 | CORE.24 | Role-based session lifetimes (part 2) |
| #522 | THM.13 | Follow-up |
| #524, #525, #526, #527 | OPS.4 | Dependabot bumps |
| #528 | THM.16 | Cart and checkout on Bootstrap (1/2) |
| #530 | THM.16 | Account and auth on Bootstrap (2/2) |
| #534 | ORD.20 to ORD.23 | Spec: order amendments (D-47) |
| #538 | ORD.20 to ORD.23 | Spec: joint tax and legal opinion (VAT timing, territory checks, 14-day refunds, clause (l)) |
| #540 | none | Coherence check 33 |
| #457 | TRU | Spec: owner answers to Q-T1 to Q-T13 |
| #532, #536, #541 | THM.15 | ride navbar (1/3), search overlay and mini-cart (2/3), `orders:cart_mini` fragment |
| #553 | CMP.5 | Flaky counter-key test fix |
| #543, #549, #555 | EVT.4 | Vendor events: list and form, blocks, images, map picker, staff side |
| #551 | ORD.20 | Order amendments 1/3: versions, engine, classification |
| #556 | CORE.32, CORE.33 | Spec: customer sign-up and password reset |
| #558 | THM.16 | e2e at three widths for every moved page |
| #559 | CORE.24 | Session lifetime boundary tests |
| #564 | ORD.20 | Order amendments 2/3: money-moving changes, refunds, passkey |
| #577 | ORD.20, ORD.22 | Order amendments 3a/3: Manager, staff and customer UI, passkey prompt |
| #624 | ORD.20, ORD.22 | Order amendments 3b-1: notices, invoice notice, staff-change notice, cancellation counts |
| #565 | TAX.12 | Consumer prices include VAT |
| #569 | none | Coherence check 34 |
| #570 | CMP.34, TAX.22, ORD.11, ORD.25, TRU.3, PAY.5 | Spec: owner answers of 16 November |
| #580 | VEN.13, VEN.16, CMP.5 | Spec: clause (l) counsel answer |
| #585 | SEO.5, MIG.22 | Spec: Matomo replaces Google Analytics; domains, mail and TLS at cutover |
| #589 | CAT.22, CAT.23, VEN.40, SEO.13 to SEO.15, I18N.16 to I18N.18, CMS.17 | Spec: follow-up rows from the SEO.1, I18N.6 and THM.15 reviews |
| #561 | SEO.1 | Product structured data, breadcrumb, return and shipping nodes, canonical |
| #563 | I18N.6 | hreflang, canonical and per-language sitemaps without fallback copies |
| #571 | THM.14 | Ride carousels (1/3: hero slider, strips, gallery) |
| #572 | FIT.1 | Manager pages for the fitment scheme, nodes and categories (2/2) |
| #573 | none | Spec: D-48 accounting connector, data flow to vendors' accounting software |
| #575 | SEO.2 | Per-type sitemaps and generated `llms.txt` (a page's own `noindex` keeps it out) |
| #576 | EVT.6 | Public event pages: list, filters, calendar, archive, event page, ICS |
| #578 | I18N.15 | `change_languages` command, language redirects, check `core.E914` (`core/0029`) |
| #579 | THM.14 | Ride reveal, counters, progress, section title, countdown, video modal (2/3) |
| #582 | I18N.15 | Non-English integration suite: country-group seed with a non-English primary language |
| #591 | THM.14 | CMS wiring: hero layouts and slides, strip blocks, showcase blocks (3/3; `cms/0019` to `0021`) |
| #593 | none | Coherence check 35 |
| #597 | I18N.15 | Tests independent of English (non-English integration run) |
| #583 | FIT.2 | Product fitment declaration, matching engine, `set_product_fitment` (`fitment/0002`) |
| #584 | THM.15 | ride page banner, footer chrome, back-to-top (3/3) |
| #586 | EVT.8 | Multilingual events (closed on GitHub; train #627 merged its commits) |
| #592 | CAT.4, CAT.24 to CAT.26 | Product images: renditions job, dedup, cover lock, image language (`catalog/0016`) |
| #606 | none | Coherence check 36 |
| #607 | OPS.9 | ZAP 10202 accepted for the consent form, with a CSRF form sweep |
| #610 | THM.15 | Navbar e2e waits for Bootstrap's shown events |
| #616 | CMP.5, CMP.40 | Spec: vendor clause (m) on restriction, suspension and termination; placeholder publish guard |
| #618 | ORD.20, D-48 | Spec: tax adviser opinion 2026-11-17, recompute rules AM1 to AM11 |
| #619 | ORD.20 | Adviser recompute rules in the amendment engine, shipping supplement at the second payment |
| #602 | I18N.16, I18N.18 | Sitemap version on non-page changes (`core/0030`), absolute site root or fail loudly |
| #612 | SEO.7 | Site-wide schema.org page graph (part 1) |
| #621 | CAT.7 | Product page gallery images and related products (2/2) |
| #588 | I18N.7, I18N.19 | German, French and Spanish AI-draft catalogs (gated), review export |
| #624 | ORD.20 | Part 3b-1: amendment notices, invoice notice, staff-change notice (`orders/0020`, `core/0031`) |
| #625 | SEO.5 | Spec: Matomo consent-free under counsel's conditions |
| #629 | EVT.9 | Event feeds (RSS, ICS), caching, rate limit |
| #632 | CAT.27 | Spec: public search results page |
| #633 | none | Coherence check 37 |
| #636 | ORD.29 | Spec: packages with shipping split across VAT rates |
| #640 | EVT.9 | Feed discovery, Subscribe block, `llms.txt` feeds (2/2) |
| #641 | ARC.36, ARC.37 | Spec: help content in the repository, coverage guard, backfill |
| #614 | I18N.15 | Tests collect and pass without a third language (`it,de` CI job): no language literals, no import-time language lookups |
| #617 | EVT.11 | Event emails through the outbox, review digest, DSA notice target, statements page |
| #630 | EVT.7 | Storefront Upcoming events section |
| #628 | THM.7 | Ride catalog listings on Bootstrap (part 1) |
| #634 | CAT.27 | Public search results page (rate limit, `noindex`, ride header search) |
| #646 | THM.7 | Ride product page and storefront on Bootstrap (part 2) |
| #649 | none | Coherence check 38 |
| #660 | none | Coherence check 39 |
| #635 | CAT.4 | Pages never build renditions; fall back to the original image |
| #647 | ARC.36 | Help content in the repository, `sync_help`, coverage sweep (`cms/0022`) |
| #661 | ARC.37 | Help for the customer account, cart, checkout and withdrawal screens (part 3) |
| #664 | I18N.13 | Register fixes: customer privacy page informal, second-factor pages formal |
| #667 | THM.17, THM.7 | Ride search results page on Bootstrap |
| #668 | CMP.5, CMP.24 | Spec: clause (m) approved by counsel 2026-11-18 |
| #666 | ARC.36 | Help entry add view gated, compliance contact in the statements help (part 2) |
| #662 | ARC.37 | Help for the Wagtail and Django admin screens and the page types (parts 4 and 5) |
| #654 | EVT.9 | Feed links once per request, distinct titles, review tests |
| #670 | OPS.15, OPS.16 | Spec: update automation and image scan (owner decision Q-82) |
| #674 | I18N.19 | EVT.11 statements and moderation mails in the sensitive review export |
| #671 | CAT.29 | Blog article and author pages without request-time renditions |
| #673 | SEO.7 | `SearchAction`, `CollectionPage`, `ItemList`, `SearchResultsPage` (part 2) |
| #681 | THM.14 | Video embed origins for the CSP as a validated setting (`cms.E003`) |
| #675 | ARC.37 | Help for the vendor Manager screens (part 6) |
| #683 | EVT.8 | Suspended-vendor tests on the translation write paths, translate page text |
| #686 | CAT.25 | Orphan MediaImage cleanup: staff review list and audited delete |
| #676 | THM.7 | Product page specifications and fitment, both themes (part 3) |
| #694 | none | Coherence check 40 |
| #672 | I18N.13 | Vendor application flow in the catalogs (formal) |
| #685 | CAT.11 | Catalog section of the Manager platform space |
| #684 | EVT.14 | Tile proxy with object-storage cache, validation and rate limit |
| #677 | ARC.37 | Help for the platform staff Manager screens (part 7) |
| #680 | THM.9 | Ride content and system pages on Bootstrap (part 1) |
| #678 | CAT.4 | Render job split by preset group, hero first |
| #695 | SEC.5 | Retry pip-audit on a network timeout |
| #689 | ARC.37 | Customer help centre from the help files, linked from the dialog and the footer |
| #687 | CMS.13 | Editor-built form pages with Turnstile and retention (`cms/0023`) |
| #691 | SEO.15 | Seller name is never a natural person |
| #700 | CAT.22 | Product physical and origin data (`catalog/0017`) |
| #704 | CAT.24 | Image rendition monitoring: staff view, retry, alert |
| #702 | EVT.15 | Public event maps on the tile proxy |
| #708 | CMS.17 | Footer columns and social profiles in the CMS |
| #714 | CAT.23 | Variant identifiers and cover image |
| #719 | CMS.15 | Download block |
| #722 | THM.10 | Visual regression baselines |
| #724 | CMS.16 | Accordion block |
| #712 | none | Coherence check 41 |
| #717 | ORD.30 to ORD.32 | Spec: actionable package list, order again, staff draft |
| #697 | CAT.25 | Follow-up: storage cleanup tests, reference owners, pagination |
| #698 | I18N.13 | Register follow-up: formal vendor Manager strings, Italian apostrophes |
| #706 | THM.9 | Ride carousels proof, consent banner styling, video modal (part 2) |
| #710 | EVT.14 | Single-flight for a cold tile |
| #711 | CAT.24 | Query limit, aggregated alert, retry limits |
| #718 | EVT.14 | HEAD on a tile, single-flight unlock failure, pooler note |
| #731 | EVT.14 | Deterministic single-flight concurrency test |
| #736 | none | progress.md rows for nine merged items |
| #723 | ORD.30 | Platform list of packages staff can still act on |
| #733 | CAT.26 | Variant galleries on the product page |
| #740 | ORD.29 | Packages with shipping split across VAT rates (`orders/0021`) |
| #737 | CORE.18 | Owner-role grant needs the vendor approval matrix permission, TODO cleanup |
| #727 | none | Lane board points at #726 |
| #734 | ORD.31 | Order again from a past order |
| #738 | CAT.28 | Database-side event search |
| #739 | ING.2 | Source state, change history, stock state |
| #742 | I18N.20 | Part 1: storefront strings in the interface catalogs |
| #743 | VEN.9 | Vendor money dashboard |
| #744 | CAT.24 | Remove the render_product_image alias |
| #745 | CAT.10 | upsert, publish and retire services |
| #747 | EVT.10 | Event JSON-LD in the page graph |
| #748 | FIT.3 | Vendor Fits tab |
| #750 | CMS.14 | Reusable snippet block |
| #752 | TRU.1 | Trust app: rules, engine, snapshots |
| #754 | VEN.40 | Vendor return policy data |
| #755 | none | Coherence check 42 |
| #756 | CAT.15 | Other sellers of a product |
| #758 | none | Spec follow-ups of the 18 Nov reviews |
| #759 | ORD.29 | Settlement test for split shipping VAT |
| #762 | FIT.18 | Publish gate for undeclared products |
| #765 | CORE.18, ORD.31 | Claim approval check in the service, bulk add audit |
| #766 | ORD.26 | Order email outbox keeps the rendered message |
| #767 | ING.4 | Extraction and mappings into MappingTable |
| #768 | none | Spec: owner decisions of 2026-11-18 |
| #769 | VEN.36 | Seller sales terms PDF, bulky return-estimate flag |
| #770 | ARC.37 | Seller guide in five languages |
| #772 | I18N.20 | Part 2: events, blog, CMS and form strings |
| #774 | CMS.18 | 1/2 Revision retention rule and service |
| #777 | CAT.30 | 1/2 Support read right, service refusals |
| #778 | VEN.40 | The shop pays the return shipping by default |
| #779 | SEO.11 | Return policy completeness in the product graph |
| #782 | none | Spec: orders API as a paid entitlement |
| #785 | THM.18 | Product card placeholder |
| #788 | I18N.20 | Part 3: template scope guard |
| #789 | CMS.18 | 2/2 Nightly retention job |
| #790 | CAT.30 | 2/2 Read-only catalog screens for Support |
| #792 | SEO.10 | 1/2 Vendor role fields and forms |
| #793 | FIT.3 | Shop role tests for the Fits pages |
| #794 | SEO.10 | 2/2 Vendor role in the product JSON-LD |
| #796 | FIT.3 | Cascading node picker |
| #797 | SEO.14 | Vendor return terms on the product page and in the JSON-LD |
| #798 | SEO.13 | 1/2 Product dimensions and origin |
| #800 | SEO.13 | 2/2 Variant identifiers and images |
| #802 | SEO.12 | Shipping service name and extra weight bands (1 of 2) |
| #803 | VEN.36 | Terms PDF template in the catalog scope |
| #804 | CMS.14 | Fail closed, warn before deleting a used snippet |
| #806 | TRU.1 | Part 2, 1/2: TrustRules versions in the Manager |
| #807 | SEO.15 | Public seller name on every surface |
| #808 | VEN.40 | Deflake the cross-shop return policy view test |
| #821 | none | Coherence check 43 |
| #820 | THM.16 | Bootstrap grid wins over base grids in ride listings and cart |
| #799 | SEO.11, SEO.14 | Spec: follow the shop-pays default (Q-105) |
| #771 | ARC.38 | Spec: the demo seed follows the operator's languages |
| #787 | none | Docs: coherence checks include a stale-items sweep |
| #823 | CMS.14 | Follow-up: snippets fail closed on every publish path |
| #824 | ARC.37 | Help labels match the shipped catalog on every route |
| #825 | I18N.20 | 3b, slice 1: consumer-law storefront pages in the catalogs |
| #828 | CAT.15 | Follow-up: order other sellers by the consumer price, match variant identifiers |
| #830 | I18N.10 | 1/2 Store user text in NFC (lane-b forms, services and API) |
| #827 | FIT.3 | Follow-up: typed search in the node picker, and its help |
| #838 | none | Coherence check 44 |
| #835 | SEO.15 | Follow-up: the search vendor group filters by public name in the database |
| #832 | I18N.20 | German withdrawal information against the official model (Art. 246a EGBGB) |
| #837 | CMP.41 | Seller address and phone in the withdrawal information |
| #839 | I18N.20 | 3b 2/n: customer template overrides in the catalog scope |
| #834 | I18N.15 | API catalog tests work under any primary language; the API language defaults to the primary language |
| #841 | I18N.20 | Model withdrawal form as a PDF in 5 languages |
| #775 | ORD.32 | Staff draft from a past order with a payment link |
| #847 | VEN.40 | 1/3 Dedicated CORE.18 matrix action for approving return policies |
| #854 | none | Coherence check 45 |
| #846 | I18N.20 | Withdrawal texts point to the seller details in the order confirmation |
| #850 | SEC.5 | Item 10 part 2a: resource-abuse attack scenarios (event body depth fix) |
| #852 | CORE.18, CORE.23 | A directly granted platform owner holds the owner role in every section check |
| #853 | SEC.5 | Item 10 part 2b: ReDoS scenarios (fast sweep per PR, 100 KB sweep nightly) |
| #856 | SEC.5 | Item 10 part 3a: business-logic attack scenarios (whole-number cart quantity) |
| #858 | SEC.5 | Item 10 part 2c: information leaks and the API 413 problem |
| #851 | VEN.40 | 3/3 passkey step-up on return-policy approval |
| #848 | VEN.40 | 2/3 immutability trigger on approved return-policy versions |
| #860 | SEC.5 | Item 10 part 2d: AI-agent injection scenarios (`strip_hidden`) |
| #861 | ING.3 | Follow-up: bulk-read variations and latest-per-field history read |
| #864 | SEC.5 | Item 10 part 2d follow-up: more hidden characters, llms.txt caps, real-route coverage |
| #863 | ING.4 | Follow-up: map the CAT.22 import fields |
| #880 | none | Coherence check 46 |
| #870 | I18N.10 | NFC on the CMS page admin forms and StreamField blocks |
| #878 | SEC.5 | `strip_hidden` by Unicode category (Cf, Cc), keeping VS16 |
| #883 | I18N.20 | Stronger Fits translation test |
| #868 | I18N.20 | 3b 3/n: the remaining CMS page templates |
| #888 | VEN.40 | Help for the `return_policy_review` sensitive action |
| #872 | I18N.20 | 3c 3/n: compliance emails |
| #889 | EVT | Events template tests work under any primary language |
| #893 | SEC.5 | `strip_hidden`: tag block always stripped, notes on `unicodedata` and private use |
| #892 | SEC.5 | ReDoS rewrites: parity cases on real valid input |
| #890 | I18N.10 | NFC on the site settings forms (Footer, SEO, Events) |
| #895 | I18N.17 | Deferred hreflang and sitemap tests |
| #900 | THM.9 | Fix the flaky ride video-modal e2e |
| #874 | EVT.10 | Follow-up 1/2: event offers and the postponed state (model and editor) |
| #901 | SEO.8 | 2/2 platform-opening checklist reminder |
| #896 | FIT.5 | 1/3 garage profiles: model and services |
| #899 | SEO.8 | 2/2 per-page SEO fields |
| #902 | I18N.20 | Formal register for the vendor recruitment and DSA notice pages |
| #904 | I18N.17 | Retired products print no hreflang |
| #905 | FIT.5 | 3/3 GDPR export and erasure, 24-month inactivity cleanup |
| #898 | FIT.5 | 2/3 My garage page |
| #910 | FIT.6 | 1/2 merge at sign-in: staging, Add and Not now |
| #913 | FIT.6 | 2/2 merge dry run and sign-out cookie clearing |
| #925 | none | Coherence check 47 |
| #914 | SEO.7 | 1/2 widget lists in the page graph |
| #926 | VEN.36 | Follow-up: `not_returnable_by_post` in the shipping config format |
| #931 | PAY.4 | Stray capture on a cancelled or mismatched order alerts support |
| #920 | I18N.15 | SEO tests are language-agnostic (1/2) |
| #929 | ARC.37 | Seller guide: catalogue and images, shipping, API and accounting |
| #922 | CMP.41 | Follow-up: phone required for sellers who sell to consumers |
| #933 | FIT.5 | Follow-up: garage inactivity retention is a setting |
| #930 | EVT.10 | Help: the Tickets and changes section of the event form |
| #927 | I18N.15 | Catalog tests are language-agnostic (2/2) |
| #923 | SEC.5 | Item 10 part 3a-2: payment and refund attack scenarios |
| #918 | I18N.19 | 1/n per-language review mark and gate check |
| #938 | PAY.4 | Follow-up: orphan-capture alert, manual close and 14-day auto-close |
| #959 | I18N.15 | e2e suite passes under any primary language (1/7) |
| #946 | I18N.15 | Help button sweep passes under any primary language |
| #944 | SHP.13, CMP.41 | Review follow-ups: strict CSV booleans, fictional test phones, rows VEN.41 and VEN.42 |
| #917 | SEO.7 | 2/2 card widget lists in the page graph |
| #940 | I18N.10 | Follow-up: NFC for the Manager staff forms |
| #937 | VEN.36 | Follow-up: terms PDF cache and headers, vendor name snapshot |
| #948 | THM | Spec: ride visual-fidelity tasks THM.19 to THM.35 and colour palettes |
| #941 | I18N.20 | Vendor self-certification texts in de, fr and es; EVT.8 tests language-neutral |
| #936 | I18N.10 | NFC for accounts, orders, payments, ledger and tax inputs |
| #875 | EVT.10 | 2/2 event offers and postponed status in JSON-LD and on the page |
| #924 | SEC.5 | Follow-up: outbound transports never follow redirects |
| #915 | ORD.19 | Unpriced products cannot be added to the cart |
| #962 to #966 | I18N.15 | e2e suite passes under any primary language (3/7 to 7/7), merged by trains #983 and #985 |
| (this PR) | none | Coherence check 48 |

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

### Coherence check 20 (main at 065124691400bbe20cf9192ef534a63ad67c8623)

Scope: main at 0651246 (merge of #278), the merges since check 19 (c9ac9d9): #282 and #288 (CI E2E install), #281 (check 19), #272 (spec wave 15 rows), #275 (CMS.10 and CMS.11, product chooser), #242 (spec D-38 garage), #278 (Caddy socket activation).

| Area | Result |
|---|---|
| Code vs spec | CMS.11: the done-when items map to `test_product_cards.py` (same block on the page types, retired or unpublished product disappears, no price for `on_request`, named `ItemList` without offers, grid plus cards give two lists) and `test_product_chooser_search.py` (search by name, SKU, brand and vendor, filters, vendor member scoping, crafted ids answer 404, non-numeric ids no longer give 500); vendor event body rules in `events` unit and integration tests; e2e screenshot of the chooser. #278: the unit tests cover the six listeners and their order, the Caddyfile fd binding derived from the unit, the deploy script paths (install, enable, rollback, a socket that cannot bind) and the release gate; the end-to-end client address check is in the parity smoke (CI, amd64 and arm64); the real systemd and Podman path on the host is lane-a's test. #282 and #288 are workflow only: the job names are unchanged and the E2E job is green on main. Spec-only PRs (#272, #242) have no code. |
| Spec and docs | New rows exist in `90-backlog.md` and `backlog.csv` (315 lines): ARC.30, ARC.31, ARC.32, VEN.32, CMP.33, OPS.11 and FIT.1 to FIT.16 (spec 17 is linked from the spec README and spec 01); IDs unique; `make docs` freshness passes. D-38 exists in `92-open-decisions.md` as decided, with Q-G1 and Q-G2 open with counsel and not blocking. `docs/dev/admin-access.md`, `docs/dev/release.md` and `deploy/README.md` describe the socket unit and the one-time switch. Spec 10 and 15 are updated for the cards and the vendor event body. |
| Hygiene | `makemigrations --check` clean (inside `make check`). New: cms 0015, events 0004. Chains linear, no duplicate numbers: cms 0001..0015, events 0001..0004; other apps unchanged since check 19 (accounts ..0007, api ..0007, catalog ..0011, compliance ..0005, core ..0021, ingest 0001, manager ..0002, orders ..0011, shipping ..0010, tax ..0009, themes 0001, vendors ..0026). No print, FIXME or credential-like literal in the delta; no private name. One TODO(TAX.12) in `product_cards.html`, the same interim net price note as the grid. |
| Gates | `make check` pass on 0651246 (exit 0). Main CI on 0651246: success. Full suite not run locally. |

Findings and actions:

1. Minor. #275 `choosers._word_matches` still reads `brand__name`; CAT.20 2/3 (#259 follow-up) may change `Brand`, then the search and the brand filter need the same edit. Action: do it in the CAT.20 PR that changes the brand model.
2. Minor. #275 leaves open: no block-level validation of a foreign product id in a hand-posted page form (chooser and event services are scoped); the item `@id` is the product page URL until SEO.7 defines the product node id; the card price is the interim net price (TAX.12); Italian for the new help texts depends on ARC.30. Owner question on #275: the vendor event grid sourced from the vendor itself (confirm, or cards only).
3. Minor. The FIT hours differ between the PR text of #242 (164 h) and the merged spec, D-38 and spec 91 (170 h of P1); the merged documents agree with each other. No action beyond reading the spec as the source.
4. Minor. #278 is not "ready to merge" by the PR text: STAGE needs the one-time switch (`ip_unprivileged_port_start` at 80 or lower, IPv6 enabled), and a Let's Encrypt issuance over the socket was not run, only read from the Caddy sources. Action: lane-a verifies on STAGE (certificate renewal and the tunnel 200) before LIVE.
5. Minor. #272 CMP.33 assumes answer A on the #266 question, still pending with lane-a; VEN.32 uses the VEN prefix and 14 h by the author's choice. Action: lane-a confirms or edits the rows. ARC.30 and ARC.32 are P1 and not started; CMS and events help texts exist already for the new blocks.
6. Minor, carried over from check 19: ORD.9 de, fr and es mail bodies in English and the other ORD.9 emails; #274 and the CORE.18 migrate-checks fix have no backlog row (lane-a to add; #269 review findings 1 and 3 open); `token-xxxxxxxx` literal in `apps/ingest/tests/integration/test_services.py` (replace with `secrets.token_urlsafe()` in the next ING PR); CAT.20 1/3 leaves `Manufacturer` unaudited until 2/3; ING.1 `SupplierSource.write_enabled` set by nothing until ING.2; CMP.4 1/2 services have no adapter; `PRIVACY_PEPPER` not declared in settings or the env example; VEN.31 page-scoped CSP hash and documents chooser (2/2, now behind VEN.32); ORD.6 customer notification on shipped (to close once confirmed on STAGE); CMP.5 3 of 4; `time.sleep(1.0)` in the orders concurrency test; CMP.7 `KEY_PAGES`; SEO.8 2/2 and CORE.9 3/3 open; OPS.7 Wagtail deprecation warning (still printed by `make check`; SHP.3 `WeightBand.surcharge_net` is now in spec 06 through #272, closed).
7. Info. #282 and #288 were an incident response to an unreachable apt mirror: the E2E job now continues without the extra fonts after one 150 s attempt; `visual-baselines.yml` stays strict on purpose.

Verdict: GO with follow-ups. No blocker, no major. CMS.11 (chooser scoping) and OPS.5 socket activation (security, reverse proxy) need human review.

### Coherence check 21 (main at ddea570ab861f8b2715410e4dd955a6716730d63)

Scope: main at ddea570 (merge of #320), the merges since check 20 (0651246): #263 (CMP.6 1/2), #285 (ARC.29), #270 (CORE.4 POST return URL), #276 (ORD.9 2/2), #271 (SEO.8 2/2), #277 (ARC.30), #295 (OPS.11), #298 (ADR-0010), #303 and #293 (spec I18N.13, I18N.14, ARC.32 admin placement, CORE.27, ORD.16), #215 (OPS.8 Manager dev docs), #290 (test fixes), #294 (check 20).

| Area | Result |
|---|---|
| Code vs spec | CMP.6 1/2: `apps/catalog/tests/unit/test_price_history_engine.py` covers the 30-day window, successive reductions, currency and window edges; `test_price_history.py` covers the append-only trigger (update, direct delete), the service writes and the variant override rule (owner Q1=A, `SALE_APPLIES_TO_OVERRIDDEN_VARIANT`). The shop-facing strikethrough, the trader status and legal guarantee notice are 2/2 and not yet shown. ARC.29: `core.E801` in `apps/core/checks.py` with a unit test per flag (`test_live_flags.py`); matches the done-when. CORE.4: `apps/accounts/http.py` `return_url` with 15 hostile Referers, the auth and MFA deny-list and GET self-return tested (`test_mfa_post_next.py`, `test_mfa_step_up.py`). ORD.9 2/2: delivered and cancelled emails (customer, vendor, vendor notice) hooked to the transitions, rollback, dry-run and vendor-leak tests in `test_notifications_lifecycle.py`; the PDF confirmation is 3/3 and ADR-0010 (WeasyPrint, on the fly) is accepted. SEO.8 2/2 (partly): per-site `SeoSettings` indexing toggle, panel, floor and demo mode win over a stored allow, disallowed Host fails closed, audit `seo.settings.configured`, sitemap line only when indexing is allowed (`test_indexing_toggle.py`, e2e `test_seo_settings.py`); the Organization JSON-LD with logo, `sameAs` and `contactPoint`, per-page SEO fields and the opening checklist reminder from the 2/2 text are not in the PR. ARC.30: sweep, allow-list with reasons, counted baselines and `docs/dev/field-help.md`; the baselines are 627 lines (field help) and 267 lines (Italian) and must only shrink (ARC.31). OPS.11: parity smoke deploys the previous release tag, then the candidate, with the no-previous-tag fallback and the upgrade path required on main and release tags (`test_deploy_script.py`, `test_release_gate.py`); the real run is the CI parity-smoke job. |
| Spec and docs | Rows CORE.27, ORD.16, I18N.13, I18N.14 exist in specs 02, 07 and 09 and in `90-backlog.md` and `backlog.csv` (318 tasks, 2603 h); `make docs` freshness passes; ARC.32 and SEO.8 rows carry the owner decisions. `docs/dev/audit-trail.md` lists `seo.settings.configured`; `docs/dev/manager.md` and `docs/dev/field-help.md` are new and linked; `platform-roles.md` is generated and clean. `PriceHistory` has no AuditLog action by design (append-only table written inside the audited price services, stated in `apps/catalog/apps.py`). No decision referenced by the delta is open. |
| Hygiene | `makemigrations --check` clean (inside `make check`). New: catalog 0012 (depends on 0011), seo 0001 (new app). Chains linear, no duplicate numbers: accounts ..0007, api ..0007, catalog ..0012, cms ..0015, compliance ..0005, core ..0021, events ..0004, ingest 0001, manager ..0002, orders ..0011, seo 0001, shipping ..0010, tax ..0009, themes 0001, vendors ..0026. Import contracts: 9 kept, 0 broken (the new `seo` app and the catalog edits keep the graph). Redaction grep over the delta and the repo: no legacy table prefix, hostname, credential or vendor name; the only `wp_` matches are the existing `legacy_wp_id_*` columns. No FIXME or stray print; one TODO(TAX.12) in `product_card.html` (interim net price). Carried findings closed: the `token-xxxxxxxx` literal in the ingest test and the `time.sleep(1.0)` in the orders concurrency test are gone (#290). |
| Gates | `make check` pass on ddea570 (exit 0). Unit suite (`make test-unit`) on ddea570: pass, 6068 tests selected, exit 0. Main CI on ddea570: success. Integration and e2e suites not run locally. |

Findings and actions:

1. Minor. The task table and the merge list of this file have no entry for #263, #285, #270, #276, #271, #277, #295, #298, #215 and the spec PRs. Action: lane-b adds the rows in the next progress update (CMP.6 partial 1 of 2, ARC.29 done, CORE.4 follow-up done, ORD.9 partial 2 of 3, SEO.8 partial 2 of 2 minus the Organization node and per-page fields, ARC.30 done with baseline, OPS.11 done, OPS.8 partial).
2. Minor. SEO.8 2/2 is narrower than its spec text: the Organization JSON-LD (logo, `sameAs`, `contactPoint` from `current_operator()`), per-page SEO fields and the platform-opening checklist reminder are not built, and `robots.txt` has no sitemap line until a sitemap route exists (tested). Action: lane-a proposes a SEO.8 3/3 row or confirms that the Organization node belongs to SEO.7.
3. Minor. CMP.6 1/2 is storage and the reference-price engine only; the strikethrough in listings, the vendor trader status and the legal guarantee notice at checkout (the rest of the row) are open, and `SALE_APPLIES_TO_OVERRIDDEN_VARIANT` is a module constant, not yet read by checkout (ORD.3 follows it, `apps/cart/engine.py` docstring). Action: CMP.6 2/2.
4. Minor. ARC.30 ships with a debt baseline (894 lines in two files) that arrived with main (product cards and grid blocks, catalog brands, SEO settings). It only shrinks; ARC.31 is P2 and not started, so every new editor field from now on must carry help text and an Italian entry. Action: keep the "no new baseline entries" rule in PR reviews.
5. Minor. OPS.11 is verified by unit tests and the CI parity-smoke job; the upgrade over a real previous tag has run only in CI, not on STAGE. Action: lane-a confirms the first run of the upgrade-path step on a tag.
6. Minor. `apps/cms/admin_views.py:30` still raises `RemovedInWagtail90Warning` on every manage command (OPS.7). CORE.27 (migrate checks and the roles sync) is now a spec row and not started; #269 review findings 1 and 3 stay open until then.
7. Minor, carried over from check 20: #275 `choosers._word_matches` reads `brand__name` (CAT.20 2/3); #275 foreign product id in a hand-posted page form and the item `@id` until SEO.7; #278 STAGE verification of the Let's Encrypt renewal and the tunnel; #272 CMP.33 assumes answer A on #266 and VEN.32; ORD.16 (de, fr and es mail bodies and the other ORD.9 emails, now a row); CAT.20 1/3 `Manufacturer` unaudited until 2/3; ING.1 `SupplierSource.write_enabled`; CMP.4 1/2 services have no adapter; `PRIVACY_PEPPER` is read in `apps/core/privacy.py:17` but declared in neither the settings nor the env examples (falls back to `SECRET_KEY`); VEN.31 2/2; ORD.6 on STAGE; CMP.5 3 of 4; CMP.7 `KEY_PAGES`; CORE.9 3/3.
8. Info. ARC.29 closes the SEO.8 review finding: a stray demo or force-noindex flag on LIVE now fails `check --deploy` with `core.E801`. ADR-0010 settles the PDF library for ORD.9 3/3.

Verdict: GO with follow-ups. No blocker, no major. ORD.9 2/2 (orders, personal data), CORE.4 (redirect target, security), SEO.8 (indexing control, audited) and ARC.29 (LIVE refusal check) need human review.

### Coherence check 22 (main at 2953ce1e23de08a47a98ad2fb04e7d4e77ce66c4)

Scope: main at 2953ce1 (merge of #339), the merges since check 21 (ddea570): #286 (FIT.1 1/2, merged before #328 but after the main that check 21 read), #311 (draft PRs run light CI), #328 (check 21), the spec stack #309 (owner decisions 2026-11-11), #313 (adviser opinion 2026-11-11) and #314 (spec 13 legacy map), #266 (CMP.4 2/3) and #302 (ARC.31 3/5).

| Area | Result |
|---|---|
| Code vs spec | CMP.4 2/3 (#266): the done-when item "erasure request on a seed order" maps to `test_privacy_views.py` and `test_gdpr.py`; the retention lifecycle, dry run, idempotence, the counts-only audit row and the pending erasure that waits for its last open order map to `test_retention.py`; the register to `test_subprocessors_register.py` and `test_subprocessors_page.py`. The done-when item for a closed vendor's owner (Q-16: keep membership, contract evidence, DAC7, ledger and orders, delete the rest) is not built: vendor-linked users raise `VendorLinkedUser` and the job skips them (stated in the PR; the table says "2 of 3"). The frozen-clock test only lists candidates in a dry run (the order trigger checks the database clock); a real run uses backdated orders. The retention job never rewrites an order itself (it calls `restrict_order` and `anonymise_order`). ARC.31 3/5 (#302): vendor help texts and `vendors/0027`; the baselines shrank from 627 to 572 lines (field help) and from 267 to 235 (Italian), and the sweep is in the unit suite (green). FIT.1 1/2 (#286): the scheme, levels, node tree, merge that moves references, proposal status and the category setting are covered by 50 tests; the done-when line "a proposed node is invisible to pickers until approved" has no picker yet (FIT.3); the Manager platform pages are part 2. #311 is workflow only. |
| Spec and docs | Owner decisions of 2026-11-11 are in `92-open-decisions.md` as "Decided (owner, 2026-11-11)": D-05, D-06, D-09, D-10, D-14, D-16, D-19, D-20; the rows PAY.4, PAY.1, SEO.5, CORE.9, VEN.6, MIG.4, MIG.9 and SHP.3 follow. D-05 says every refund event is charged provider cost plus the surcharge, rounded up to the whole euro, and the same text is in PAY.4, VEN.33 and spec 07 (no "already charged" parameter); the fee and its exclusion flag for a platform malfunction come from the lawyer fixes (L-10) and are marked so. The lane-b design details of #309 are listed in its PR text as assumptions, and D-05 labels its defaults "until the owner says otherwise". D-02, D-03, D-08 and D-15 are marked as decided by the adviser opinion (D-02 also by the owner), with TAX.17, TAX.18 and VEN.33 as rows; none of the decisions blocks a task that was started. New rows: I18N.15, MIG.16 to MIG.21, PAY.5, PAY.6, SEO.9, TAX.17, TAX.18 and VEN.33 (331 tasks in `backlog.csv`, 13 more than at check 21; no row removed; `make docs` freshness passes). `docs/dev/audit-trail.md`, `docs/dev/background-jobs.md`, the tenancy registry and the security registry carry the CMP.4 additions. The task table and the merge list of this file now have the rows that check 21 asked for (finding 1 there is closed). |
| Redaction | The whole repository (outside the git-ignored `private/`) has no legacy table prefix: the only `wp_` match is a WordPress function name in spec 09. The only URL in `docs/spec` is `https://www.google.com`. Spec 13 (legacy map, 151 lines changed) names source systems by kind (classic posts store, marketplace per-vendor table), has no hostname, credential, vendor name or security detail; `legacy_wp_id_*` and `legacy_wp_order_id` are the existing column names. The sub-processor register names one product (Stripe), which the spec itself names. |
| Hygiene | `makemigrations --check` clean. New: vendors 0027 (depends on 0026), fitment 0001 (new app). Chains linear, one leaf per app, no duplicate numbers: accounts ..0007, api ..0007, catalog ..0012, cms ..0015, compliance ..0005 (unchanged by #266), core ..0021, events ..0004, fitment 0001, ingest 0001, manager ..0002, orders ..0011, seo 0001, shipping ..0010, tax ..0009, themes 0001, vendors ..0027. Import contracts: 11 kept, 0 broken (the fitment contracts are in). No FIXME or stray print in the delta. |
| Gates | `make check` pass on 2953ce1 (exit 0). Unit suite (`make test-unit`): pass, 6110 tests selected, exit 0. Main CI on 2953ce1: success. Integration and e2e suites not run locally. |

Findings and actions:

1. Minor. CMP.4's done-when is wider than #266: the closed vendor owner erasure (Q-16) needs a vendor-side retention model and is not built; vendor-linked accounts that ask for erasure are skipped by the job and refused by the views. Action: lane-a schedules CMP.4 3/3 (or splits it into a CMP row); the table says "2 of 3".
2. Minor. The public sub-processors page states facts that the operator has not confirmed (each provider bound by a data processing agreement, locations "EU" and "EU and USA", proxied map tiles without cookies, named Stripe next to generic names), and the new customer privacy pages, the sub-processor page and `subprocessors.py` have no Italian strings. Action: owner and counsel confirm the wording before LIVE (the PR asks lane-a to add it to the owner register); Italian with the next I18N.7 slice.
3. Minor. MIG.19 and spec 13 cite D-39 (coupons as a feature, issue #243, spec 18), which has no row in `92-open-decisions.md` on main; D-17 is open but blocks nothing. Action: the promotions spec PR adds D-39, or MIG.19 names the issue only.
4. Minor. FIT.1 deviates from spec 17 (`FitmentCategory` rows instead of a `Category.fitment_relevant` field); the PR argues it keeps the catalog free of fitment. FIT.1 and FIT.2 wait for the Manager role UI (CORE.18 2/2) for their pages. Action: lane-a confirms the deviation and edits the FIT.1 text in spec 17.
5. Minor. The nightly retention run writes an audit row even with zero counts (kept and documented in #266); the login throttle key hash, audit `ip` retention and `OutboxEmailEvent.detail` are open follow-ups of #266.
6. Minor, carried over from check 21: SEO.8 Organization JSON-LD, per-page SEO fields and checklist reminder (3/3 row to propose); CMP.6 2/2; ARC.31 4/5 and 5/5; OPS.11 first run on a tag; `apps/cms/admin_views.py` Wagtail deprecation warning (OPS.7); CORE.27 not started (#269 review findings 1 and 3); #275 `choosers._word_matches` reads `brand__name` (CAT.20 2/3) and the foreign product id in a hand-posted page form; #278 STAGE verification of the renewal and the tunnel; #272 CMP.33 assumes answer A on #266 (the retention job is now its base) and VEN.32; ORD.16; CAT.20 1/3 `Manufacturer` unaudited until 2/3; ING.1 `SupplierSource.write_enabled`; CMP.4 1/2 services now have an adapter (closed); `PRIVACY_PEPPER` is read in `apps/core/privacy.py` but declared in neither the settings nor the env examples; VEN.31 2/2; ORD.6 on STAGE; CMP.5 3 of 4; CMP.7 `KEY_PAGES`; CORE.9 3/3.
7. Info. The spec stack is stacked (#309, then #313 and #314 on it) and merged in one train; the decisions of 2026-11-11 now unblock PAY.1, PAY.4, VEN.33, TAX.17, TAX.18, MIG.16 and SEO.5. PAY.4, VEN.33 and the ledger rules need human review when built.

Verdict: GO with follow-ups. No blocker, no major. CMP.4 2/3 (personal data, erasure and retention, security of the account pages) needs human review.

### Coherence check 23 (main at d5d615bf)

Scope: main at d5d615bf (merge of #352), the merges since check 22 (2953ce1): #305 (I18N.13, export and import of texts for native review), #280 (CAT.20 2/3, brand proposals, review, merge) and #284 (CAT.20 3/3, brand pages, JSON-LD, logo upload), merged through the trains #348 and #352. #264 is not merged. Check 22 is PR #349, still open, and this check builds on it.

| Area | Result |
|---|---|
| Code vs spec | CAT.20: every done-when item maps to a test. The 50 approved brands are `test_a_vendor_with_50_approved_brands_can_add_products_of_all_of_them`; the 21st pending proposal and the freed slots are `test_the_21st_pending_proposal_is_refused_and_approving_frees_slots`; the import with 80 new brands is `test_an_import_with_80_new_brands_makes_one_batch_outside_the_limit`; an invalid Wikidata ID or a non-https link is refused in `test_a_proposal_with_an_invalid_wikidata_id_or_link_is_refused` and in the JSON-LD unit tests; the merge moves products and redirects in one hop for every language (`test_brand_proposals.py`, `test_brand_pages.py`); a pending brand has no page and no graph. "Two vendors selling the same GTIN show the same brand `@id`" holds for the brand node (its `@id` comes from the brand page, the same for every language); the product-side JSON-LD that points at it is SEO.7. Review fixes are in: advisory lock on brand names, batch size caps, the rejection notice to every affected vendor, atomic admin save. I18N.13: round trip, placeholder, tag, URL, length, msgid and checksum refusals with row numbers, `--skip-invalid`, `--dry-run`, Read me as first sheet in EN, IT, DE, FR and ES, audit with the file hash, and `docs/dev/translations.md` are covered by `test_i18n_review_engine.py`, `test_i18n_review_files.py` and `test_i18n_review_commands.py`; the import also restricts the language to an allow-list and parses `str.format` fields with `string.Formatter` (review hardening, beyond the spec text). CMS pages are left to wagtail-localize as the spec says, and the doc explains it. |
| Spec and docs | `docs/dev/audit-trail.md` lists `core.i18n.imported` and the CAT.20 actions (`catalog.brand.*`, `catalog.brandbatch.*`, `catalog.manufacturer.*`); `catalog/apps.py` now lists `brand`, `brandbatch` and `manufacturer` as audited (check 22 finding on `Manufacturer` closed). `docs/dev/platform-roles.md` and the public forms register are unchanged and clean. The tenancy registry has the new models. The task table and the merge list of this file have the rows for I18N.13 and CAT.20 and the three merges. No decision referenced by the delta is open. |
| Redaction | The whole repository (outside the git-ignored `private/`) has no legacy table prefix: the `wp_` matches are the WordPress function name in spec 09 and the existing `legacy_wp_id_*` column names. The new files (`brands.py`, `apps/core/i18n_review`, `translations.md`, the env example) contain no URL, hostname, vendor name or credential-like literal; no credential assignment in the new tests. |
| Hygiene | `makemigrations --check` clean. New: catalog 0013 (brand proposals, depends on 0012) and 0014 (help texts); no other app has a new migration. The catalog chain is linear to 0014, one leaf, no duplicate number; core is still at 0021. Import contracts: 11 kept, 0 broken. No FIXME, TODO or stray print in `brands.py`, `taxonomy_admin.py` or `i18n_review`. New environment variables `MPMV_BRAND_PENDING_LIMIT`, `MPMV_BRAND_BATCH_LIMIT` and `MPMV_BRAND_BATCH_PENDING_LIMIT` are in `mpmv/settings/base.py` and `deploy/server/mpmv.env.example`. |
| Gates | `make check` pass on d5d615bf plus check 22 (exit 0, 1115 files analysed by import-linter, mypy strict over 1156 files, i18n catalog check, theme gates). Unit suite (`make test-unit`): pass, exit 0. Main CI on d5d615bf: success. Integration and e2e suites not run locally. |

Findings and actions:

1. Minor. CAT.20 says the pending proposal limit is "a platform setting, default 20"; it is an environment variable (`MPMV_BRAND_PENDING_LIMIT`), and two batch caps (500 brands per batch, 2000 pending in batches) that the spec does not mention were added in review. Action: lane-a edits the CAT.20 text (environment setting, the two caps) or asks for a stored platform setting.
2. Minor. `docs/dev/translations.md` is linked only from `audit-trail.md`; there is no index page of `docs/dev` to add it to. Action: none now; add it when a dev docs index exists.
3. Minor. The Read me in DE, FR and ES and the Italian strings added with CAT.20 are not native-reviewed; I18N.14 covers it before LIVE.
4. Minor. I18N.13 has no reviewer recording (reviewer, date, file hash per language) and no readiness list entry; that is I18N.14, not started. The XLSX limit "only the reviewer column unlocked" is sheet protection without a password, as the spec says, so it guards against slips, not against edits.
5. Minor, carried over from check 22: #275 `choosers._word_matches` still reads `brand__name` (it works, because the field is unchanged; pending brands are not filtered there, to check when the brand filter is rebuilt in SEO.7); CMP.4 3/3; the sub-processors wording and its Italian strings; D-39 row; FIT.1 deviation text in spec 17; SEO.8 Organization JSON-LD, per-page SEO fields and checklist reminder; CMP.6 2/2; ARC.31 4/5 and 5/5; OPS.11 first run on a tag; `apps/cms/admin_views.py` Wagtail deprecation warning (OPS.7, still printed by `make check`); CORE.27; #278 STAGE verification; #272 CMP.33; ORD.16; ING.1 `SupplierSource.write_enabled`; `PRIVACY_PEPPER` is read in `apps/core/privacy.py` but declared in neither the settings nor the env examples; VEN.31 2/2; ORD.6 on STAGE; CMP.5 3 of 4; CMP.7 `KEY_PAGES`; CORE.9 3/3. Closed here: CAT.20 1/3 `Manufacturer` unaudited.
6. Info. The brand pages and logo upload (CAT.20 3/3) handle uploaded files and a public JSON-LD graph; the proposals and merge change the catalog and write redirects. Both need human review, with the I18N.13 import, which writes the Italian and other catalogs from a file.

Verdict: GO with follow-ups. No blocker, no major. CAT.20 2/3 and 3/3 (catalog moderation, uploads, redirects) and I18N.13 (file import into the catalogs) need human review.

### Coherence check 24 (main at d77482b5)

Scope: main at d77482b5 (merge of #367), the merges since check 23 (d5d615bf) that check 23 did not cover: #216 (VEN.27, no-TIN countries as dated data), #301 (ARC.31 2/5, catalog), #264 (VEN.31 2/2, documents), #307 (ARC.31 4/5, Manager), #332 (redaction performance flake), #279 (ARC.32 1/4, help button) and #308 (ARC.31 5/5, remaining apps), merged through the trains #358, #361, #364, #365, #366 and #367. #349 (check 22) and #359 (check 23) are also merged now. Main CI on 2c70054b (the merge of #366) is success; the run on d77482b5 was still in progress when this check was written.

| Area | Result |
|---|---|
| Code vs spec | VEN.27: every done-when item maps to `test_no_tin_countries.py` and `test_engine_dac7.py` (migration seeds the six countries and the DAC7 result is unchanged; a country that staff add makes an individual with a place of birth and no TIN complete; the removal sets `valid_to`, keeps the row and a past day still answers yes; overlapping windows are refused by a constraint; a restricted session is refused and an operational one allowed; a dry run changes nothing; the engine has no ORM import and the engine contract is kept). The spec text and `audit-trail.md` describe the same behaviour (`vendor.no_tin_country_added` and `.no_tin_country_removed`). The spec says the payout task must re-check completeness in the transaction that creates the payout; there is no payout task yet, so that is a note for PAY. VEN.31 2/2: documents go to the vendor's own collection through `upload_storefront_document` and the SEC.2 checks; the editor has no document link and a save refuses one (the owner decision of 2026-11-11, now in the spec row, which also depends on ARC.15); `test_manager_document_link.py` (e2e) and `test_document_chooser.py` and `test_link_dialog.py` (integration) cover it. ARC.32 1/4: the shared component, storefront, account and Manager spaces have the button, the modal fetched from `help_views`, the user-menu item, the "Fields on this page" list, the vendor guide link (`MPMV_VENDOR_GUIDE_URL`), the sweep `test_help_button_sweep.py` and the e2e file `test_help_button.py` with desktop and mobile screenshots (`desktop-closed`, `desktop-open`, `mobile-closed`, `mobile-open`, `menu-open`, `form-fields`); the Wagtail admin and Django admin parts (2 to 4) are not built, so the done-when line "both admins included" is open, as the table says. ARC.31 2/5 to 5/5: the e2e screenshot of a Manager form with Italian help is `profile-form-help-it` (`test_manager_shell.py`); the screenshot of the event page editor belongs to part 1, which is not merged. #332: the unit test now measures CPU time, takes the best of seven runs after a warm-up and retries three times; the threshold moved from 8x to 10x for a 4x input, so a quadratic regression (about 16x) still fails. |
| Spec and docs | `docs/dev/audit-trail.md` has the VEN.27 and VEN.31 rows. `docs/dev/rich-text.md` has the new `rel="noopener noreferrer nofollow ugc"`. `docs/dev/field-help.md` describes the baselines without counts (nothing to update). `apps/cms/apps.py` lists `helpentry` as logged in Wagtail's action log, so no audit-trail row is needed. `docs/dev/platform-roles.md` and the public forms register are unchanged and their build checks pass. The tenancy registry and the endpoint security test have the new models and help URLs. `MPMV_VENDOR_GUIDE_URL` is in `mpmv/settings/base.py` and `deploy/server/mpmv.env.example`. The task table and the merge list of this file now have the rows for VEN.27, VEN.31 2/2, ARC.31 (4 of 5), ARC.32 and the nine merges (the rows for #349 and #359 were missing too). No decision referenced by the delta is open. |
| Redaction | No legacy table prefix and no legacy plugin name in any versioned `py`, `md`, `html`, `txt`, `example` or `yml` file outside the git-ignored `private/`. The only URL literal in the new code is the `https://www.example.com/guide` example inside a help text of `apps/manager/media.py`; the new tests use `example.com` addresses and generate passwords with `secrets.token_urlsafe()`. No vendor name, hostname or credential-like literal in the delta. |
| Hygiene | `makemigrations --check` clean. New migrations: vendors 0028 (no-TIN country), catalog 0015, cms 0016 (`HelpEntry`), accounts 0008, compliance 0006, core 0022, ingest 0002, shipping 0011, tax 0010 and themes 0002 (the last eight, except cms, are field help texts). Each chain is linear with one leaf and no duplicate number. Import contracts: 11 kept, 0 broken (1137 files analysed). The Italian catalog check (`tools/i18n_catalog.py check` and `test_italian_catalog.py`) passes and `django.mo` is rebuilt. The ARC.30 baselines only shrank: 572 to 358 lines (field help) and 235 to 113 (Italian), and both now list only `cms` and `events` fields (the unit test `test_the_remaining_apps_are_out_of_both_baselines` guards this). No FIXME or stray print in the delta. |
| Gates | `make check` pass on d77482b5 (exit 0: ruff, mypy strict over 1181 files, import-linter, theme gates, docs freshness, public forms and roles docs, i18n catalog, Django checks, makemigrations). Unit suite (`make test-unit`): pass, 6283 passed, 8 skipped (the known TAX.4, TAX.8 and ORD placeholders). Integration and e2e suites not run locally; main CI on 2c70054b success. |

Findings and actions:

1. Minor. ARC.31 part 1 (events and CMS) is not merged although parts 2 to 5 are; the row says "merged in that order". The two baselines still list 358 and 113 `cms` and `events` fields, and the done-when screenshot of the event page editor does not exist yet. Action: lane-a schedules ARC.31 1/5 next, or edits the row to drop the order rule; the table says "4 of 5".
2. Minor. `docs/dev/translations.md` says the `vendor-help` scope has "help texts included", but the scope follows the source references of `apps/vendors/` and `apps/manager/`, so the help texts of catalog, compliance, core, ingest, shipping, tax and themes (`HELP_TEXT_SCOPES`, #308) only appear in the `all` scope. Action: say so in that table, or extend the scope for the review files.
3. Minor. #332 loosened the scaling bound from 8x to 10x and added retries; a slow quadratic path of low weight could now hide below it. Action: none now; revisit if the test is touched again.
4. Minor. VEN.27: removing a country re-blocks vendors without notifying them (stated in the spec); the payout re-check "in the transaction that creates the payout" has no code to attach to yet. Action: the payout task must carry it; the notice goes to the vendor communication rows.
5. Minor. ARC.32 parts 2 to 4 (Wagtail admin, Django admin, the a11y e2e in each space) are open, so "button on every signed-in page of both admins" and the axe check per space are not met yet; the spec records the Wagtail sidebar deviation accepted by lane-a.
6. Minor. VEN.31: documents can be uploaded and chosen but not linked until ARC.15; a vendor can store PDFs in its collection with no public use. Action: ARC.15 follows; nothing else.
7. Minor, carried over from check 23: CAT.20 limits text (check 23 finding 1); #275 `choosers._word_matches` reads `brand__name`; CMP.4 3/3; the sub-processors wording and Italian strings; D-39 row; FIT.1 deviation text in spec 17; SEO.8 Organization JSON-LD, per-page SEO fields and checklist reminder; CMP.6 2/2; OPS.11 first run on a tag; `apps/cms/admin_views.py` Wagtail deprecation warning (OPS.7, still printed by `make check`); CORE.27; #278 STAGE verification; #272 CMP.33; ORD.16; ING.1 `SupplierSource.write_enabled`; `PRIVACY_PEPPER` is read in `apps/core/privacy.py` but declared in neither the settings nor the env examples; ORD.6 on STAGE; CMP.5 3 of 4; CMP.7 `KEY_PAGES`; CORE.9 3/3; I18N.14 native review (now also the new help texts in Italian). Closed here: VEN.31 2/2 and ARC.31 4/5 and 5/5 are built, and the missing progress rows of checks 22 and 23.
8. Info. The help button (ARC.32) adds a signed-in endpoint that renders sanitised rich text from a snippet, the document upload adds a file path through SEC.2, and VEN.27 changes DAC7 inputs. These need human review.

Verdict: GO with follow-ups. No blocker, no major. VEN.27 (DAC7 data), VEN.31 2/2 (file upload, tenancy) and ARC.32 1/4 (signed-in help endpoint) need human review.

### Coherence check 25 (main at dfc05fb1)

Scope: main at dfc05fb1 (merge of #390), the merges since check 24 (#368, merged by the train #371): #287 (ARC.31 1/5, events and CMS), #225 (VEN.2, plans and commission rules), #289 and #283 (ARC.32 2/4 and 3/4, help button in the account, Manager and both admins), #378 (help e2e fix), #273 (CMP.6 online withdrawal), #321 (I18N.15, configurable languages), #381 (OPS.12, staff Site links), #382 and #389 (CORE.25 banner button, security page, `core.elevate` for owner, support and finance), #383 and #384 (event e2e fix, product link stopgap and `MFA_LOGIN_URL`), #304 (ORD.9 3/3, PDF) and #357 (ORD.7, My orders), merged through the trains #374 to #377, #379, #380, #385, #387, #388 and #390. #231 is not merged. The newest completed main CI run before this check is success; the run on dfc05fb1 was still in progress when this check was written.

| Area | Result |
|---|---|
| Code vs spec | VEN.2: rule and plan changes sit behind the `commission_change` operational-session action and the roles of the CORE.18 matrix, with overlap tests in `test_commission_rules.py` and `test_engine_commission.py`. CORE.25 and D-32: the owner decision of 2026-11-12 (owner, support and finance hold `core.elevate`) is in `manager/roles.py`, migration `manager/0004` and `docs/dev/platform-roles.md` (same rows; the generated doc passes its check); the banner and `audit-trail.md` match. CMP.6 withdrawal: append-only statement (`compliance/0007`, `0008`), engine tests, e2e screenshots (`information`, `step-one`, `step-two`, `acknowledgement`, `it-step-one`). ORD.7: e2e screenshots `list` and `detail`. ORD.9 3/3: PDF with a guarded URL fetcher (unit tests for loopback and link-local addresses) and the `LOAD_TRUNCATED_IMAGES` reset in `core/uploads.py` after WeasyPrint sets it process-wide. ARC.31 1/5: both ARC.30 baselines are at count 0 and the event editor screenshot `event-field-help` exists. ARC.32: Wagtail and Django admin screenshots exist (`wagtail-menu`, `wagtail-open`, `django-closed`, `django-open`, `django-form-fields`). I18N.15: `MPMV_LANGUAGES` feeds the three settings, the info check I911 flags de, es and fr (no interface catalog yet) and CI runs the unit suite and the migration check with `it,de`. The decisions the delta references (D-04, D-32 and the owner decision of 2026-11-12) are decided; none is open. |
| Spec and docs | `audit-trail.md` has the VEN.2 (`vendor.plan_saved`, `vendor.commission_rule_added`) and CMP.6 (`compliance.withdrawal.submitted`) rows and its guard test passes. `field-help.md` describes the baselines without counts (nothing to change). `platform-roles.md` and the public forms register build clean. `MPMV_LANGUAGES` is in `mpmv.env.example`; `PRODUCT_PAGES_ROUTED` and `MFA_LOGIN_URL` are settings with a comment, not environment variables. The task table and the merge list of this file now have the rows for the merges above, and the rows of ARC.31, ARC.32, CMP.6 and ORD.9 were updated. |
| Redaction | No legacy table prefix beyond the existing `legacy_wp_id_*` columns, and no new product or plugin name in code: the matches are the generic names already in the spec. URL literals in the delta are `example.*`, `testserver`, loopback or link-local test addresses and existing public service hosts. No vendor name, hostname or credential-like literal in the delta. |
| Hygiene | Single migration leaf per app, no duplicate numbers: accounts 0009, api 0007, cart 0002, catalog 0015, cms 0017, compliance 0008, core 0022, events 0005, fitment 0001, ingest 0002, manager 0004, orders 0012, seo 0001, shipping 0011, tax 0010, themes 0002, vendors 0029 (the VEN.2 migration; there is no 0030). `makemigrations --check` clean. Import contracts: 11 kept, 0 broken (1178 files). The Italian catalog check and `test_italian_catalog.py` pass (no duplicate msgids). |
| Gates | `make check` pass on dfc05fb1 (exit 0: ruff, mypy strict over 1226 files, import-linter, theme gates, docs freshness, i18n catalog, Django checks). Unit suite (`make test-unit`): exit 0, 6415 selected. Integration and e2e suites not run locally. |

Findings and actions:

1. Minor. I18N.15: the removal of a language or a change of the primary language as a management command with redirects (done-when 3) was not found in the code; only the setting, the checks and the CI job exist. Action: lane-a decides whether it is a follow-up row; the task row says partial.
2. Minor. `docs/dev/translations.md` still says the `vendor-help` scope has "help texts included" while the help texts of other apps are only in `all` (check 24 finding 2, still open), and it does not mention `MPMV_LANGUAGES` (the import allow-list reads `settings.LANGUAGES`, so a non-default install changes the accepted languages). Action: one line each.
3. Minor. ARC.32: parts 2 and 3 are merged; the spec done-when asks for an axe check per space, which this check could not confirm without the e2e suite. Action: confirm in the next full CI run, then mark the row done.
4. Minor. The ARC.31 row says "parts merged in that order"; part 1 was merged last. Action: lane-a edits the done-when text.
5. Minor. `PRODUCT_PAGES_ROUTED` (#384) is a stopgap that hides product links until the product pages are routed. Action: remove it with the task that routes them.
6. Minor. OPS.12 `/_links/` is a temporary page and must go before LIVE. Action: add it to the release checklist.
7. Minor, carried over from check 24: CAT.20 limits text; #275 `choosers._word_matches`; CMP.4 3/3; sub-processors wording; D-39 row; FIT.1 deviation text; SEO.8 remaining parts; CMP.6 remaining items (strikethrough, trader status, guarantee notice); OPS.11 first run on a tag; the Wagtail deprecation warning in `apps/cms/admin_views.py` (OPS.7, still printed by `make check`); CORE.27; #278; #272; ORD.16; ING.1 `write_enabled`; `PRIVACY_PEPPER` is read in `apps/core/privacy.py` but declared in neither the settings nor the env examples; ORD.6 on STAGE; CMP.5 3 of 4; CMP.7 `KEY_PAGES`; CORE.9 3/3; I18N.14 native review; the VEN.27 payout re-check. Closed here: ARC.31 1/5 built (baselines at 0), the ARC.32 admin parts, and the VEN.2 and CMP.6 withdrawal rows.
8. Info. The delta touches security and personal data: CORE.25 (passkey window, roles with `core.elevate`), the withdrawal flow (public form, personal data, append-only table), the PDF renderer (fetches URLs), My orders (customer data and tenancy) and VEN.2 (commission rules). These need human review.

Verdict: GO with follow-ups. No blocker, no major. CORE.25 role grants, CMP.6 withdrawal, ORD.9 3/3 (PDF fetcher), ORD.7 and VEN.2 need human review.

### Coherence check 26 (main at 97572ee2)

Scope: main at 97572ee2 (merge of #396), the merges since check 25 (base dfc05fb1): #231 (CORE.9 review and revert, platform review and revert of storefront profile changes, D-33), #386 (CAT.7 1, product page), #391 (check 25) and #393 (CORE.25 passkey window wording), merged through the trains #392, #394, #395 and #396. #389, #304 and #321 were already merged at check 25 and were re-verified here.

| Area | Result |
|---|---|
| Code vs spec | CAT.7: route, view, `product_pages.py` and `engine.product_node` match spec 03 (visible SKU, "on request" without a stored price, retired products keep a page, companion only when published and active); `catalog:product` is in `tests/security_registry.py` and `tests/tenancy_registry.py`; `PRODUCT_PAGES_ROUTED` is True in base settings and tests, with its comment updated. #393: no "Secure mode", "Operational mode" or "restricted mode" in templates, the Italian catalog, `docs/dev`, spec or settings; the only matches are `core/0020` and `0023` (the rename migration, reversible: its `RunPython` has a backwards function), a test asserting their absence, and the old row of the CORE.25 task (fixed here). Spec 02, 04, 12 and D-32 use the new wording; the old audit action names (`operational_started`, `operational_ended`) are documented as still read. #389: `core.elevate` is in `manager/roles.py` for owner, support and finance and the same three rows are in `platform-roles.md`; `manager/0004` and `0005` re-sync the role groups. #231: permission `vendors.revert_storefront_profile` (`vendors/0030`), `manager/0005` depends on it, `audit-trail.md` has the revert, stale and logo rows, `background-jobs.md` has the logo purge job, D-33 is recorded as decided, the revert view is in the security registry. #304: the PDF fetcher and the `LOAD_TRUNCATED_IMAGES` reset are in place. #321: `accounts/0009`, `cart/0002` and `orders/0012` are the callable language defaults and `makemigrations --check` is clean. |
| Spec and docs | The task table and the merge list of this file now have rows for #231, #386 and #393 (CORE.9 changed to partial with the D-14 lock open, CAT.7 added as partial, CORE.25 old wording note). The backlog files are fresh (the check passes). The decisions the delta references (D-32 and D-33) are decided; none is open. |
| Redaction | No vendor name, hostname, legacy order number or credential-like literal found in the delta; URL literals are `example.*`, `testserver` or loopback. |
| Hygiene | Single migration leaf per app, no duplicate numbers: accounts 0009, api 0007, cart 0002, catalog 0015, cms 0017, compliance 0008, core 0023, events 0005, fitment 0001, ingest 0002, manager 0005, orders 0012, seo 0001, shipping 0011, tax 0010, themes 0002, vendors 0030 (ledger, mcp and payments have none). `makemigrations --check`: no changes. Italian catalog check passes. |
| Gates | `make check` pass on 97572ee2 (exit 0: ruff, mypy strict, import-linter, docs freshness, i18n catalog, Django checks). Unit, integration and e2e suites not run locally; CI on main is the gate. |

Findings and actions:

1. Minor. The CAT.7 product page has no gallery images (CAT.4 has no image model; `product_pages.gallery()` is an empty hook), no related products (CAT.15) and a disabled add-to-cart placeholder until a cart view exists. Action: the CAT.7 row stays partial; lane-a decides whether the rest is a follow-up row.
2. Minor. `PRODUCT_PAGES_ROUTED` is now always True: the page is routed, so the switch only guards a deployment without it. Action: remove the setting and the `getattr` in `catalog/services.py` when a catalog task next touches that code (check 25 finding 5 is closed by #386).
3. Minor. Three test names still say "operational session" (`test_a_plan_change_needs_the_operational_session_too`, `test_an_operational_session_reverts_both_kinds`, `test_an_operational_session_may_add_and_end`). Cosmetic. Action: rename with the next change to those files.
4. Minor. The #231 PR text names `vendors/0027` and `manager/0003`; the migrations landed as `vendors/0030` and `manager/0005` after renumbering. The code and this log are right. No action.
5. Minor. CORE.9 is partial, not done. Its Done-when also requires the D-14 field lock for pipeline-fed products (owner decision 2026-11-11): "on a pipeline-fed product a vendor edit of price, stock or any other field locks that field, a later feed run leaves it unchanged but stores the new feed value, the Manager shows the lock and the feed value, unlocking lets the next feed run overwrite it, every lock and unlock is in the AuditLog, and a vendor cannot lock or edit another vendor's product". Nothing in `apps/` implements it (no lock or feed-value model or service) and #231 does not claim it. The first draft of this check closed CORE.9 as 3/3; lane-a's review corrected that. Action: lane-a adds a sub-part or follow-up row for the D-14 lock; the CORE.9 row says partial until then.
6. Minor, carried over from check 25: I18N.15 removal command; `translations.md` lines on `vendor-help` and `MPMV_LANGUAGES`; ARC.32 axe check per space; OPS.12 `/_links/` before LIVE; CAT.20 limits text; #275 `choosers._word_matches`; CMP.4 3/3; sub-processors wording; D-39 row; FIT.1 deviation text; SEO.8 remaining parts; CMP.6 remaining items; OPS.11 first run on a tag; the Wagtail deprecation warning in `apps/cms/admin_views.py` (OPS.7); CORE.27; #278; #272; ORD.16; ING.1 `write_enabled`; `PRIVACY_PEPPER` declared in neither the settings nor the env examples; ORD.6 on STAGE; CMP.5 3 of 4; CMP.7 `KEY_PAGES`; I18N.14 native review; the VEN.27 payout re-check. Closed here: the product link stopgap of #384 (the page exists).
7. Info. The delta touches security and public data: CORE.9 review and revert (revert action, logo file retention, emails to owners), the CORE.25 rename (permission rename migration, session policy keys) and the public product page (published fields only). These need human review.

Verdict: GO with follow-ups. No blocker, no major. CORE.9 review and revert (revert and logo retention), the CORE.25 rename and CAT.7 (public data) need human review.

### Coherence check 27 (main at d51e41e8)

Scope: main at d51e41e8 (merge of #416), the merges since check 26 (base 97572ee2): 15 merge trains (#400, #401, #403 to #408, #410 to #416) with 69 non-merge commits and 21 PRs: the specs #223, #241, #243 and #297; money #318, #326, #350, #351, #353, #354 and #334; tax #325 (which carries #323, closed and merged through it); privacy #296, #337 and #265 (roles); legal #343; SEO #292 and #345; the cart UI #360; #299 (ORD.16) and #402 (THM.12); and #399 (check 26). #291 (CMP.5 4/4) and #356 (VEN.6) are still open and are not in this check.

| Area | Result |
|---|---|
| Code vs spec | Money: no `float` carries an amount in the delta (the matches are timeouts and clocks). The ledger, the commission statement, the Stripe transfer shares and the refund fee round `ROUND_HALF_UP`; the refund fee uses `ROUND_CEILING` to the euro, the one documented exception, then `round_line`. `LedgerEntry` is append-only through database triggers (`ledger/0002`), and `record_entry` audits `ledger.entry_recorded`. `payments.payment.succeeded` is audited by the webhook. Tax: `BT` postcodes resolve to `XI`, `XI` stays out of `VIES_COUNTRIES`, the shipping VAT follows the goods (`shipping/0012`, `0013`). Privacy: `PRIVACY_PEPPER` is in the base settings, both env examples, the local stack script and the parity smoke, and `core.E901` guards LIVE and STAGE (check 25 finding closed); the export needs a fresh second factor (#296). Roles: `staff_roles` is in the help sweep, `compliance.review_notices` is in `roles.py`, `platform-roles.md` and `manager/0006`. SEO: the operator `Organization` node reads `current_operator()` (no `"IT"` literal in the delta except the documented postcode table in `tax/engine.py`). Cart UI: `orders:checkout` is in the security and tenancy registries, `payments_webhook` too. Legal: the clauses are pure, EN and IT, and marked as draft wording that needs counsel (CMP.24). |
| Spec and docs | The task table and the merge list of this file now have rows for every PR above (VEN.4, VEN.33, PAY.1, PAY.2, PAY.4, PAY.5, SHP.4, ORD.3, ORD.5, ORD.10, TAX.17, TAX.18, CMP.2, CMP.33, SEO.7, ORD.16, ARC.15), and updated rows for CORE.18 (2/2, done), CMP.4, CMP.5, SEO.8 and THM.12. `backlog.csv` has 372 tasks (40 new since check 26: TAX.15, TAX.16, TAX.19, ORD.13, CMP.28, CMP.32, CMS.13 to CMS.16, TRU.1 to TRU.11, PRM.1 to PRM.16, SEC.15, OPS.13, VEN.30; none removed) and `make docs` freshness passes. D-28, D-37 (proposal, no TRU row is claimed), D-39 and ADR-0009 are consistent with their rows; the scanner row is OPS.13 in the spec and in the ADR. `audit-trail.md` has the ledger, payment, staff role, email suppression and export rows. |
| Redaction | No vendor name, hostname, legacy order number or credential-like literal found in the delta; URL literals are `example.*`, `testserver` or loopback. |
| Hygiene | Single migration leaf per app, no duplicate numbers: accounts 0009, api 0007, cart 0002, catalog 0015, cms 0017, compliance 0009, core 0024, events 0005, fitment 0001, ingest 0002, ledger 0002, manager 0006, orders 0012, payments 0001, seo 0001, shipping 0013, tax 0010, themes 0002, vendors 0031 (mcp has none). `makemigrations --check`: no changes. The Italian catalog check passes. |
| Gates | `make check` pass on d51e41e8 (exit 0). Unit, integration and e2e suites not run locally; CI on main is the gate. |

Findings and actions:

1. Major for go-live, not for the merge. The checkout is not connected to payment. `place_order` creates the order, but no code calls the Stripe provider, and `on_payment_succeeded` has no receiver: a confirmed payment is stored and audited but the order does not become `paid`, and no ledger entry is written from an order. Action: lane-a plans the wiring (PAY.3 and the ORD.5 payment step) before STAGE tests; ORD.5, PAY.1 and PAY.2 stay partial until then.
2. Minor. The refund fee (VEN.33), the commission statement (CMP.2) and PAY.4 are pure engines: nothing posts them to the ledger yet, and no refund is built. Action: none until PAY.4 and CMP.2 2/2 are planned.
3. Minor. SEO.7 has only the Organization schema check and provenance tests. The done-when also asks for the search results page, a per-page-type snapshot sweep and a CI schema check for Product, Breadcrumb, Article, Event and ProfilePage; none of those exists. The row is partial.
4. Minor. `PRIVACY_PEPPER` is declared and checked but no document under `docs/dev` or `docs/spec` describes it (what it protects, that rotating it resets the keyed counters and the suppression list hashes). Action: add a short section to `docs/dev/release.md` with the next compliance task.
5. Minor. TAX.18 is partial: XI VAT numbers cannot be stored (TAX.19), so a business with an XI number is taxed under the "not in VIES" rule. Known and recorded in the TAX.18 and TAX.19 rows.
6. Minor. ORD.10 "verified on STAGE" is open; the done-when needs a STAGE run.
7. Minor, carried over from check 26 and still open: the CAT.7 product page has no gallery, no related products and a placeholder add-to-cart (the cart UI now exists, so the placeholder can be wired); `PRODUCT_PAGES_ROUTED` is always True and the `getattr` in `catalog/services.py` can go; three tests still say "operational session"; CORE.9 is partial with the D-14 field lock open; I18N.15 removal command; `translations.md` lines on `vendor-help` and `MPMV_LANGUAGES`; ARC.32 axe check per space; OPS.12 `/_links/` before LIVE; CAT.20 limits text; #275 `choosers._word_matches`; CMP.4 3/3; sub-processors wording; FIT.1 deviation text; CMP.6 remaining items; OPS.11 first run on a tag; the Wagtail deprecation warning in `apps/cms/admin_views.py` (OPS.7); CORE.27; #278; #272; ING.1 `write_enabled`; ORD.6 on STAGE; CMP.5 4 of 4 (#291 open); CMP.7 `KEY_PAGES`; I18N.14 native review; the VEN.27 payout re-check.
8. Closed here, from check 26: the `PRIVACY_PEPPER` declaration (#337); the SEO.8 Organization JSON-LD (#292); the D-39 row (#243 landed); CORE.18 part 2 (#265).
9. Info. The delta touches money, tax, payments, ledger, privacy and permissions code: VEN.4, VEN.33, PAY.1, PAY.2, ORD.3, ORD.5, ORD.10, TAX.17, TAX.18, CMP.2, CMP.33, CMP.4 (#337), CMP.5 (#343) and CORE.18 (#265). All need human review, and the legal wording needs counsel (CMP.24).

Verdict: GO with follow-ups. No blocker for the merged code; finding 1 is a gap to close before any payment test on STAGE. The ledger, payments, tax, roles and legal code need human review.


### Coherence check 28 (main at b7f9136a)

Scope: main at b7f9136a (merge of train #435), the merges after check 27 (#419, train #424): six trains, #427 (#426, #336), #428 (#333), #431 (#430, #409), #432 (#331), #434 (#341) and #435 (#330, #429), 25 non-merge commits. Also covered, because check 27 listed them as open and did not review them: #291 (CMP.5 4/4, train #417), #356 (VEN.6, train #423), #421 (OPS.14, train #422) and #420 (SEO.10 spec, train #424).

| Area | Result |
|---|---|
| Code vs spec | #429: one PaymentIntent per order (`payments/checkout.py`, `transfer_group` = the order number, reached through `ORDER_PAYMENT_STARTER` so `orders` never imports `payments`); the webhook transaction runs `order_hook.order_paid_on_payment`, which calls `mark_order_paid`; the session keeps only the payment id and the client secret is asked from the provider on each payment page (no model field, no audit payload holds it); `payments.E004` refuses LIVE while `PAYMENTS_TRANSFERS_WIRED` is False, and it is False in `base.py` (no override) with a test for LIVE, STAGE and the wired case. #409: `refund_lines` is per refund event, the fee is charged to the vendor and never passed on (docstring, spec 04, PAY.4 row). #426: clause (f) says Stripe's actual cost plus the plan surcharge rounded up, with no pass-on sentence; the CMP.5, VEN.33, PAY.4, D-05 and L-10 rows agree; art. 1341 comma 2 is a spec requirement only (VEN.13 and VEN.14 build the approval; `terms_clauses.py` does not mark clauses as onerous). #331: the parser rejects extra keys, `validated_garage` re-checks every vehicle against the catalogue and keeps the catalogue labels, one miss makes an empty garage, the views clear the cookie, `off` deletes it. #330: `consent_save` is `csrf_exempt`, refuses a request whose Origin, else Referer, is not this host (403) before the rate limit, and has a cross-site test with a foreign Origin and a foreign Referer. #341 and #336: the legacy reader opens SQLite with `mode=ro`, an authorizer that denies every write and a SELECT-only gate (`ReadOnlyViolation`); the fixtures are synthetic; no vendor name, hostname, legacy order number or credential-like literal in the delta. |
| Spec and docs | `progress.md` had no rows for VEN.3, VEN.6, FIT.6, FIT.17, MIG.1, MIG.3, MIG.6, SEO.5, SEO.9, SEO.10 and OPS.14, and stale rows for CMP.5, PAY.1, PAY.2 and ORD.5; all added or updated here, with the merge list (#291, #356, #421, #420, #426, #336, #333, #430, #409, #429, #331, #341, #330). `backlog.csv` has 374 tasks (2 new since check 27: SEO.10, OPS.14; none removed) and `make docs` freshness passes. D-05, D-08, D-19 and L-10 agree with their rows; there is no D-40 anywhere in the repository (the last decision row is D-39). `audit-trail.md` has the order payment, payments, ingest and connected account rows and says why `seo.consentlog` is not audited. `payment-providers.md` now names `payments.E004` and says the order state change is wired (it said it was still to connect). |
| Hygiene | Single migration leaf per app, no duplicate numbers: accounts 0009, api 0007, cart 0002, catalog 0015, cms 0017, compliance 0010, core 0024, events 0005, fitment 0001, ingest 0002, ledger 0002, manager 0006, orders 0013, payments 0002, seo 0002, shipping 0013, tax 0010, themes 0002, vendors 0032 (mcp has none). `makemigrations --check`: no changes. The Italian catalog check passes. |
| Gates | `make check` pass (exit 0). Unit, integration and e2e suites not run locally; CI on main is the gate. |

Findings and actions:

1. Major for go-live, not for the merge. Check 27 finding 1 is half closed: the checkout creates the payment and the webhook marks the order paid (#429). Still open: no vendor transfer (PAY.3, `shares=()` and the whole amount stays on the platform), no ledger entry from an order, no refund. `payments.E004` keeps LIVE from starting until PAY.3 sets `PAYMENTS_TRANSFERS_WIRED`; STAGE is not affected. Action: lane-a plans PAY.3 next.
2. Minor, new. `PlatformOperator.commission_vat_mode` is read nowhere (only the admin, the field list and a docstring in `vendors/commission.py` mention it) and the demo seed sets it to `STANDARD`. The commission VAT is therefore not applied from the operator setting. Action: wire it in the statement or ledger code (CMP.2, VEN.4 posting) or drop the claim in the docstring.
3. Minor, new. `apps/orders/tests/integration/test_migrations.py` hard-codes `LATEST = "0013_vehicle_snapshot"`; the next orders migration breaks it. Action: derive it from the migration loader's leaf.
4. Minor, new. The consent cross-site test has an Origin case and a Referer case but no `Sec-Fetch-Site` case, and `_same_site` ignores that header. Action: add a case, or document that Origin and Referer are the only signals.
5. Minor, new. #341's `"blue"` assertions in `test_catalog_loader.py` (`not in changed`, `not in logged`) never run against a value the loader would otherwise have kept, so they prove nothing. Action: make the rejected record carry the value in a way the loader would have written.
6. Minor, new. The art. 1341 comma 2 approval of the onerous clauses (#426) is in the CMP.5 spec row but no code marks a clause as onerous. Action: none until VEN.13 and VEN.14 are built; keep the row as the owner.
7. Minor, carried over from check 27 and still open: finding 2 (VEN.33, CMP.2 and PAY.4 are pure engines, nothing posts them to the ledger); 3 (SEO.7 is partial: results page, page-type sweep and CI schema check); 4 (`PRIVACY_PEPPER` has no document under `docs/dev` or `docs/spec`); 5 (TAX.18 and TAX.19, XI VAT numbers); 6 (ORD.10 on STAGE); and all the check 26 follow-ups listed in finding 7 of check 27.
8. Closed here: check 27 finding 1 as far as the order marked paid; the missing `progress.md` rows and the stale `payment-providers.md` text (this PR); the `#291` and `#356` rows that check 27 left out.
9. Info. The delta touches money, payments, orders and compliance code: VEN.3, VEN.6, PAY.1, PAY.2, ORD.4, ORD.5, FIT.17, CMP.5 (#291, #426) and SEO.5 (consent log). All need human review, and the legal wording needs counsel (CMP.24).

Verdict: GO with follow-ups. No blocker for the merged code; findings 1 and 2 matter before any LIVE payment. The payments, orders, ledger and legal code need human review.

### Coherence check 29 (main at c45180ee)

Scope: main at c45180ee, the merges after check 28 (#437): the trains #461, #464, #466, #468 and #470, #459, and #463. They carry #452 (PAY.3), #454 (spec: adviser rules for RF20 and XI, MQ-38 to MQ-44), #458, #460 (SEO.9), #462 (check 28 follow-ups), #465 (PAY.2 Connect secret), #467 (ORD.5, VEN.2), #469 (ORD.19) and #463 (ORD.17); 26 non-merge commits. #448 and #449 (VEN.35) landed before check 28 and had no row; covered here.

| Area | Result |
|---|---|
| Code vs spec | PAY.3: `PAYMENTS_TRANSFERS_WIRED = True` in `base.py` with the reason in the comment, `payments.E004` only fires when it is False (`test_transfers_wired_check.py`), so LIVE no longer stops on it; the ledger settlement posts the lines and queues one outbox transfer per vendor package, skips a refunded or partially refunded package and refuses to settle when `commission_vat_mode` is not `none` (`ledger.settlement_blocked_vat_mode`, spec 04 4.4 case 3). That also closes check 28 finding 2: the mode is now read. ORD.19: `cart_add` is `require_POST` with no `csrf_exempt`, has the cart rate budget, and the server re-checks the sales mode through `add_to_cart` and `effective_sales_mode`. PAY.2 (#465): `STRIPE_CONNECT_WEBHOOK_SECRET` is a separate setting (`MPMV_STRIPE_CONNECT_WEBHOOK_SECRET`) passed to the provider as `connect_webhook_secret`; the platform secret is untouched. ORD.5 (#467): `commission_snapshots` in `orders/ui/state.py` calls `commission_rule_for`; the stub is gone on main. ORD.17 (#463) is merged: the terms snapshot goes in `terms_acceptance` (`orders/0014`). D-08, D-40 and D-41 are in the register with their rows (PAY.3, ORD.17, VEN.35, PAY.4) and agree with the code. |
| Spec and docs | `progress.md` had no rows for PAY.3, ORD.17, ORD.19 and VEN.35 and stale PAY.1 and PAY.2 rows; added or updated here, with the merge list (#448, #449, #452, #454, #458, #460, #462, #463, #465, #467, #469). `make docs` freshness passes. `audit-trail.md` has the PAY.3 settlement actions (`ledger.posting_blocked`, `transfer_failed`, `transfer_without_account`, `transfer_blocked`, `transfer_skipped_refunded`, `settlement_blocked_vat_mode`). `payment-providers.md` has the Stripe idempotency keys and cached errors section. |
| Hygiene | Single migration leaf per app: accounts 0009, api 0007, cart 0002, catalog 0015, cms 0018, compliance 0010, core 0025, events 0005, fitment 0001, ingest 0002, ledger 0002, manager 0006, orders 0014, payments 0002, seo 0002, shipping 0013, tax 0010, themes 0002, vendors 0034 (mcp has none). `makemigrations --check`: no changes. |
| Gates | `make check` pass. Unit, integration and e2e suites not run locally; CI on main is the gate. |

Findings and actions:

1. Closed here: check 28 finding 1 (PAY.3 landed, LIVE is no longer stopped by `payments.E004`); finding 2 (`commission_vat_mode` is read by the settlement); finding 3 (the orders migration test derives the leaf), finding 4 (consent Sec-Fetch-Site and cross-site cases) and finding 5 (the catalog loader test) all by #462; the missing `progress.md` rows.
2. Major for go-live, still open: PAY.4 (refunds) is not built. The PAY.3 guard stops a transfer to a refunded package, but no code refunds the customer or reverses a transfer; staff decide by hand until PAY.4.
3. Minor, open: the platform settles only with `commission_vat_mode` `none`; any other mode blocks every paid order until VEN.34. The demo seed sets `STANDARD`, so a seeded platform shows `ledger.settlement_blocked_vat_mode`. Action: VEN.34, or have the seed use `none`.
4. Minor, open: the Stripe idempotency cached-error limitation (`payment-providers.md`): a repeated key replays Stripe's first error for 24 hours, so a retry of a failed vendor transfer under the same key does not make a fresh attempt. Documented in `payment-providers.md`, no code change planned.
5. Minor, open: ORD.17 follow-ups: box 2 lists only seller clauses until counsel enters the customer terms clauses (`onerous_clauses`) in the CMS; the owner has to confirm that "marketplace terms" are the CMS page of kind `terms`, and that a seller with no published terms shows a notice and stores null; the PDF of a terms version is VEN.36.
6. Minor, carried over from checks 27 and 28 and still open: VEN.33, CMP.2 and PAY.4 are pure engines (finding 2 of check 27, now partly covered by PAY.3 posting); SEO.7 partial; `PRIVACY_PEPPER` has no document; TAX.18 and TAX.19 (XI VAT numbers, the adviser rules of #454 are spec only); ORD.10 on STAGE; art. 1341 comma 2 marking in code waits for VEN.13 and VEN.14; the check 26 follow-ups.
7. Info. The delta touches money, payments, ledger, orders and compliance code: PAY.1 to PAY.3, ORD.5, ORD.17, ORD.19, VEN.35. All need human review; the legal wording needs counsel (CMP.24).

Verdict: GO with follow-ups. No blocker for the merged code. Finding 2 (no refunds) matters before any LIVE payment. The payments, ledger, orders and legal code need human review.

### Coherence check 30 (main at 6cb3033d)

Scope: main at 6cb3033d, the merges after check 29 (#472): #473 (ORD.17b), #474 (CORE.25, VEN.6 shop owner passkey), #477 (PAY.3 transfer key), #479 (D-43 spec: VEN.38, VEN.19, VEN.33) and #480 (demo seed sales terms), through the trains #475, #478 and #481.

| Area | Result |
|---|---|
| Code vs spec | #474 matches the D-32 amendment: `levels.may_confirm` lets a vendor owner confirm only `SHOP_OWNER_ACTIONS` on a shop `is_shop_owner` says they own, re-checked on each call through `settings.SHOP_OWNER_CHECK` (`apps.vendors.services.is_vendor_owner`); spec 02 and D-32 carry the amendment. #477 matches PAY.3 and `payment-providers.md` (transfer key with destination and amount, blocked and demo-account cases). #473 and #480 match ORD.17 and VEN.35 (no change of status). #479 is spec only: no code reads `fee_fixed` per vendor order or the billing period yet. |
| Spec and backlog | VEN.38 is a new row (P-table total 3128), VEN.19 and VEN.33 reworded, D-43 in the register; `90-backlog.md` and `backlog.csv` were regenerated by `make docs` in #479 (not edited by hand), freshness passes. `progress.md` had no rows for ORD.17b, the #474 amendment, the PAY.3 transfer key and VEN.38; added here with the merge list. |
| Docs | Relative links in the touched docs resolve; `payment-providers.md` was updated by #477. |
| Gates | `make check` pass. Unit, integration and e2e suites not run locally; CI on main is the gate. |

Findings and actions:

1. Minor, open: #474 hardening. (a) The window path in `levels.py` has a window that is not scoped to the shop on every code path; (b) `SHOP_OWNER_CHECK` should fail closed (a wrong dotted path or an exception must deny, not raise); (c) offers should be popped after use, including in `close_window`; (d) the shop should be in `SINGLE_USED` and `record_prompt` so the audit and the friction report name it; (e) a test for a vendor owner demoted during an open window is missing. Security code: needs human review before more vendor owner actions join `SHOP_OWNER_ACTIONS`.
2. Minor, open: the spec has no rule for a per-vendor fixed fee yet in the fee engine; D-43 and VEN.38 define the fixed amount once per vendor order, and the settlement still reads the plan rule. Action: VEN.38 and a spec line in spec 04 for the per-vendor fixed fee rule before the form is built.
3. Minor, open: carried over from check 29 and still open: PAY.4 (no refunds; matters before any LIVE payment), the platform settles only with `commission_vat_mode` `none` (VEN.34), the Stripe cached-error limitation (now narrowed by the new transfer key), ORD.17 customer terms clauses pending counsel, VEN.36, VEN.33 and CMP.2 pure engines, SEO.7 partial, `PRIVACY_PEPPER` document, TAX.18 and TAX.19, ORD.10 on STAGE.
4. Info. The delta touches security, payments, ledger and orders code: CORE.25, VEN.6, PAY.3, ORD.17b. All need human review; the legal wording needs counsel (CMP.24).

Verdict: GO with follow-ups. No blocker for the merged code. The #474 hardening items should land before more shop owner actions are added. PAY.4 still matters before any LIVE payment.

### Coherence check 31 (main at 5fa7c54f)

Scope: main at 5fa7c54f, the merges after check 30 (#482): #476 (PAY.4 refunds through and outside the platform, with the duplicate payment refund; ledger 0003 to 0005, core 0026, payments 0003) and #485 (spec PAY.7, the vendor refund action), through the trains #483 and #486.

| Area | Result |
|---|---|
| Code vs spec | #476 matches the PAY.4 row: pure `refund_engine`, service in `ledger/refunds.py` with `dry_run` and `AuditLog`, pro rata reversal of commission and collection cost, refund fee from the provider cost plus surcharge frozen on the refund, L-10 exclusion flag, outside refunds with a declared date and method, duplicate payment refunded in full with no fee and no vendor share reversed (`payments/duplicates.py`), operator refund settings from D-41. Chargebacks and the vendor-facing refund action are not in the code. #485 is spec only: no `vendor_refund` action exists yet in `levels.py`. |
| Spec and backlog | PAY.7 is a new row; `90-backlog.md` and `backlog.csv` were regenerated by `make docs` in #485, freshness passes. The `progress.md` PAY.4 row still said "refunds are not built"; corrected here, PAY.7 and the merge list added. |
| Docs | `audit-trail.md` lists every new action: `ledger.refund_recorded`, `ledger.refund_fee_excluded`, `ledger.refund_marked_outside`, `ledger.refund_reversal_pending`, `ledger.refund_reversal_done`, `ledger.refund_states_pending`, `payments.payment.duplicate_flagged`, `.duplicate_refunded` and `.duplicate_refund_failed`. `payment-providers.md` now says the refund reversal key is `<refund key>:reverse:<transfer>`, amount-free on purpose, so a retry with another amount fails closed. |
| Gates | `make check` pass. Unit, integration and e2e suites not run locally; CI on main is the gate. |

Findings and actions:

1. Minor, open: #476 non-blocking review items. (a) a test for the savepoint around the state update; (b) the reversal sweep stops on the first exception instead of going on with the next refund; (c) `_apply_states` catches a broad `Exception`; (d) a pending reversal is audited again on each retry; (e) the cleared count the sweep returns is not exact; (f) optional: squash the ledger migrations 0003 to 0005 before LIVE.
2. Major for go-live, open: PAY.4 is partial. Fixes 4 to 6 of the PAY.4 review and chargebacks (dispute cost as provider cost) are not built. No vendor can start a refund yet: staff refund by hand through the service.
3. Major for the vendor channel, open: PAY.7 code not started (the `vendor_refund` critical action, the per-action rule in `levels.may_confirm`, the shop-scoped lookup, the owner's four isolation tests). Human review required when built.
4. Minor, open: carried over from check 30 and still open: the #474 hardening items (finding 1), the per-vendor fixed fee rule (finding 2), the `commission_vat_mode` `none` limit (VEN.34), the Stripe cached-error limitation, ORD.17 customer terms clauses pending counsel, VEN.36, VEN.33 and CMP.2 pure engines, SEO.7 partial, `PRIVACY_PEPPER` document, TAX.18 and TAX.19, ORD.10 on STAGE.
5. Info. The delta touches payments, ledger, orders and tax-adjacent code: PAY.4, `settlement.py`, `orders/services.py`, `vendors/commission.py`. All need human review.

Verdict: GO with follow-ups. No blocker for a STAGE release. Finding 2 still matters before any LIVE payment.

### Coherence check 32 (main at 1ffd8b5e)

Scope: main at 1ffd8b5e, the merges after check 31 (#487): #484 (CORE.30), #488 (CAT.21), #489 and #490 (SEC.5), #491 (CORE.24 part 1), #496 and #507 (ARC.34), #497 and #510 (CORE.25 hardening), #498 and #500 (ARC.20 lane account and Lane review check), #502 and #503 (THM.8), and the spec PRs #505 (owner decisions), #508 (tax adviser answers, D-46, ARC.35, clauses (j) and (k)) and #512 (`ride` on Bootstrap 5.3, THM.13 to THM.16, MQ-45), through the trains #493 to #495, #501, #504, #506, #509, #511 and #513.

| Area | Result |
|---|---|
| Code vs spec | #491 matches the CORE.24 part 1 text (index in `session_store.py` with no raw User-Agent or IP, email change ends other sessions, Support end, panic button); part 2 (role-based lifetimes) is not built. #497 and #510 close the #474 hardening items 1 to 5 of check 30 (fail-closed `SHOP_OWNER_CHECK`, offers dropped, shop in the audit, demotion test, an unscoped window covers only while the user may elevate); `tests/security` holds no strict xfail any more. #489 matches SEC.5 items 6 and 7. #502 and #503 ship `ride` CSS for the THM.8 pages; THM.16 is the move to Bootstrap 5.3. #508 and #512 are spec only: no code reads clause (j) or (k), the D-46 regime, CMP.34 or the Bootstrap foundation yet. |
| Spec and backlog | `make docs` gives no diff (`90-backlog.md` and `backlog.csv` are fresh; 401 tasks, 2,344 h of P1, 3,202 h in all). Spec 04, 11, 13, 92, `01` (ARC.35) and the CMP.5 text agree on clauses (a) to (k): the title of #508 says clause (l), the merged text folded it into (k); no (l) exists anywhere. Spec 14 has no leftover "no Bootstrap" or scroll-snap text outside the supersession note; THM.7, THM.9 and THM.10 dependencies name THM.13 to THM.16, and the hours in `91-estimates.md` now have a note for the 2026-11-15 spec PRs. The 92 register: MQ-45 is the last MQ and is unique (it opens the 2026-11-15 themes table), D-44 to D-46 are present; the register has no D-23 and no D-42 (not reused, nothing refers to them). `progress.md` had no rows for #484, #488 to #491, #496 to #512 (the 2026-11-15 work); added here with the merge list and the SEC.5 row rewritten. |
| Docs | `CONTRIBUTING.md`, `release.md` and `parallel-lanes.md` did not describe the ARC.34 CI shape (4 shards, shard check, docs-only skip, drafts skip E2E and image, `ready_for_review`); added. `manager.md` had no platform map section (CORE.30); added. The Lane review rule is in `parallel-lanes.md` and `CLAUDE.md`. `audit-trail.md` lists `accounts.sessions.ended` and `accounts.sessions.ended_all` and the shop key on the window events. |
| Gates | `make check` pass. Unit, integration and e2e suites not run locally; CI on main is the gate. |

Findings and actions:

1. Minor, fixed here: the missing `progress.md` rows, the stale SEC.5 row (it still named WIP branches), the missing CI, platform map and estimates notes (see above).
2. Major for go-live, open: CORE.24 part 2 (role-based idle and absolute lifetimes on `PlatformOperator`) is not built; until it lands staff and vendor members keep the default session length. Human review needed (authentication).
3. Major, open: spec only, no code yet, for money and legal: D-46 and VEN.34 (VAT on every fee by regime and category), CMP.2 statement wordings, CMP.34 RF19 thresholds, ARC.35 takeover runbook, clauses (j) and (k) in the CMS and the ORD.17 box 2. Tax and legal wording needs counsel (CMP.24) and the adviser's rates and Natura codes need a human check before code is built on them. The platform still settles only with `commission_vat_mode` `none` until VEN.34.
4. Minor, open: THM.13 to THM.16. The merged `ride` THM.8 CSS is plain CSS and moves to Bootstrap 5.3 in THM.16; until THM.13 measures the real sizes, the provisional 75 KB CSS and JS budgets are only in the spec.
5. Minor, open: carried over from check 31 and still open: the #476 review items, PAY.4 parts (chargebacks) and PAY.7 code, VEN.38 per-vendor fixed fee rule in spec 04, the Stripe cached-error limitation, ORD.17 customer terms clauses pending counsel, VEN.36, VEN.33 and CMP.2 pure engines, SEO.7 partial, `PRIVACY_PEPPER` document, TAX.18 and TAX.19, ORD.10 on STAGE.
6. Info. The delta touches security, authentication, settings and secrets code: CORE.24, CORE.25, SEC.5; plus the CI and review gates. All need human review.

Verdict: GO with follow-ups. No blocker for a STAGE release. Finding 2 and the PAY.4 and PAY.7 items still matter before LIVE.

### Coherence check 33 (main at 40d4fdff)

Scope: main at 40d4fdff, the merges after check 32 (#517): #514, #515, #516 and #522 (THM.13), #519 (SHP.15), #520 (CORE.24 part 2), #524 to #527 (OPS.4 Dependabot), #528 and #530 (THM.16 in two parts) and the spec PR #534 (ORD.20 to ORD.23, D-47).

| Area | Result |
|---|---|
| Code vs spec | THM.13 matches spec 14 (Bootstrap 5.3.9 in `@layer bootstrap`, hash-checked vendor files, token map, budgets ride CSS 48 KB and JS 70 KB, CSP lint). THM.16 moves the THM.8 pages to Bootstrap classes; sign-up and password-reset pages do not exist yet. SHP.15 completes the special areas (shipping 0014). CORE.24 part 2 adds role-based lifetimes on `PlatformOperator.session_policy`. #534 is spec only. Migrations core 0027 and shipping 0014 are linear and reversible. Vendored hashes match `VENDORED.toml`. No inline script or style, tooltip, popover or data-bs-html in the delta. |
| Spec and backlog | `build_docs --check` is fresh. The D-47 refund-fee exemption was missing from the older refund text in 04, 07 and 92 (point 4 also lacked the amendment refund in the critical actions); fixed here. Spec 14 wording on `VENDORED.toml` and the THM.16 row corrected. D-23 and D-42 are not in the 92 table and nothing refers to them (corrected in check 34). |
| Docs | `progress.md` had no rows for the delta; added. `docs/dev/themes.md` gets a Bootstrap foundation section; `docs/dev/admin-access.md` documents the `session_policy` keys. |
| Gates | Main CI on 40d4fdff green. `make check` pass. Unit, integration and e2e suites not run locally; CI is the gate. No credentials, real names or hostnames, no TODO without an ID. |

Findings and actions:

1. Major, fixed here: no `progress.md` rows for the delta (THM.13, THM.16, SHP.15, CORE.24, ORD.20 to ORD.23, OPS.4 and the PR table).
2. Major, fixed here: D-47 refund-fee exemption missing from spec 04, 07 and 92 (D-05 row); point (4) of D-47 now names any amendment that refunds money.
3. Minor, fixed here: spec 14 `VENDORED.toml` wording and the THM.16 row; `themes.md` and `admin-access.md` additions.
4. Minor, open: THM.16 e2e: review, payment and confirmation pages are checked only at the default width, login not at 768, and the MFA verify, TOTP and recovery codes pages and the levels prompt are not under the e2e gates at three widths (the prompt has a render test).
5. Minor, open: CORE.24: no test that exactly 72 h is accepted; the `PlatformOperator` write path through `parse_policy` is not verified; the "no enrolment without a fresh second factor" Done-when is covered only by older tests (`test_sessions.py:350`, `test_mfa_totp.py:190`, `test_mfa_passkeys.py:113`).
6. Info: EVT.7 is blocked by EVT.8 (and EVT.4, EVT.6, I18N.6), none built; CAT.12 is already done. GitHub Actions are pinned by tag, not SHA (existing convention). Human review needed: CORE.24 part 2 (authentication), the THM.16 MFA and passkey templates and the Stripe confirmation template in #528 (markup only).

Check 32 findings 2 (CORE.24 part 2, #520) and 4 (THM.13 final budgets) are closed.

Verdict: GO with follow-ups. No blocker for the 11:45 UTC STAGE release.

### Coherence check 34 (main at f8cc3896)

Scope: the merges after check 33 (main at 40d4fdff): #457 (trust answers), #532, #536 and #541 (THM.15), #538 (ORD.20 opinion spec), #540 (check 33), #543, #549 and #555 (EVT.4), #551 and #564 (ORD.20 parts 1 and 2), #553 (flaky test), #556 (CORE.32 and CORE.33 spec), #558 (THM.16 e2e), #559 (CORE.24 tests) and #565 (TAX.12).

| Area | Result |
|---|---|
| Code vs spec | THM.15 1/3 and 2/3 match spec 14 (navbar, offcanvas, search modal, HTMX mini-cart, no-JS fallback); 3/3 is open. EVT.4 meets its Done-when (e2e `test_manager_events_moderation`); spec 15 now says `VendorEventPolicy` is a custom admin view (#555). ORD.20 parts 1 and 2: trigger-guarded versions, one open amendment, orders never imports the ledger (setting hook), refunds share the PAY.4 code path, `due_by` of 14 days, no 30-day term, fee exemption not settable by hand; the row says 1/4 for the whole of ORD.20 while the PRs split it 1/3 to 3/3 (emails, durable-medium notices, downloads and Manager settings are part 3). TAX.12: gross price equals what checkout charges, the catalog has no tax import (new contract). Migrations orders 0016 to 0019 and ledger 0006 are linear. No vendored file changed. No inline script or style in the delta (the JSON-LD data block is the existing one). |
| Spec and backlog | `build_docs --check` is fresh. Every D-xx and Q-xx in the delta exists in 92. D-23 and D-42: nothing refers to them; check 33 was wrong to say they are referenced (check 32 had it right): the gaps stay unused. |
| Docs | `progress.md` had no rows for the delta; added (THM.15, EVT.4, ORD.20, TAX.12, CORE.32 and CORE.33, TRU) and the PR table. `docs/dev/themes.md` gets the ride navbar and overlays; `docs/dev/manager.md` the shop events section. |
| Gates | Main CI green on 33f50cab and earlier; the run on f8cc3896 was still in progress. `make check` pass. Unit, integration and e2e suites not run locally; CI is the gate. No credentials, real names or hostnames, no TODO without an ID (the two existing ones name CORE.18 and CMP.4). |

Findings and actions:

1. Major, fixed here: no `progress.md` rows for the delta.
2. Minor, fixed here: `themes.md` and `manager.md` additions; check 33's D-23 and D-42 note corrected.
3. Minor, open: the ORD.20 spec row is titled 1/4 but the work ships as parts 1/3 to 3/3; call the remaining part by the same names (suggest: lane-a renames the row text on the next spec PR).
4. Minor, open: TAX.12 has no geolocation step and no business opt-in toggle (spec says "when one exists"), and the JSON-LD `offers` wait for SEO.1; the row stays partial until the feeds exist. CMP.14 UK lead-only check is deferred for ORD.20 (no CMP.14 code yet).
5. Info: CI on the merge commit was still running at the time of the check; the previous heads are green.
6. Info: EVT.4's `VendorEventPolicy` is not a Wagtail ViewSet; spec 15 was changed to match.

Check 33 findings 4 (THM.16 e2e widths, closed by #558: review, payment, confirmation, login, MFA verify, TOTP, recovery codes and the levels prompt at 360, 768 and 1280) and 5 (CORE.24, closed by #559: 72 h and 365 day boundaries, idle ceiling, operator write path; the fresh-second-factor enrolment case stays covered by the older tests) are closed.

Human review needed: ORD.20 parts 1 and 2 (orders, payments, ledger: refunds, fee exemption, passkey digest), TAX.12 (tax: the rate shown to consumers, rounding, estimated country, the owner's note), EVT.4 (moderation flow and uploads).

Verdict: GO with follow-ups. No blocker.

### Coherence check 35 (main at 08800668)

Scope: the merges after check 34 (main at f8cc3896): #569 (check 34), #570 (owner answers of 16 November), #577 (ORD.20 part 3a), #580 (clause (l) counsel answer), #585 (Matomo, domains and mail at cutover, MIG.22), #589 (follow-up rows) and #590 (merge train). #573 (D-48 accounting connector) is still open and not in scope.

| Area | Result |
|---|---|
| Code vs spec | #577 meets its Done-when for part 3a: Manager draft, preview, revise, discard and apply; the passkey prompt for a critical change (`order_amend_address`, `vendor_refund`, `order_amend_cancel`) is in the CORE.25 critical lists in `session_policy.py` and the shop grants in `accounts/levels.py`; staff tools and customer pages exist; the cancel cause (`vendor_fault`, `customer_request`, `platform_error`) decides the refund fee and the restock; Manager settings live on `PlatformOperator`. Migrations are linear: core 0028 after 0027, ledger 0007 after 0006 (and orders 0019, vendors 0034). `makemigrations --check` finds no change. No inline script or style, no `<script>` or `style=` in the template delta; the `ride` customer order page was updated with the base. The import contracts pass (`make check`). #570, #580, #585 and #589 are spec only. |
| Spec and backlog | `make docs` gives no diff; `build_docs --check` is fresh. The 14 new rows (CAT.22, CAT.23, VEN.40, TAX.22, ORD.25, I18N.16 to I18N.18, CMS.17, SEO.13 to SEO.15, CMP.34 text, MIG.22) have a Done-when and every dependency exists. IDs are plain numeric: no `X.12b`-style ID is left in `docs/spec` (CAT.5b and SEC.5b only appear as "formerly" and "proposed as" notes). MQ-43, MQ-46 and MQ-47 and D-46 to D-48 exist in 92; Q-08 and Q-91 are owner-decision labels cited in spec 13 only (older register, not in 92). The "1/4" to "4/4" in ORD.20 to ORD.23 is the numbering of four rows, not parts of ORD.20; check 34 finding 3 was a misreading and is closed. |
| Docs | `progress.md` had rows for #577 (ORD.20 3a, ORD.22) but none for #570, #580, #585 and #589; added with the PR table. The ORD.20 to ORD.23 "spec only" row was stale (ORD.20 and ORD.22 are partly built); split. `audit-trail.md` lists the #577 actions (`orders.amendment_package_cancelled` with the cancel cause, `accounts.session.prompt`, `ledger.refund_fee_excluded` with `customer_requested_cancellation`). `manager.md` and `themes.md` describe the amendment pages. `platform-roles.md` is regenerated and unchanged. |
| Gates | `make check` pass. CI on main: the run on 08800668 was queued at the time of the check; the previous heads (1776801e and 5fcfc4b1) are green. Unit, integration and e2e suites not run locally; CI is the gate. No credentials, real names or hostnames in the delta; no TODO without an ID. |

Findings and actions:

1. Minor, fixed here: no `progress.md` rows for #570, #580, #585 and #589; the stale ORD.20 to ORD.23 row.
2. Minor, closed: the "1/4" row label of ORD.20 (it numbers four rows).
3. Minor, open: TAX.12 stays partial (no geolocation header, no business opt-in toggle until TAX.22, JSON-LD `offers` wait for SEO.1); TAX.22 is the code change that the owner answer asks for.
4. Minor, open: clause (l) cites the suspension clause as "[x]"; set it to the real letter when the suspension clause gets one (CMP.5, VEN.13).
5. Minor, open: ORD.20 part 3b (emails, durable-medium notices, downloads) is not built; ORD.21, ORD.23 to ORD.25 are spec only.
6. Info: the analytics change (Matomo, cookieless, consent-free) depends on counsel's answer (MQ-46); the banner and consent log of SEO.5 stay until then.

Human review needed: ORD.20 3a and ORD.22 (money: the cancel-cause refund-fee rule, restock, the ledger 0007 fee exclusion for a customer's request or a platform error, the passkey for refunds); CMP.34 thresholds (tax); clause (l) and the P2B waiver wording (legal, counsel); Matomo without consent (legal, privacy).

Verdict: GO with follow-ups. No blocker.

### Coherence check 36 (main at b6241cbe)

Scope: the merges after check 35 (main at 08800668), the first-parent chain being train merges: #561, #563, #571, #572, #573, #575, #576, #578, #579, #582, #591, #593 and #597 (50 commits, 247 files). #589 was already in check 35.

| Area | Result |
|---|---|
| Migrations | Linear, no duplicate number in any app: core 0028 then 0029 (`site_languages`), cms 0018 then 0019, 0020, 0021, shipping 0004 then 0005 (data seed of country groups). Ledger stays at 0007, fitment and events have no new migration. `makemigrations --check` reports no changes |
| Spec and backlog | `make docs` in a clean worktree at the sha gives no diff in `backlog.csv`, `90-backlog.md` or the generated docs |
| Progress rows | Missing for #561, #563, #571, #572, #573, #575, #576, #578, #579, #582, #591, #593 and #597; added with the PR table. #575, #579 and #591 show as closed on GitHub because the train PRs merged their commits |
| Anonymization | The diff adds no credential-like literal, no real vendor name and no hostname. The only external origins are the two public no-tracking video hosts (see below). The CMP.35 commit adds a gitleaks ignore for a false positive in an earlier spec commit |
| Gates | `make check` pass at the sha. Unit, integration and e2e suites not run locally; CI and the non-English nightly job are the gate |

Operator notes for STAGE v0.1.17:

1. New environment variables: none in the application. The only new `MPMV_LANGUAGES` use is the CI job (`it,de`); the setting itself exists since #321.
2. Migrations to apply: `core/0029_site_languages`, `cms/0019_hero_layout_strip`, `cms/0020_showcase_blocks`, `cms/0021_standard_page_noindex`, `shipping/0005_seed_country_groups`. All run in order on a database at core 0028, cms 0018 and shipping 0004.
3. New management command: `change_languages` (I18N.15, #578): changes the site languages and records language redirects. A removed language keeps same-site redirects only.
4. CSP: `frame-src` is not a setting. The origins `https://www.youtube-nocookie.com` and `https://player.vimeo.com` are the constant `VIDEO_EMBED_ORIGINS` in `apps/cms/engine.py`; `VideoEmbedCspMiddleware` adds them only on responses that render a video block. Nothing to configure; a reverse proxy must not set its own stricter `frame-src`. (Superseded by #681: the origins are the validated setting `MPMV_VIDEO_EMBED_ORIGINS`, check `cms.E003`.)
5. Matomo and analytics: no setting added. #585 (Matomo replaces Google Analytics) is spec only; the banner and consent log of SEO.5 stay until counsel answers (MQ-46).
6. Sitemaps are now per type and per language, and `llms.txt` is generated (SEO.2); sitemap chunks refill their cache after the deploy.

Findings and actions:

1. Minor, fixed here: 13 PRs without a `progress.md` row.
2. Minor, open: the task table rows for I18N.15 and FIT.1 still say "partial" with the command and the Manager pages open; both are built now (#578, #572).
3. Minor, open: #576 (EVT.6) shows as open on GitHub although train #605 merged it.
4. Info: Matomo and consent-free analytics wait for MQ-46; D-48 (#573) is spec only.

Human review needed: none new in tax, payments or ledger. The public event and showcase pages and the CSP change for video embeds are for the release smoke test.

Verdict: GO. No blocker.


### Coherence check 37 (main at b1591742)

Scope: the nine train merges after check 36 (main at b6241cbe): #608 (#606), #609 (#607), #611 (#610, #583), #615 (#592), #620 (#616), #622 (#619), #623 (#618), #626 (#586) and #627 (#584, #586); 80 files. #572, #573 and #576 were already in check 36. #586 (EVT.8) shows as closed on GitHub because the trains merged its commits. #602 (I18N.16, I18N.18) is still open and not in scope.

| Area | Result |
|---|---|
| Code vs spec | CAT.4 meets its Done-when (renditions job, D-18 replacement in DE only, alt fallback), declared as services only: no vendor upload UI, variant galleries, orphan cleanup or monitoring (CAT.24 to CAT.26). FIT.2 meets cases 1 to 3 as unit tests without a database; the visibility half of case 3 belongs to the garage filter, the publish gate is FIT.18, proposed-node rows are FIT.3. EVT.8: cases 9, 10 (HTML) and 11 are tested; the translate route and API are in the guard registries. THM.15 3/3: banner, footer columns, social links and back-to-top are built and tested, but no page includes the banner (THM.7, THM.9) and no CMS setting feeds the footer (CMS.17); both are declared in the PR and in the spec. ORD.20 adviser rules (a) to (e) and the chained amendments are covered by AM1 to AM11 in two test files; the PR table still says "AM1 to AM8" for #619 while the spec now has AM11 (#618 and the review commit), both are met by tests. Migrations are linear with no duplicate number: catalog 0015 then 0016, fitment 0001 then 0002; core stays at 0029, orders at 0019, ledger at 0007, events at 0005 (EVT.8 needs none); `makemigrations --check` reports no changes. No new inline script or style, no vendored file changed, import contracts pass in `make check`. The ride theme.css is 43052 B gzip of a 46080 B budget (PR measure), ride.js 500 B. The CSP gets no change; the only new external origins in the diff are the two video hosts already in check 36 |
| Spec and backlog | `uv run tools/build_docs.py --check` is fresh. The new rows CAT.24 to CAT.26, FIT.18, TAX.22 and CMP.40 have a Done-when and all their dependencies exist; no suffixed ID exists; every D-xx and MQ-xx cited in the delta exists in 92. The brief named an ORD.26: no such row exists, nothing cites it |
| Docs | `progress.md` had no row for #583, #584, #586, #592, #606, #607, #610, #616, #618 and #619 and no task rows for CAT.4, FIT.2, EVT.6 and EVT.8: added. Stale rows fixed: I18N.15 (the `change_languages` command is built), FIT.1 (Manager pages built, #572), THM.15 (3/3), ORD.20 (adviser rules), OPS.9 (ZAP 10202), CMP.5 (clause (m)). `docs/dev/background-jobs.md` lacked the `catalog.render_product_image` job; `themes.md` lacked THM.15 3/3; `manager.md` lacked the event translate page; all added. `security-scans.md` and `audit-trail.md` were updated by their PRs. There is no dev doc for product media or fitment; the code docstrings and the spec carry them |
| Gates | `make check` pass at the sha. Main CI was green on the last merge at the time of the check. The delta adds no credential-like literal, no real vendor name and no hostname (`example.org` and `https://x` are test fixtures). No `TODO` without an ID. `.gitleaksignore` has one entry (the #573 spec false positive), unchanged and still valid |

Check 36 open minors:

1. Minor, closed here: the I18N.15 and FIT.1 task rows still said "partial".
2. Minor, closed: #576 shows as closed on GitHub; its commits are on main.
3. Minor, open: TAX.12 stays partial (no geolocation header, no business opt-in until TAX.22, JSON-LD `offers` wait for SEO.1).
4. Minor, closed by spec: clause (l) now cites clause (m); CMP.40 (spec only, not built) is the guard against a placeholder like "[x]" in a published terms version.
5. Minor, open: ORD.20 part 3b (emails, durable-medium notices, downloads) is not built; ORD.21, ORD.23 to ORD.25 are spec only.
6. Info, open: Matomo without consent waits for MQ-46.

Code findings (no code changed here):

1. Medium, tax (#619): the vendor order stores one shipping VAT rate (a database check) and checkout blocks a package with two shipping rates, so a mixed 22 % and 4 % shipping is kept as one 13 % figure on the vendor order and as the real split in the shipping choice, which version 1 reads. The amendment code is right for AM1 to AM11, but any report that reads the vendor order's shipping rate alone sees the blended figure. The PR documents it; it needs a decision before ORD.3's mixed-rate shipping lands (ledger, VAT reports).
2. Low, tax (#619): rule (c) takes the second payment's date from the draft preview (`now`) because ORD.21 does not exist yet; the production path with a real second payment is untested until ORD.21.
3. Low (#592): `upload_image` services have no caller until CAT.19, ING.6 and MIG.3; deleting a gallery row leaves its `MediaImage` (CAT.25).
4. Low (#607): the 10202 accept is global in ZAP; the sweep `tests/unit/test_post_forms_csrf.py` is the only guard, so it catches only a template that posts a form without `{% csrf_token %}`; a new CSRF-exempt view that renders such a form in another way is not seen.
5. Info (#584): the footer `widgets` and `social_links` are not passed by any view yet; the footer is unchanged for visitors.

Operator notes for the next STAGE release: migrations to apply `catalog/0016_product_images` and `fitment/0002_product_declaration`; no new environment variable; the worker must run the new `catalog.render_product_image` job (a page shows without renditions until it runs); no CSP change.

Human review needed: ORD.20 adviser rules (#619, tax: shipping VAT split and the second-payment date); clause (m) and clause (l) wording (#616, legal, counsel; the Italian text is a proposal); the ZAP 10202 acceptance (#607, security: the owner approved it on 2026-11-17); CAT.4 media services (dedup scoping and audit actions, flagged in #592).

Verdict: GO with follow-ups. No blocker.

### Coherence check 38 (main at 2b68b06f)

Scope: the eight train merges after check 37 (main at b1591742): #631 (#602), #637 (#633, #632, #612), #638 (#621), #639 (#636), #642 (#588), #643 (#624), #644 (#625, #641, #629) and #645 (#640); 173 files, about 40 000 lines, most of them the DE/FR/ES catalogs. #614, #617, #628, #630, #634, #635, #646 and #362 are open and not in scope.

| Area | Result |
|---|---|
| Code vs spec | EVT.9 meets its Done-when: the feed part of cases 10 and 12 to 16 and 20 has named tests (`test_case_12_...`, cancelled retention, one VEVENT with RRULE and EXDATE, ETag 304 and change after a hide, no email or phone or member name, GEO and `georss:point`), the 31st request gets 429 with `Retry-After` and a 304 counts, every feed parses with `icalendar` or `defusedxml` and a hand-written RSS 2.0 rule check (no external RSS validator; minor, the Done-when wording says "validator"), and the discovery list carries both alternate links. The rate limit reads `client_ip` (a spoofed forwarded header does not change the bucket) and a 404 scope writes no throttle row. SEO.7 part 1 is declared partial: `seo.page_graph` registry with builders for the home, CMS pages, blog, product, listing and event index, one JSON-LD script, a hostile product name inert, a sweep over the registered types, the 30 KB bound; the search results page waits for CAT.27 (#634, open) and the event and profile types for part 2. CAT.7 2/2 meets its Done-when (gallery from the renditions, related products, axe and CSP e2e in both themes); the original-image fallback is #635 (open). I18N.16 and I18N.18 meet their Done-whens (sitemap version bump through the services, `core.E921` and `site_root_url()` that raises in production). I18N.7 slice 2 adds the DE/FR/ES catalogs and the I18N.19 interim guard: production serves `en,it` only (`MPMV_LANGUAGES` default, `deploy/server/mpmv.env.example` agrees); the I18N.19 gate checks (1) to (4) are not built, which is declared. ORD.20 3b-1 is declared partial (3b-2 downloads open): notices, invoice notice and the trigger-guarded `OrderEvent` have integration tests for each notice, its text version and its page. Migrations are linear: core 0030 (`sitemap_version`) and 0031 (the 24 h cancel-hold minimum), orders 0020; the ledger 0007 change is a docstring only; `makemigrations --check` reports no changes. Spec only and correct as spec: SEO.5 Matomo consent-free conditions, CAT.27, ORD.26 to ORD.29, ARC.36, ARC.37, I18N.19 |
| Spec and backlog | `uv run tools/build_docs.py --check` is fresh. The new rows ARC.36, ARC.37, CAT.27, I18N.19 and ORD.26 to ORD.29 have a Done-when or an explicit scope and their dependencies exist; no suffixed ID exists. Open PRs hold CAT.28 (#634) and CAT.29 (#635), so the next free IDs are not reused here. Check 37 said no ORD.26 existed: it exists now (outbox bodies that are forgotten with the order, lane-b review of #624) |
| Docs | `progress.md` had no row for #588, #612, #621, #624 (task row only), #625, #629, #632, #633, #636, #640, #641 and #602 (check 37 only), and the task rows for CAT.7, SEO.7, SEO.5 and I18N.7 were stale; no rows existed for EVT.9, I18N.16, I18N.18 and I18N.19: all added or fixed. `docs/dev/translations.md` lacked the I18N.19 gate, `review-export` and the I18N.18 base URL rule: added. `audit-trail.md` and `background-jobs.md` were updated by their PRs. There is no dev doc for the feeds or the page graph; the module docstrings and the spec carry them (not a defect) |
| Gates | `make check` pass at the sha (ruff, mypy strict, import contracts, backlog freshness, Django checks, theme quality gates for base and ride). `make test` under the shared lock (one worker, 7.5 GB machine): stopped at 22 % after about 20 minutes with no failure, because a full run needs about 90 minutes here; CI on main is the gate, and its run 44142558904 on 2b68b06f is green The delta adds no credential-like literal, no real vendor name and no hostname (`shop.example`, `example.org`, `example.test`, `localhost` are fixtures). No inline style or script in a template, no vendored file changed, no `TODO` without an ID, `.gitleaksignore` unchanged. The CSP gets no change |

Check 37 open items:

1. Medium, tax (shipping VAT blended on the vendor order): now specified as ORD.29 (#636, spec only, not built); still open as code. Needs the decision before ORD.3's mixed-rate shipping lands.
2. Low (second payment date from the draft preview until ORD.21): unchanged, open.
3. Low (#592 `upload_image` callers, gallery row deletion leaves its `MediaImage`): unchanged, CAT.25.
4. Minor, closed: the Matomo question (MQ-46) is answered by counsel (#625); the spec now allows consent-free Matomo under conditions; not built.
5. Minor, open: TAX.12 stays partial; ORD.20 part 3b-2 (PDF and CSV downloads, the guest's signed link) is open; ORD.21, ORD.23 to ORD.25, ORD.27, ORD.28 are spec only.

Findings:

1. Note, lane-a (#624): the outbox keeps the rendered email text and the `OrderEvent` keeps the exact notice text; the `OrderEvent` is scrubbed at anonymisation, and ORD.26 asks that the outbox body is forgotten with the order too. Until ORD.26 is built the outbox body is a retention point for the privacy review.
2. Minor, lane-b (EVT.9): the Done-when says "RSS 2.0 validator"; the tests check the RSS 2.0 rules by hand (channel title, link and description, item fields) and parse the XML. No defect; the wording could say "RSS 2.0 rules". No row needed.
3. Minor, lane-b (I18N.19): the gate rests on a settings default; no system check refuses a LIVE deploy that lists `de`, `fr` or `es`. It blocks LIVE and is already a Done-when item (1) of I18N.19; no new row.
4. Note, lane-b (SEO.7): the task stays partial; the product-card and grid `ItemList`s of the Done-when and the search results page depend on CAT.27 (#634).
5. Note: #633 (check 37) is itself in this delta; check 37's `progress.md` edits landed as written.

No defect found in a lane-b area that needs a new row. Nothing found in lane-a code beyond the notes above (reported only, no edit).

Operator notes for the next STAGE release: migrations to apply `core/0030_sitemap_version`, `core/0031_amendment_cancel_hold_minimum` and `orders/0020_ord20_order_events`; `MPMV_BASE_URL` must be a real https root (new check `core.E921`) and the default Wagtail `Site` must match it; the new feed routes sit under the events index in every language and are rate limited per IP; keep `MPMV_LANGUAGES=en,it` on LIVE (the staff preview list is for STAGE only); no CSP change.

Human review needed: ORD.20 3b-1 (#624, orders and tax, counsel-approved wording; DE, FR and ES wait for the translator); the public feeds (#629: field allow list and the rate limit); the DE/FR/ES catalogs (#588, I18N.19, translator and counsel before LIVE).

Verdict: GO with follow-ups. No blocker.

### Coherence check 39 (main at 9ae06b98)

Scope: the train merges after check 38 (main at 2b68b06f): #650 (#649, check 38 itself), #651 (#614), #652 (#617), #653 (#630) #656 (#634) and #658 (#646, which carries #628); 163 changed files outside the catalogs, about 4 000 lines added, mostly tests, templates and locale entries. #635, #647, #654 and #362 are open and not in scope.

| Area | Result |
|---|---|
| Code vs spec | CAT.27 meets its Done-when: `/?s=` and `/it/?s=` answer on the language home (`HomePage.serve`, reads only) with products first through CAT.9 and the other groups below; named tests for SKU, partial SKU, variant SKU, GTIN, MPN (and the draft's GTIN finding nothing), the typo fallback, public-only items (drafts, hidden events, suspended vendors, view-restricted and other-language articles), `noindex, follow` on every outcome, a plain GET form, the 100-character cut, escaping, filters with numbered pagination, a bounded query count, 429 over `SEARCH_RATE_LIMIT` and no slot used by an empty query; the e2e runs axe and the CSP recorder at 360, 768 and 1280 px in both themes and both schemes, with and without a match, and once with JavaScript off. The SEO.7 `SearchResultsPage` node is not built, as the row says. CAT.28 (events filtered in Python) is a spec row, not built. EVT.7 meets its Done-when: the next three public events of the vendor, a DE-only event with `lang="de"` and its language name, a hidden, cancelled or published change on the next response, a query count that does not grow, a suspended vendor shows none. EVT.11 meets its Done-when: one email per transition in the vendor's language, a repeated signal queues nothing, no digest for an empty queue, Wagtail's own moderation emails off, the changes link works once and is dead after the resubmit, and `test_a_notice_on_an_event_ends_with_the_page_gone_from_pages_and_feeds` in the CMP.5 statement suite. THM.7 parts 1 and 2 (#628, #646) cover the listings, the product page and the storefront profile: the ride overrides are copied from the base templates with the same ids and hooks, and have render tests under ride, an axe and CSP e2e at the three widths, and keyboard-only e2e for the pagination, filter form, gallery carousel, tabs and accordion, 44 px target and reduced motion tests; the search results page is not overridden for ride (THM.17), so the row is declared partial. I18N.15's test hardening (#614) is declared in its own task row (done); the it,de CI job is the gate. No migration in scope; `makemigrations --check` reports no changes |
| Spec and backlog | `uv run tools/build_docs.py --check` is fresh after this PR's wording fix. CAT.28 has a Done-when and its dependencies exist; the open PRs hold CAT.29 (#635) and no THM row, so THM.17 (new here) and later IDs are free; no ID is reused. The EVT.9 Done-when now says "hand-written RSS 2.0 rule checks (no external validator)" (check 38 minor 2, closed) |
| Docs | `progress.md` had no task rows for EVT.7, EVT.11 and CAT.27 and no PR rows for #614, #617, #628, #630, #634, #646 and #649 and no THM.7 row: added (THM.7 partial, THM.17 spec only); the I18N.15 row lists #614 and the THM.15 row no longer says no page includes the banner. `docs/dev/manager.md` (statements page) and `audit-trail.md` (digest action) were updated by #617; `deploy/README.md` by #634 (the search rate limit joins the trusted-proxy note); the `Throttle` docstring names the search keys. No dev doc for the search page; the module docstring and the spec row carry it (not a defect) |
| Gates | `make check` pass at 9ae06b98 (ruff, ruff format, mypy strict on 1610 source files after clearing `.mypy_cache`, import contracts, backlog freshness, Django checks, `makemigrations --check`, theme quality gates for base and ride). Targeted suites with `-n 0` and `MPMV_TEST_DB_NAME=test_mpmv_check39, and from the new main `tests/integration/test_ride_catalog.py` and `test_ride_product.py` (22 passed in 86 s), `apps/manager/tests/integration/test_shop_events.py`, `tests/integration/test_language_switcher_everywhere.py`, `test_ride_events.py` and `apps/themes/tests/unit/test_ride_templates.py` (198 passed, 34 skipped for the disabled current-language buttons, 374 s)`: `tests/integration/test_search_page.py`, `apps/events/tests/integration/test_storefront_events.py`, `test_emails.py` and `apps/compliance/tests/integration/test_statement_of_reasons.py`: 96 passed in 190 s. `make test` was not run (about 90 minutes here); CI on main is the gate: run 44179706985 on adb83dbe is green (static checks, secret scan, four test shards, language and upgrade checks, E2E Playwright, images), and the train PR #658 run 44190055386 (head f1a5f2d9, the same tree as 9ae06b98) is green; the main run 44193483799 on 9ae06b98 was still in progress when this was written. Import contracts hold: `catalog` imports `cms` only through a registry hook, `cms.services` imports `catalog.search_page` (allowed edge), `events.notifications` imports `accounts`, `vendors` and `core` only. The delta adds no credential-like literal, no real vendor name, no hostname other than `localhost`, `example.org`, `evil.example`; no inline style or script in a template; no vendored file changed; `.gitleaksignore` unchanged; no `TODO` without an ID. Money and tax code is not touched |

Check 38 open items:

1. Minor, closed here (EVT.9, "RSS validator" wording): the Done-when in spec 15 now names the hand-written RSS 2.0 rule checks; `make docs` regenerated.
2. Minor, still open (I18N.19, no LIVE system check): the guard is still the `MPMV_LANGUAGES` default `en,it` (`mpmv/settings/languages.py`) and `deploy/server/mpmv.env.example`; no check refuses a LIVE deploy that lists `de`, `fr` or `es`. It is Done-when item (1) of I18N.19; no new row.
3. Note (SEO.7): the search results page it waited for exists now (#634); the `SearchResultsPage` node and `WebSite` + `SearchAction` are SEO.7 part 2.
4. Note (ORD.26, ORD.29, TAX.12, ORD.20 3b-2, ORD.21): unchanged, lane-a.

Findings:

1. Note, lane-b (CAT.27): every non-empty search writes one `Throttle` row per client and minute (`search:ip:*`), so a public search adds a database write per request, the same pattern as the event feeds; the row is purged nightly. Acceptable at the expected traffic; if the shop is crawled hard the write load follows it. No row; revisit when a shared cache with an atomic counter exists.
2. Note, lane-b (CAT.27): the articles group matches `title` and `summary` with `icontains` and the events group is filtered in Python; both are bounded by the page's limit or by CAT.28 (open). No defect at the current catalogue size.
3. Note, lane-b (I18N.15): a few older tests still compare a locale code with the literal `it` (`test_v1_event_translations.py`, `test_multilingual_events.py`, `tests/e2e/test_manager_events_moderation.py`, all EVT.8). `it` is a configured language in every CI configuration, so they pass; they would fail on an installation without Italian. Not part of #614's scope; no row.
4. Note, lane-a (EVT.11 touches `apps/compliance/services.py` and `apps/manager/urls.py`): the changes are the notice target for an event page and the statements page of the own shop; reviewed, scoped to the vendor, no notifier identity shown (`test_the_statements_page_shows_no_notifier_identity`).
5. Minor, lane-b (THM.7, row proposed): the ride theme has no `catalog/search.html` override, so the CAT.27 results page renders the base template inside the ride layout (the e2e gates pass); the THM.7 row lists "search results" and the #646 body says it follows with CAT.27. New row THM.17 (P2, 3 h, THM.7 and CAT.27) in spec 14; THM.7 is `partial` in `progress.md` until it lands.

One gap in a lane-b area needs a row: finding 5 (THM.17). No defect otherwise. Nothing found in lane-a code beyond the notes above (reported only, no edit).

Operator notes for the next STAGE release: no migration in this delta; new setting `SEARCH_RATE_LIMIT` (default 30 per minute per client IP); the daily `events.review_digest` job (08:30) is registered by the worker; the search page answers `/?s=` on every language home; no CSP change; keep `MPMV_LANGUAGES=en,it` on LIVE.

Human review needed: the ride product page and storefront (#646, tax display rules TAX.12 kept in the copy: check the price, tax and shipping text against the base page); EVT.11 (#617: the emails to vendors and the DSA notice target for events; DE, FR and ES texts wait for the translator); the public search page (#634: the rate limit and the query handling).

Verdict: GO with follow-ups. No blocker.

### Coherence check 40 (main at 4009ea06)

Scope: the train merges after check 39's own train (#665, which carried #660, #647 and #635; main at fc3a29a8): #669 (#661), #679 (#664, #667, #668, #666), #682 (#662, #654, #670, #674), #688 (#671, #673, #681) and #690 (#675, #683, #686, #676); about 4 800 lines changed outside the catalogs (help files, tests, templates). #678, #680, #677, #689, #685, #684, #687, #691, #672 and #362 are open and not in scope. #647 (ARC.36) and #635 (CAT.4) merged with check 39's train and had no rows: added here.

| Area | Result |
|---|---|
| Code vs spec | THM.17 meets its Done-when: `catalog/search.html` for ride is copied from the base template (same blocks, ids, hooks, `s`, `in` and `page` parameters, `noindex, follow`), a parity test compares base and ride on six cases (results, paged, one group, filtered, empty, no match), the e2e runs axe wcag22aa and the CSP recorder at 360, 768 and 1280 px, keyboard-only submit and pagination, 44 px targets and reduced motion; THM.7 is done (parts 1 to 3: #628, #646, #667, #676). THM.7 part 3 (#676) shows the specifications and the fitment summary through the `catalog_product_fitment` hook (the catalog does not import `fitment`) and only active nodes (spec 17.2.4); the FAQ and reviews tabs are not built (no data). SEO.7 part 2 (#673) meets its Done-when items for the search results page (`SearchResultsPage` with one mixed `ItemList`, `noindex, follow`), the two named `ItemList`s of a page with a product grid and product cards, `CollectionPage` and the `WebSite` `SearchAction`; the task stays partial for the `Event` nodes (EVT.10) and the widget lists. CAT.29 (#671) meets its Done-when (`test_blog_renditions.py`: the rendition count does not change for the article and author pages, alt, credit and caption kept; the `core.render_image` job makes the `article` and `avatar` presets). CAT.25 (#686) meets its Done-when (listed, one `AuditLog` row per deleted image, an image still in a gallery or added to a reference after the list was shown cannot be deleted; `dry_run` and `ChangeSet`). EVT.9's review minors (#654) each have a named test and the Subscribe block an e2e at three widths in both themes. EVT.8 (#683) has the two suspended-vendor tests. THM.14's setting (#681): `MPMV_VIDEO_EMBED_ORIGINS`, the CSP, the video block and `cms.E003` read one list; the check refuses `http://`, `*`, wildcard hosts, paths, queries, ports and credentials. ARC.36 part 2 (#666) gates the add view of the help entries and sweeps every add, edit, delete and copy view of the registered Manager snippet viewsets. ARC.37 parts 3 to 6 (#661, #662, #675) take the baseline from 72 to 20 keys; DE, FR and ES are AI drafts behind I18N.19. I18N.13's register fixes (#664) have catalog tests for the informal customer privacy page, the "Your data" context and the formal second-factor pages. I18N.19 export group (#674): the EVT.11 statements and moderation mails are in `review-export` with their own groups (the gate itself is still open). CMP.5 clause (m) (#668) and OPS.15 and OPS.16 (#670) are spec only and correct as spec. No migration in scope; `makemigrations --check` reports no changes |
| Spec and backlog | `uv run tools/build_docs.py --check` is fresh after this PR's new row. New row I18N.20 (P1, 12 h, I18N.7): the storefront templates are in no interface catalog (finding 1); IDs checked on main and the open PRs (#677, #678, #680, #684, #685, #687, #689, #691): I18N.20 is free and no ID is reused. The ARC.37 row keeps its parts in the spec; `progress.md` carries which are merged |
| Docs | `progress.md` had no task rows for ARC.37, THM.14, THM.17, CAT.25, CAT.29, OPS.15 and OPS.16, an ARC.36 row that said "PR pending number" and no rows for #635, #647, #654, #661 to #668, #670 to #671, #673 to #676, #681, #683 and #686; the THM.7, SEO.7, I18N.13, I18N.19, CMP.5, EVT.8, EVT.9 and CAT.4 rows were stale: all added or fixed. The check 35 operator note "frame-src is not a setting" is marked superseded by #681. `docs/dev/themes.md`, `deploy/server/mpmv.env.example` (#681), `docs/dev/audit-trail.md` (#686), `docs/dev/translations.md` (#664, #674), `docs/dev/help-content.md` (#661, #662, #666) and `docs/dev/dependency-updates.md` (#670) were updated by their PRs |
| Gates | `make check` pass at 4009ea06 (ruff, ruff format, mypy strict after clearing `.mypy_cache`, import contracts, backlog freshness, `i18n_catalog check`, Django checks, `makemigrations --check`, theme quality gates). Targeted suites with `-n 0` and `MPMV_TEST_DB_NAME=test_mpmv_check40`: from fa34e9e1, `tests/integration/test_ride_search.py`, `apps/seo/tests`, `apps/cms/tests/unit`, `test_blog_renditions.py`, `test_help_sync.py`, `test_showcase_blocks.py`, `apps/events/tests/integration/test_feed_discovery.py`, `tests/unit/test_review_export.py`, `test_language_catalogs.py`, `test_help_entry_access.py`, `test_help_coverage.py`, `test_session_levels.py` and `test_italian_pages.py`: 1271 passed in 215 s; from 4009ea06, `test_media_cleanup.py`, its engine tests, `test_orphan_images_in_content.py`, `test_product_specs.py`, `test_ride_product.py`, the EVT.8 translation tests, `test_help_coverage.py` and `test_language_catalogs.py`: 141 passed in 124 s. `make test` was not run (about 90 minutes here); CI on main is the gate: run 44221889517 on fa34e9e1 is green (the non-English integration run 44223065269 and the security nightly 44223927961 on the same sha are green too); the run 44224912652 on 4009ea06 was still in progress when this was written. Import contracts hold (`seo.page_builders` imports `catalog` and `events` inside functions, as in part 1; `catalog.product_pages` reaches `fitment` through a hook). The delta adds no credential-like literal, no real vendor name, no hostname other than `localhost`, `example.org`, `example.com`, `shop.example` and `player.example.com`; no inline style or script in a template; no vendored file changed; `.gitleaksignore` unchanged; no `TODO` without an ID. Money and tax code is not touched (the ride product page keeps the TAX.12 price note; #676 changes only the tabs below the buy box) |

Check 39 open items:

1. Closed (THM.17): the ride search results page landed (#667); THM.7 is `done`.
2. Open (I18N.19, no LIVE system check): unchanged; the guard is the `MPMV_LANGUAGES` default `en,it`. The gate checks (1) to (4) now belong to lane-a (I18N.15 part 3 and I18N.19); the EVT.11 strings joined the review export (#674).
3. Closed (SEO.7 search results page): built in part 2 (#673); the `Event` nodes wait for EVT.10.
4. Note (CAT.28, ORD.26, ORD.29, TAX.12, ORD.20 3b-2, ORD.21): unchanged, lane-a.
5. Note (check 39 finding 3, I18N.15): the remaining tests that hardcode a language are listed in finding 6.

Findings:

1. Minor, lane-b (I18N, row proposed): `SCOPES` in `tools/i18n_catalog.py` lists the product page only, so the strings of the other customer-facing templates are in no catalog and an `it` page shows them in English. The new specifications and fitment includes of #676 ("Fits everything.", the fitment "more" count) are an example; the same holds for listings, the search results page, the ride overrides, the public event pages and the blog and CMS block templates. The PR #676 says so (English until the storefront I18N). `check` passes because the scope is the gate. New row I18N.20 (P1, 12 h, I18N.7) in spec 09: add the folders to `SCOPES`, translate, and a guard test for templates outside it. The row is a go-live item: I18N.7 asks for 100 % of interface strings.
2. Minor, lane-b (THM.7): the spec row lists "accordion" and the PR #676 found no FAQ data (no field on `Product`, `ProductTranslation` or `Variant`) and no review data; those two tabs are not built. Needs an owner or lane-a decision on where a product FAQ lives; no row until then.
3. Note, lane-b (CAT.29): images saved before #671 show the original file (with its own width and height) until their render job runs again; the CAT.4 follow-up that splits the render job by preset group and renders the hero first is #678 (open). THM.9 (ride blog templates) must use `{% responsive_image %}` with `article` and `avatar`, not `{% image %}`.
4. Note, lane-b (CAT.25, #686): the review list reads Wagtail's private `ReferenceIndex._extract_references_from_object` (Wagtail 8.1); a Wagtail upgrade can break it, and the integration tests would show that. The list shows 200 orphans and no pagination. The PR leaves two owner questions (the 24 h grace period; whether vendor-collection images are limited to platform staff); the screen follows the Wagtail image permission and the delete needs the `bulk_edit` session level. Lane-a review.
5. Note, lane-b (THM.14, #681): a bad `MPMV_VIDEO_EMBED_ORIGINS` fails `manage.py check` at startup (`cms.E003`), not silently; the default is unchanged, so no release action is needed. An empty value disables video embeds.
6. Note, lane-a (I18N.15 part 3): with `MPMV_LANGUAGES=it,de` several tests still hardcode English or French paths (`test_site_links` storefront header, `test_brand_pages` brand node, `test_language_switch` vendor storefront, `test_vendor_storefront` fallback, and 24 events integration failures); the `it,de` job on main is informational. Reported only.
7. Note, lane-a (CMP.5, #668): clause (m) is approved wording in the spec; the contract version that carries (l) and (m) with the new double signature is built by VEN.13 and VEN.14 (open). Legal text, no code in this delta.
8. Note, lane-a (OPS.15, OPS.16): spec only; the update workflow and the image scan are not built; nothing in CI changed.
9. Note, lane-b (I18N): `e7d447e5` re-saved the Italian catalog with the project tool after an unwrapped format on main; `test_language_catalogs.py` passes on it.

One gap in a lane-b area needs a row: finding 1 (I18N.20). No defect otherwise. Nothing found in lane-a code beyond the notes above (reported only, no edit).

Operator notes for the next STAGE release: no migration in this delta; new optional variable `MPMV_VIDEO_EMBED_ORIGINS` (comma separated plain https origins, default YouTube no-cookie and Vimeo player); new Wagtail admin screen `admin/images/unused/` (CAT.25); new image presets `article` and `avatar` are made when an image's render job next runs; the help files sync on migrate (`sync_help`); keep `MPMV_LANGUAGES=en,it` on LIVE; no CSP change other than the configurable video origins.

Human review needed: CAT.25 (#686: an audited delete of media, owner questions); the fitment summary on the public product page (#676: the active-node rule and the wording against spec 17.2.4); clause (m) wording (#668, counsel approved, legal); ARC.37 help texts (#661, #662, #675: each statement against the screen on STAGE; DE, FR and ES AI drafts).

Verdict: GO with follow-ups. No blocker.

### Coherence check 41 (main at f648e4d7)

Scope: release gate for v0.1.18, the train merges after check 40 (main at 4009ea06): #693 (#672), #696 (#685), #699 (#684, #677, #680, #678), #701 (#695, #694, #689, #687), #705 (#691), #707 (#700) and #709 (#704); 272 files and about 8 600 lines added outside the catalogs (help files, tests, templates). #362 is open and not in scope.

| Area | Result |
|---|---|
| Code vs spec | I18N.13 (#672): the vendor application templates, its three emails and `apps/vendors/forms.py` are in the catalog scopes, formal, with `test_the_vendor_application_flow_is_translated_and_formal`. CAT.11 (#685) meets its Done-when: staff edit the audited fields and publish, unpublish or retire in bulk with a dry-run preview (`test_staff_edit_audited_fields_with_dry_run_then_confirm`, `test_bulk_publish_previews_then_applies_with_audit`), and Finance and Support get 404 (`test_finance_and_support_cannot_change_a_product`); a full product edit form is not part of the row (CAT.19). EVT.14 (#684) meets its Done-when: the second request is served from the cache with a fake upstream, `z=19`, negative and non-integer values give 404 with no upstream call, the upstream is a setting and the attribution comes from the setting; the zoom range is 2 to 18 as in spec 15 (the PR body says 0, the setting says 2). ARC.37 part 7 (#677) empties the baseline (`# count: 0`); the customer help section (#689) exists in five languages under `/help-centre/` and is linked from the help dialog and the footer and header of both themes; the vendor guide pages and the review checklist per part against STAGE are not built, so ARC.37 stays partial (finding 1). THM.9 part 1 (#680): the content pages, error layout, axe, CSP, focus-not-hidden and reduced-motion e2e at the three widths; the consent banner and the video modal are part 2, so the task is partial and its Done-when is not met yet (declared so). CAT.4 follow-up (#678): one job per preset group, hero first, a lock per image and group, old single-argument jobs still render all. CMS.13 (#687) meets its Done-when: no token gives 400 and stores nothing, the page type is in the SEC.13 register without a manual step, the purge deletes only submissions past their page's retention (finding 3). SEO.15 (#691) meets its Done-when: a sole trader with a shop name emits it, one whose only name equals the legal name emits no seller and no error, `schema_check` rejects an `Organization` named like the person; the application form already asks for the shop name (`business_name`, stored as `display_name`). CAT.22 (#700) meets its Done-when for the checks (zero, lowercase and unknown country refused, also in the database and on an import dry run) and the AuditLog rows; the fields show in the table in both languages, but the labels are English in every language (finding 2). CAT.24 (#704) meets its Done-when: the original-size fallback while a rendition is missing, one alert after the retry limit and for a decode error, a second run renders nothing new (`test_a_second_run_and_a_re_save_do_not_duplicate_renditions`). SEC.5 (#695) retries `pip-audit` once on a network timeout in the `Makefile`; no ignore added. Migrations `catalog/0017_product_physical_origin` (after 0016) and `cms/0023_form_pages` (after 0022) are linear with no number collision; `wagtail.contrib.forms` brings Wagtail's own `wagtailforms` migrations; `makemigrations --check` reports no changes |
| Spec and backlog | `uv run tools/build_docs.py --check` is fresh after this PR's wording change: the I18N.20 row now also names the CMS.13 form page templates and the specification table labels of `apps/catalog/product_pages.py` (CAT.22). No new ID; no ID is reused in `backlog.csv` |
| Docs | `progress.md` had no task rows for CAT.11, EVT.14, THM.9, CMS.13, SEO.15, CAT.22 and CAT.24, stale ARC.37, CAT.4 and I18N.13 rows, and no PR rows for #672, #677, #678, #680, #684, #685, #687, #689, #691, #694, #695, #700 and #704: all added or fixed (THM.9 and ARC.37 partial). `docs/dev/maps.md` and `deploy/server/mpmv.env.example` (#684), `docs/dev/platform-roles.md` (#685), `docs/dev/public-forms.md`, `background-jobs.md` and `audit-trail.md` (#687, #678, #704) and `docs/dev/help-content.md` (#689) were updated by their PRs |
| Gates | Memory checked first (38 % free). `make check` pass at f648e4d7 (ruff, ruff format, mypy strict on 1673 source files, import contracts 13 kept and 0 broken, backlog freshness, `i18n_catalog check`, Django checks, `makemigrations --check`, theme quality gates). Targeted suites with `-n 0` and `MPMV_TEST_DB_NAME=test_mpmv_check41`: the new tests of the delta (`test_product_physical.py`, `test_product_status.py`, `test_rendition_monitor.py`, `test_translation_states.py`, `test_form_pages.py`, `test_help_guide.py`, `test_reserved_slugs.py`, `test_map_tiles.py` and its engine tests, `test_map_tile_settings.py`, `test_catalog_products.py`, `apps/seo/tests`, `test_help_centre_footer.py`, `test_product_physical_specs.py`, `tests/integration/test_ride_content.py`) plus `test_image_rendition_job.py`, `test_help_coverage.py` and `test_language_catalogs.py`: 635 passed in 141 s. `make test` was not run; CI on main is the gate: run 44250963685 on f648e4d7 is green, as are the train runs on 6aae9a1f, 71e8df51, ea8fc34f, e459c1ae, 6b31224c and 00caef52 (check 40's pending run 44224912652 on 4009ea06 finished green). Security sweep of the delta: no credential-like literal, no real vendor name, no hostname other than test domains (`example.*`, `.invalid`, `.test`, `localhost`) and the public defaults `tile.openstreetmap.org`, `www.youtube-nocookie.com` and `player.vimeo.com`; no inline style or script and no event handler attribute in a template; one new `\|safe` (`cms/help_guide_section.html`) renders help-file HTML already passed through `cms.engine.sanitise_rich_text`, not user input; `format_html` in `manager/product_views.py` escapes its arguments; no vendored file changed; `.gitleaksignore` unchanged; no `TODO` without an ID. Money, tax, payment, ledger and order code is not touched |

Check 40 open items:

1. Open (I18N.20): no part merged; the row now also covers the CMS.13 form page and the CAT.22 labels (findings 2 and 3).
2. Open (THM.7 FAQ and reviews tabs): unchanged, waits for an owner or lane-a decision on where a product FAQ lives.
3. Closed (CAT.29 follow-up): the render job split by preset group, hero first, landed (#678); THM.9 part 1 uses `{% responsive_image %}` with `article` and `avatar` (#680).
4. Note (CAT.25): unchanged; CAT.24 (#704) adds a rendition monitor beside the orphan review list, with the same Wagtail image permission.
5. Open (I18N.19, no LIVE system check): unchanged; the guard is the `MPMV_LANGUAGES` default `en,it`.
6. Note (I18N.15 part 3, CMP.5, OPS.15, OPS.16, CAT.28, ORD.26, ORD.29, TAX.12, ORD.20 3b-2, ORD.21): unchanged, lane-a.

Findings:

1. Minor, lane-b (ARC.37): the vendor guide pages are not built (only a vendor guide link flag exists) and no review checklist per part against the STAGE screens is recorded; both are Done-when items. The row stays partial; no new row. The #689 changes and withdrawal pages are customer-facing, legal-adjacent text: human review.
2. Minor, lane-b (CAT.22): the specification table labels (Length, Width, Height, Material, Colour, Country of origin) are built in `apps/catalog/product_pages.py`, outside the catalog scopes, so an `it` page shows them in English; the test checks the values in both languages under English keys. The country shows its ISO code. Folded into I18N.20 (row text extended here). The older `length_cm` fields coexist with the new mm fields; the legacy ingest loader does not map the new fields yet.
3. Minor, lane-b (CMS.13): no ride override of the form page and its three customer strings are not in the catalogs (I18N.20). Open owner questions: the submission retention (per-page default 90 days, at most 365, since D-29 and D-30 cover orders only) and who may read submissions (any staff user with Forms; no role is bound to `cms.FormPage`, which changes the CORE.8 matrix and is lane-a's). No e-mail notification on a submission. Personal data and an anonymous POST: human review.
4. Note, lane-a (SEO.15): the application form asks for the shop name but gives no hint that a sole trader should not reuse their own name; with that name the seller node is simply omitted. Owner question Q-103 (a trading name that contains the person's name is emitted) stays open. No row.
5. Note, lane-b (EVT.14): `MAP_TILE_URL` now defaults to the proxy, so every map picker loads tiles through the site, and the proxy calls OpenStreetMap's public tile server (light use only) until `MPMV_MAP_TILE_UPSTREAM_URL` names another provider; the daily cap (default 100 000) answers 503 past it. The cache sits in the `default` storage under `map-tiles/` (not served by Caddy) until ARC.15 object storage exists. A burst on a cold tile makes several upstream calls (no single-flight). The LIVE upstream is an open owner question.
6. Note, lane-a (CAT.11): the PR adds the role matrix row `platform.products` (Catalog edit, Owner read) and leaves two owner questions (a separate hidden state; the new row instead of `platform.catalog`, which gives Support read access). The bulk path allows retired back to draft or published, as the spec does. Lane-a review of the matrix change.
7. Note, lane-b (CAT.24): the new admin screen has no help file and no e2e (plain Wagtail listing markup); failed decode jobs stay in the queue table as failed and are hidden once their group has its renditions.
8. Note, lane-a (#689): the slug `help-centre` is reserved under each language home (`cms.E004` flags an existing page); an installation that already had such a page fails `manage.py check` at deploy. Not checked on STAGE here; the release run of `manage.py check` shows it.

No Major. Two gaps in lane-b areas are folded into the existing I18N.20 row (findings 2 and 3); no new row. Nothing found in lane-a code beyond the notes above (reported only, no edit).

Operator notes for the v0.1.18 release:

- Migrations: `catalog/0017_product_physical_origin` (new optional product columns, three check constraints on the sizes, two translation columns), `cms/0023_form_pages` and Wagtail's `wagtailforms` migrations (new app `wagtail.contrib.forms`); run `migrate` before the new code serves traffic.
- New optional variables, all with defaults: `MPMV_MAP_TILE_UPSTREAM_URL` (https template with `{z}`, `{x}`, `{y}`; empty means OpenStreetMap's public tile server; a bad value stops the settings from loading), `MPMV_MAP_TILE_USER_AGENT` (empty means the product, the site URL and the geocoding contact), `MPMV_MAP_TILE_UPSTREAM_DAILY_LIMIT` (default 100000). `MPMV_MAP_TILE_URL` keeps its meaning, but empty now means the site's own proxy instead of no tile layer. `MPMV_VIDEO_EMBED_ORIGINS` is from the previous release (#681) and unchanged. `FORM_PAGE_RATE_LIMIT` (10 per IP per 15 minutes) and `FORM_PAGE_MAX_SUBMISSION_BYTES` (32 KiB) and the other `MAP_TILE_*` values (zoom 2 to 18, 600 tiles per IP per minute, 7-day browser cache, `default` cache storage) are settings, not environment variables.
- New nightly job `cms.purge_form_submissions` (cron `33 4 * * *`), registered by the worker; render jobs are now queued per preset group (`core.render_image:<pk>:<group>`), older queued jobs still render everything.
- New public routes `/maps/tiles/{z}/{x}/{y}.png` and `/help-centre/` (per language); the footer and header Help link points to `/help-centre/` when `MPMV_HELP_URL` is empty. New staff screens: Manager platform catalog (`/manager/platform/catalog/products/`) and Images > Image renditions.
- Keep `MPMV_LANGUAGES=en,it` on LIVE. No CSP change (tiles are same-origin).

Human review needed: CMS.13 (#687: personal-data retention and an anonymous public POST); the customer help centre (#689: changes and withdrawal texts against spec 07 and the approved legal texts); CAT.11 (#685: role matrix and an audited bulk service); EVT.14 (#684: the outbound fetch, the cache and the OSM usage policy); ARC.37 part 7 (#677: sensitive staff screens described from the templates). DE, FR and ES texts are AI drafts behind I18N.19.

Verdict: GO with follow-ups. No blocker.

### Coherence check 42 (main at d1aa151b)

Scope: everything merged on `origin/main` since check 41 (f648e4d7): 53 merge commits (13 of them first-parent train merges, counted with `git log --merges f648e4d7..d1aa151b`), 207 files, about 19 800 lines added. Task PRs: #697 (CAT.25), #698 (I18N.13), #702 (EVT.15), #706 (THM.9 part 2), #708 (CMS.17), #710, #718 and #731 (EVT.14), #711 (CAT.24), #714 (CAT.23), #719 (CMS.15), #722 (THM.10), #723 (ORD.30), #724 (CMS.16), #733 (CAT.26), #737 (CORE.18), #740 (ORD.29); spec and docs PRs #712, #717 and #736. ING.2 and VEN.9 are not merged yet.

| Area | Result |
|---|---|
| Code vs spec | ORD.29 (#740) is done except the settlement regression test (follow-up, finding 2): the `SHIPPING_VAT_SPLIT` block is gone from `apps/`; `shipping_tax_rate` and `shipping_tax_reason` are nullable and the DB check is `rate IS NULL OR amount = round(net × rate / 100, 2)` (`orders/0021`, additive, no backfill); the portions live in `shipping_choice["tax_portions"]` and the v1 snapshot; the pure `engine.check_shipping_portions` has unit tests (`test_shipping_portions.py`) and `test_split_shipping_vat.py` places the 50.00 at 22 % plus 50.00 at 4 % package; no other reader of `shipping_tax_rate` outside `versions.py`, `services.py` and the model, so no reader can take a NULL rate for zero. ORD.30 (#723) meets its Done-when: one predicate `staff_actionable` serves the list and `_lock_amendable` (the actions), with the state combinations tested against the SQL filter; constant queries with blocked rows; matrix scoping through the platform section check. CAT.26 (#733): pure `engine.variant_gallery` under the D-18 rule, a variant without own images keeps the product gallery, a language-bound cover of another language never shows; unit, integration (base and ride) and e2e `test_variant_galleries.py`. CORE.18 (#737): approving a vendor claim in the admin needs the `platform.vendor_approval` matrix section before the review page and the decision, tests per role and no audit row on refusal. THM.9 part 2 (#706) proves the carousels and the video modal that shipped with THM.14 and adds the ride consent banner (same ids, hooks and texts, Reject and Accept equal weight): the task is done. CMS.15 to CMS.17: the download block is served as an attachment with `nosniff` (`test_download_block.py`), the accordion is native `details`, the footer is edited in the CMS with a fragment cache dropped on edit (`test_footer_settings.py`); migrations `cms/0024` to `0026` and `catalog/0018` are linear. EVT.14 and EVT.15: single-flight by advisory lock, HEAD on a tile, unlock failure closes the connection, the flaky timing assertion is replaced by a barrier. CAT.24 and CAT.25 follow-ups: query limit, one alert key for decode errors, retry cap of 20 per minute, pagination of the orphan list. I18N.13 (#698): the register check covers the Manager and vendor templates; 35 Italian strings moved to the formal form. Spec spot-checks (spec 01): no `float(` in the new app code; no `apps.orders` import in `apps/tax` or `apps/shipping`; money stays `Decimal` and the half-up rule is applied in `engine`; new mutating services take `dry_run` and write `AuditLog` (the project guard tests in `tests/unit` and the CI integration run cover this); the country and tax position still come from `current_operator()` |
| Spec and backlog | `uv run tools/build_docs.py --check` is fresh and `make docs` gives no diff. New rows from #717 (ORD.30 to ORD.32) each have a Done-when and estimate; no ID is reused |
| Docs | `progress.md` had no task rows for ORD.29, ORD.30, ORD.31 and ORD.32 (spec only), CAT.26, and stale rows for THM.9 (part 2 done), CORE.18, EVT.14, CAT.24, CAT.25 and I18N.13; no merge-list rows for #697, #698, #706, #710, #711, #718, #723, #731, #733, #736, #737, #740 and the check 41 and spec PRs (#712, #717): all added or fixed. `docs/dev/release.md` (two places) and `docs/dev/parallel-lanes.md` still named the full lane board #86: now #726; `tools/security_report.py` (`LANE_BOARD`) and its docstring too, with the test needle. `docs/dev/background-jobs.md` still lists the `catalog.render_product_image` alias with its removal TODO (dated 2026-11-17); the alias and the doc line stay together until the removal PR (finding 3). `docs/dev/maps.md` (#710, #718) and `docs/dev/themes.md` (#706) were updated by their PRs |
| Gates | `make check` pass at d1aa151b (ruff, ruff format 1961 files, mypy strict on 1702 source files with the cache cleared, import contracts 13 kept and 0 broken, backlog freshness, `i18n_catalog check`, Django checks, `makemigrations --check` no changes, theme quality gates base and ride). `make test-unit`: 8340 tests: 8339 passed and 1 failed in 200 s; the failure (`test_release_doc_has_the_security_schedule`) was caused by this PR's own #86 to #726 edit and is fixed with the matching change in `tools/security_report.py` (finding 1); `tests/unit/test_security_schedules.py` then passes (31). `make test` and `make test-e2e` were not run locally (7.5 GB shared machine); CI on main is the gate: run 44318257353 on d968bf6e is green, as are the train runs 44306691503 (f1cd268d), 44299981021 (b7278813), 44296666871 (c3173d79), 44289630179 (bce69bad), 44286034184 (2dce3c5e) and 44281295715 (eed7d180); the run on d1aa151b (44324319178, the #737 merge) was still in progress when this check was written (it differs from d968bf6e by #737 only). Security sweep of the delta: no credential-like literal (the userinfo URLs in the URL-sanitising tests use `example.org` and `x.example`), no real vendor name, no hostname other than test domains (`example.*`, `.invalid`, `.test`) and the public defaults `www.openstreetmap.org`, `www.google.com` (map directions link, EVT.15), `www.youtube.com` and `www.youtube-nocookie.com`; no inline style, script or event handler attribute in a template; no vendored file changed; no `TODO` without an ID. Tax, orders and ledger code changed in ORD.29 and ORD.30 (human review flagged by their PRs) |

Check 41 open items:

1. Open (I18N.20): unchanged; the new strings of CAT.26 and ORD.30 went into the catalogs with their PRs.
2. Open (THM.7 FAQ and reviews tabs): unchanged.
3. Open (ARC.37 vendor guide pages and per-part review checklist): unchanged.
4. Closed (EVT.14 single-flight): #710 serialises a cold tile with a PostgreSQL advisory lock; #718 documents that it does not work through a transaction-mode pooler.
5. Closed (CAT.24 notes): #711 limits the failed-job query, aggregates decode alerts and rate-limits retries.
6. Note (CMP.5, OPS.15, OPS.16, CAT.28, ORD.26, TAX.12, ORD.20 3b-2, ORD.21): unchanged, lane-a.

Findings:

1. Fixed here, lane-b (lane board number): the security report job still commented on #86, which is full; `LANE_BOARD` is now `726` in `tools/security_report.py`, with the docs and the test updated. A workflow run opened after merge will post there.
2. Major, lane-a (ORD.29, #740): the Done-when item "PDF, email and settlement show the summed shipping gross" has no settlement test on main. The docstring of `apps/orders/tests/integration/test_split_shipping_vat.py::test_pdf_and_email_show_the_summed_shipping_gross` points to `apps/payments/tests/integration/test_split_shipping_settlement.py`, which does not exist. Lane-b does not touch payments code: lane-a follow-up on ORD.29, add the split-package settlement regression test and fix the dangling docstring.
3. Minor, lane-b (overdue TODO): `catalog.render_product_image` is kept as an old task name "after one release" with `TODO 2026-11-17` in `docs/dev/background-jobs.md`; the removal PR #744 (CAT.24) is open and not merged. No new row.
4. Minor, lane-b (ORD.29): the order confirmation "VAT per rate" (ORD.20) and the CMP.23 connector must read `shipping_choice["tax_portions"]`, never `VendorOrder.shipping_tax_rate` (NULL when split); today no reader does, and the spec text says so; a new reader needs a test with a split package. No new row.
5. Note, lane-a (ORD.29): a split package stores no rate, reason or nature code on the vendor order, so any report that groups shipping VAT by `shipping_tax_rate` (OSS, DAC7) must read the portions; human review of the tax and ledger paths is flagged on #740.
6. Note, lane-b (EVT.14): the advisory-lock single-flight needs a session-level PostgreSQL connection; behind a transaction-mode pooler it does not hold (documented in `docs/dev/maps.md`). Check at deploy time.
7. Note, lane-a (ORD.31, ORD.32): spec only (owner decision 2026-11-18); ORD.32 touches orders, payments and consumer law and is P2, 8 h; no code.
8. Note, lane-b (CAT.26): the variant gallery swap needs JavaScript; without it the page shows the product gallery then each variant gallery under its heading.

One Major (finding 2, lane-a follow-up, a missing regression test rather than a defect). Nothing else found in lane-a code beyond the notes above (reported only, no edit).

Verdict: GO with follow-up (finding 2).

### Coherence check 43 (main at b80f2bf3)

Scope: everything merged on `origin/main` since check 42 (d1aa151b): 110 merge commits (`git log --oneline --merges d1aa151b..origin/main`; 18 first-parent train merges, counted with `git log --first-parent --oneline d1aa151b..origin/main`, carrying 47 task and spec PRs), 417 files, about 33 200 lines added (`git diff --shortstat d1aa151b origin/main`). The rules of #787 (backlog pass first, then the check with a stale-items sweep) are not on main yet; this check follows them as announced on #726. Not merged at the time of writing: #819 (train with #810 and #817), #820 (THM.16), #775 (ORD.32), #799, #787, #771.

| Area | Result |
|---|---|
| Code vs spec | Done-when spot-checked against tests for CMS.14 (unpublished snippet renders nothing, page-language translation), CMS.18 (engine, service, nightly job: 12 months plus last 10, dry run, protected revisions), CAT.30 (Support 403 on every write route, Finance 404), SEO.10 (producer, reseller and unknown snapshots, CAT.20 manufacturer wins), SEO.11, SEO.13, SEO.14 (`test_return_terms_match_page.py`), SEO.15 (sole trader cases and the schema check), VEN.40 (13 days refused, 14 accepted, default applies), TRU.1 (a new rules version changes a result, the old snapshot keeps its version), FIT.3 (50-product bulk, the 21st proposal refused, cross-tenant 404), FIT.18 (gate on and off, no catalog import), I18N.20 (scope guard), EVT.10, CAT.10, CAT.15, CAT.28; and for lane-a ORD.26 (stored message, scrub with `dry_run`, idempotent, restricted order encrypted), ORD.29 (#759 settlement test), ORD.31 (skipped items with reason, cap shown, merge not replace, 404 for another customer), VEN.9 (owner-only export behind a fresh passkey), VEN.36 (old version PDF, bulky flag), ING.2, ING.4, THM.18 (placeholder under base and ride). One Done-when item is unmet: SEO.12 handling time from the vendor dispatch settings and closures (no `handlingTime` anywhere in `apps/`; finding 1); the row says partial. CAT.28 proves "bounded" by a constant query count, not a row cap (finding 6). Spec 01 spot-checks: the only `float(` in the new app code is the GeoCoordinates conversion of the event JSON-LD (not money); no `apps.orders` import in `apps/tax` or `apps/shipping`; the new mutating services (`add_trust_rules`, `record_source_state`, the retention service, the catalog upserts, `scrub_order_emails`) take `dry_run` and the audit guard tests pass in CI; country fallbacks in the SEO graph use `current_operator()`; `uv run lint-imports`: 14 kept, 0 broken (`apps.trust` contracts added with TRU.1). Migrations added are linear (`catalog/0019`, `cms/0027`, `core/0032`, `fitment/0003` and `0004`, `ingest/0003` and `0004`, `shipping/0015`, `trust/0001`, `vendors/0035` and `0036`; `makemigrations --check` clean) |
| Spec and backlog | `make docs` gives no diff and `make check` backlog freshness passes. New spec rows from lane-a (#782 orders API entitlement, #758, #768) have a Done-when and no ID is reused. ORD.32 (#775) and the SEO.14 shop-pays spec change (#799) are still open |
| Docs | `progress.md` had no merge-list row for any of the 47 PRs merged since check 42 (#727 to #808) and no task row for ORD.26, ORD.31, VEN.9, VEN.36, ING.2, ING.4, THM.18, CMS.14, CMS.18, CAT.30, CAT.10, CAT.15, CAT.28, SEO.11 to SEO.14, VEN.40, TRU.1, FIT.3, FIT.18, I18N.20 and EVT.10, and stale rows for ORD.29, SEO.10, SEO.15, CAT.24 and ARC.37: all added or fixed here. `docs/dev/background-jobs.md` no longer lists the removed `catalog.render_product_image` alias (#744). `docs/dev/audit-trail.md`, `platform-roles.md`, `release.md`, `help-content.md`, `transactional-email.md` and `parallel-lanes.md` were updated by their task PRs; no reference to the removed `SHIPPING_VAT_SPLIT` block remains |
| Gates | `make check` pass at b80f2bf3 (ruff, ruff format, mypy strict on 1786 source files with the cache cleared, import contracts 14 kept and 0 broken, backlog freshness, roles doc, `i18n_catalog check`, Django checks, `makemigrations --check` no changes, theme quality gates base and ride). `make test-unit`: 8687 passed and 8 skipped in 123 s. `make test` and `make test-e2e` were not run locally (7.5 GB shared machine); CI on main is the gate: run 44476735929 (CI) on b80f2bf3 is green, as are 44470868926 (dd3d6220), 44464554057 (cd3f8dc7), 44456063146 (681d669b) and 44449037847 (2664fe0d). Security sweep of the delta (`git diff d1aa151b origin/main -- apps themes tools`, 23 900 added lines): no credential-like literal, no real vendor name or legacy order number (checked against the private pseudonym map), no hostname other than test domains (`example.*`, `.invalid`) and public schema or standard hosts (`schema.org`, `www.w3.org`, `localhost`), no inline style, script or event handler in a template, no vendored file changed, one `TODO` and it has an ID (`TODO(CMP.4)` in `apps/vendors/engine.py`). Tax, orders, ledger and privacy code changed in ORD.26, ORD.31 and VEN.9 (human review flagged by their PRs) |

Check 42 open items:

1. Closed (ORD.29 settlement test): #759 adds `apps/payments/tests/integration/test_split_shipping_settlement.py`.
2. Closed (`catalog.render_product_image` TODO): #744 removed the alias and its doc line; the LIVE job-queue check stays on #757 (lane-a).
3. Open (EVT.14 pooler note, CAT.26 JavaScript note, ORD.29 readers of `tax_portions`): unchanged, notes.
4. Open (ORD.32): spec only, #775 in review.

Findings:

1. Major, lane-b (SEO.12): the Done-when item "a vendor closure extends the stated handling time in the JSON-LD" is not met: only the service name and extra weight bands shipped (#802), and no `handlingTime` exists in `apps/`. The progress row says partial and the spec row stays open. Follow-up for the remaining half (needs the vendor dispatch settings and VEN.20 closures); no new row.
2. Minor, lane-b (process): none of the 47 PRs had a progress row; the train workflow does not add them, so each coherence check back-fills. This check back-filled them all.
3. Minor, lane-b (CAT.28): the Done-when says "reads a bounded number of rows"; the test proves a constant query count (`test_search_events_query_count_does_not_grow_with_the_catalogue`), not a row cap. Accepted; no new row.
4. Note, lane-b (stale branches): about 44 merged `lane-a/*` and `lane-b/*` remote branches, 14 older unmerged `lane-b/*` branches and 23 local worktrees remain; they are listed in the PR body, not deleted (the owner decides).
5. Note, lane-a (#757): ORD.31 N+1 in `add_to_cart`, ING.3 bulk reads, VEN.9 pending and available split, LIVE job-queue check, VEN.36 minors; none older than a week.
6. Note, lane-b (#757): CAT.15 consumer-price order and variant matching, CMS.14 cache key with `snippet_cache.generation()` (SEO.6), EVT.10 `offers`, SEO.15 database-side vendor group, VEN.40 matrix action and immutability trigger: all listed with an owner.

One Major (finding 1, lane-b follow-up). Nothing found in lane-a code beyond the notes above (reported only, no edit).

Verdict: GO with follow-up (finding 1).

### Coherence check 44 (main at d41c4547)

Scope: everything merged on `origin/main` since check 43 (#821, merge commit 6b559727): 17 merge commits (`git log --oneline --merges 6b559727..origin/main`; 5 train merges #822, #826, #829, #831 and #833, carrying 10 PRs: #820, #799, #771, #787, #823, #824, #825, #828, #830, #827), 85 files, about 3 000 lines added (`git diff --shortstat 6b559727 origin/main`). Not merged at the time of writing: #832 (lane-b, re-review pending after fixes), #834 (lane-b draft, waits for the lane-a choice on the API default language in `apps/api/v1/catalog.py`), #835 (labelled), #775 (ORD.32, lane-a, cross-review approved, CI red on guards lane-a is fixing).

| Area | Result |
|---|---|
| Code vs spec | Done-when spot-checked against tests: CMS.14 (`test_snippet_publish_guard.py`, fail closed on every publish path), ARC.37 help labels (`test_help_labels.py`, `tests/help_labels.py`), CAT.15 ordering by the consumer price and variant identifiers (`test_cat15_other_sellers_order.py`, `test_other_sellers.py`), FIT.3 node picker search (`test_fitment_picker.py`, e2e `test_fitment_picker.py`), I18N.20 3b slice 1 (`test_i18n_consumer_law_pages.py`, `test_language_catalogs.py`), THM.16 grid fix (`test_ride_bootstrap_grid_wins.py`, ride baselines at 360, 768 and 1280 px), I18N.10 part 1 (`test_nfc_boundaries.py`). Unmet by design: I18N.10 Done-when needs the importers and the removal of the F3 xfail in `test_hostile_input_outputs.py`, so it stays partial (finding 2); SEO.12 `handlingTime` from check 43 is still open. Spec 01 spot-checks: no new `float(` in money code, no `apps.orders` import in `apps/tax` or `apps/shipping`, `uv run lint-imports` 14 kept and 0 broken, no new migration since check 43 |
| Spec and backlog | `make docs` gives no diff. #799 (SEO.11 and SEO.14 follow shop-pays), #771 (new row ARC.38, Done-when present, ID not reused) and #787 (stale-items sweep) are spec or docs only and consistent with the tables |
| Docs | `progress.md` had no merge-list row for the 10 PRs above and #821, no row for I18N.10, and stale rows for THM.16, CMS.14, CAT.15, FIT.3, I18N.20 and ARC.37: all added or fixed here. `docs/dev/help-content.md`, `release.md`, `parallel-lanes.md` and `dsa-p2b.md` were updated by their PRs. Help files changed for the fitment and statements routes in it, de, fr and es with the picker PR; the new picker JSON route is covered by `tests/help_routes.py` NOT_PAGES and `apps/manager/site_map.py` (the help sweep and site map tests pass in CI) |
| i18n | `i18n_catalog check` passes in `make check`; the `.po` and `.mo` files of de, es, fr and it moved together with #825, #827 and #828 |
| Stale-items sweep | (a) No row marked done with an unmet Done-when other than the open items above; no `TODO`, `FIXME` or `XXX` without an ID in `apps`, `themes`, `tools` or `tests` (the `XXXXXXX` hits are the SDI foreign recipient code and BIC samples); #757 was last updated 2026-11-19 and has no item older than a week, but four items are done and not ticked (finding 1). (b) The PRs since check 43 add no template, static file, fixture, script or setting that nothing references; no doc describes removed behaviour. (c) Remote branches are listed in the PR body, nothing deleted |
| Gates | See the PR body for the exact results: `make check` and `make test` run at the head of this branch under the shared test lock |

Check 43 open items:

1. Open (SEO.12 `handlingTime`): unchanged, lane-b follow-up needing VEN.20 closures.
2. Closed (progress back-fill): done again in this check for the 10 PRs above.
3. Open (EVT.14 pooler note, CAT.26 JavaScript note, ORD.29 readers of `tax_portions`): unchanged, notes.
4. Open (ORD.32): #775 in review.

Findings:

1. Minor, lane-a (#757 housekeeping): CMS.14 (#804 and #823), the FIT.3 picker (#796, #827), the CAT.15 ordering (#828) and the ORD.29 settlement test (#759) are done and still unticked on #757. The owner of the list ticks them.
2. Minor, lane-b (I18N.10): part 2 (importers and the removal of the F3 strict xfail) is not started; the row stays partial and the xfail reminder stays red-on-fix by design.
3. Note, lane-b (SEO.12): `handlingTime` still missing (check 43 finding 1).
4. Note, lane-b (stale branches): 118 merged remote branches and 29 unmerged ones, the oldest from 2026-11-07, are listed in the PR body, not deleted (the owner decides).

No Major finding. Nothing found in lane-a code (reported only, no edit).

Verdict: GO.

### Coherence check 45 (main at 9010f52b)

Scope: everything merged on `origin/main` since check 44 (#838, merge commit 062d9a8f): 14 merge commits (`git log --merges 062d9a8f..origin/main`; train merges carrying #835, #832, #837, #839, #834, #841, #775 and #847), 78 files, about 5 400 lines added (`git diff --shortstat 062d9a8f origin/main`). New migrations: `core/0033` and `orders/0022` (ORD.32). Not merged at the time of writing: #846 (I18N.20, labelled, queued), #848 (VEN.40 2/3, trigger) and #850 (SEC.5 2a) waiting for lane-a's review, #851 (VEN.40 3/3, draft).

| Area | Result |
|---|---|
| Code vs spec | ORD.32: every Done-when item has a named test in `test_staff_drafts.py` (today's prices and skipped lines, no price change, one-time link for that customer only, GET changes nothing, expired and guessed links refused, only the order's own email, fourth send refused, no passkey window refused, unpaid draft makes no order, reservation or ledger line, Catalog or Finance cannot draft, audit with role and note) and `test_staff_draft_checkout.py` (one normal order through the link). VEN.40 1/3 (#847): `platform.return_policy` in the matrix and `review_return_policy` gated by it, covered by `test_roles.py` and `test_return_policy.py`; the row stays open for 2/3 and 3/3. CMP.41: `test_trader_contact.py`, `test_withdrawal_views.py`, `test_checkout_legal.py`, `test_notifications.py`, strings in it, de, fr and es. I18N.20: #832, #839 and #841 move the catalogs, `test_withdrawal_form_pdf.py` covers the PDF in five languages; the row stays partial (rest of 3b, 3c). I18N.15 (#834): the API default language follows the primary language (`test_v1_catalog.py`). SEO.15 (#835): `test_search_vendor_group.py`. Spec 01 spot-checks: no new `float(` in money code, no `apps.orders` import in `apps/tax` or `apps/shipping`, `lint-imports` clean in `make check` |
| Spec and backlog | `make docs` gives no diff; `docs/spec/90-backlog.md` and `backlog.csv` changed only through #775 and #837 (`make docs`). No row marked done with an unmet Done-when other than the open items below |
| Registries | `tests/help_routes.py` NOT_PAGES has the three ORD.32 POST routes; `apps/manager/site_map.py` and the platform map list the draft pages (`test_platform_map.py`); `help/{en,it,de,fr,es}/route/manager__platform__order_draft(s).md` exist; the tenancy registry has `orders.StaffDraftLine`, the draft Manager routes (StaffOnly), `staff_draft_link`, `staff_draft_link_continue` and `withdrawal_form_pdf`; the security registry has the same public routes with their credential; the GDPR map (`test_gdpr.py`) was updated by #775 and the audit trail lists `order_draft_send`; the form PDF carries no personal data |
| i18n | `i18n_catalog check` passes in `make check`; the `.po` and `.mo` of de, es, fr and it moved together with #832, #837, #839 and #841 |
| Stale-items sweep | (a) No `TODO`, `FIXME` or `XXX` added since check 44. #757 was updated 2026-11-19 and has no item older than a week; the SEO.15 search filter item is now done by #835 (finding 3). (b) New files since check 44 are all referenced: `apps/orders/staff_drafts.py`, `draft_link_urls.py`, the draft templates and emails (urls and services), `apps/compliance/documents.py` (the PDF view). No dead code found. (c) Lane leftovers are listed in the PR body, nothing deleted |
| Gates | `make check` passes at the head of this branch under the shared test lock. The full local `make test` was skipped on this 7.5 GB machine: CI is the gate. Guard and registry tests: 6233 passed (unit, tenant isolation, endpoint security) and 118 passed (help coverage and sweeps, platform map, GDPR) |

Check 44 open items:

1. Open (SEO.12 `handlingTime`): unchanged, lane-b follow-up needing VEN.20 closures.
2. Open (I18N.10 part 2): not started.
3. Open (#757 housekeeping): ticking done items is for the list owner.

Findings:

1. Major, lane-a (CORE.18): after #847 the section check does not honour a platform owner granted directly by `bootstrap_platform_owner`. `may_access_section` (`apps/manager/access.py:48`, reached through `settings.PLATFORM_SECTION_CHECK`, `mpmv/settings/base.py:199`) reads `user_roles` (`apps/manager/role_groups.py:131`), which looks only at role group membership. `is_platform_owner` (`apps/core/services.py:277`) accepts the direct permission, and `staff_roles._holds_owner_directly` (`apps/manager/staff_roles.py:89`) knows the case, but the check does not. A STAGE account whose only grant is the direct owner permission therefore cannot approve return policies (`can_approve_return_policy`, `apps/vendors/services.py:2096`) and has the same gap for vendor approval (`may_approve_claims`, `:2081`, since #737). Lane-a is fixing it under CORE.18; v0.1.21 waits for that fix. Not fixed here (lane-a code, docs and tests only)
2. Minor, lane-b (I18N.10): part 2 (importers and the F3 xfail removal) is still not started.
3. Minor, lane-a (#757 housekeeping): ORD.32 (#775), CMP.41 (#837), the SEO.15 search filter (#835) and the API language default (#834) are done and may need ticking.
4. Note, lane-b (SEO.12): `handlingTime` still missing.
5. Note, lane leftovers: listed in the PR body, not deleted (the owner decides).

Verdict: NO-GO for the tag until finding 1 is fixed by lane-a and the check is repeated or lane-a accepts the fix as the only change after it (release.md rule 3).

### Coherence check 46 (main at 65c7efee)

Scope: everything merged on `origin/main` since check 45 (main at 9010f52b): 19 merge commits (`git log --merges 9010f52b..origin/main`; train merges carrying #846, #850, #852, #853, #854, #856, #858, #851, #848, #860, #861, #864 and #863), 52 files, about 3 300 lines added (`git diff --shortstat 9010f52b origin/main`). One new migration: `vendors/0037` (VEN.40 2/3). No new model, route, page type or audit action. Backlog pass (compiled by the main session): queued with labels #857, #865, #867, #868, #871; approved and waiting for labels #869, #870, #872, #874; #875 has changes requested (fixes running); #877 is a lane-a train. Owner questions pending for the morning: the vendor-recruitment register (#868), the notice-email register (#872) and the EVT.10 offer, postponed and currency questions (#874). The owner deletion list is unchanged.

| Area | Result |
|---|---|
| Code vs spec | VEN.40 follow-up (3 of 3): the dedicated matrix action (#847, check 45), the trigger (#848: `test_return_policy_immutable.py` refuses a raw update per column and a delete, allows the reviewer deletion, service approve and a newer proposal still work, the migration reverses and reapplies) and the passkey step-up (#851: `test_return_policy_admin_level.py`, no window refused for approve and reject, window accepted, finance refused with a window). CORE.18 and CORE.23 (#852): `test_direct_owner_sections.py` (a bootstrapped owner with no role group approves a claim and a return policy, a superuser without the permission is refused); the CORE.23 Done-when in spec 02 gained that clause. I18N.20 (#846): `test_language_catalogs.py`. SEC.5 item 10 part 2 (2a to 2d) and part 3a: scenario suites under `tests/security`, with the fixes listed on the task rows. ING.3 (#861): `test_source_state_queries.py`, constant query count. ING.4 (#863): `test_catalog_loader.py`. ING.3 itself (the HTML and Store API adapters) is not built, so its Done-when is open, and the progress row now says so |
| Spec and backlog | `make docs` gives no diff. `docs/spec/backlog.csv` and `90-backlog.md` changed only through the spec rows of CORE.23 (#852) and VEN.40 (#851). Progress rows back-filled in this PR: SEC.5 (item 10 parts 2 and 3a, new rows), VEN.40, CORE.18, CORE.23, I18N.20, ING.3 (new row), ING.4, and 13 merge-list rows |
| Registries | No new Manager route, page, model or audit action, so `tests/help_routes.py` NOT_PAGES, `apps/manager/site_map.py`, `tests/tenancy_registry.py`, `tests/security_registry.py`, the GDPR map and `docs/dev/audit-trail.md` needed no change; their guard tests pass (see Gates). The trigger of `vendors/0037` keeps `reviewed_by` erasable (`ON DELETE SET NULL`), consistent with the GDPR decision of VEN.40. `return_policy_review` is in `DEFAULT_SENSITIVE_ACTIONS`, in the `WIRED` registry of `test_session_levels.py` and in the VEN.40 spec row; no help text mentions it (finding 2) |
| i18n | `i18n_catalog check` passes in `make check`; the `.po` and `.mo` of de, es, fr and it moved together with #846; no template was added, so `SCOPES` and `EXCLUDED` are unchanged. Finding 3 covers the events tests under `MPMV_LANGUAGES=it,de` |
| Stale-items sweep | (a) No `TODO`, `FIXME` or `XXX` added since check 45. No row is marked done with an unmet Done-when (ING.3 is now `partial`; SEC.5 stays `partial`). #757 was updated 2026-11-20 01:20 UTC and has no item older than a week; ING.3 bulk reads (#861), ING.4 CAT.22 fields (#863), the CORE.18 vendor-approval item (lane-a, `approve_claim` already refuses a user without the section, #765) and VEN.40 (#847, #848, #851) are done or need ticking by the list owner. (b) New files are all referenced: `apps/core/hidden_text.py` (llms, ICS, RSS), `tests/security/leak_urls.py` (`test_information_leaks.py`), `redos.py` (`test_redos.py`), the new test files and `vendors/0037`. No dead code found. (c) Lane leftovers are listed in the PR body, nothing deleted |
| Gates | `make check` passes at the head of this branch under the shared test lock. The full local `make test` was skipped on this 7.5 GB machine: CI is the gate. Guard and registry tests: 1160 passed (`tests/unit`, including the platform map and GDPR map) and 5111 passed (tenant isolation, endpoint security, help coverage and the admin help sweep); `make docs` gives no diff |

Check 45 open items:

1. Closed (CORE.18, check 45 finding 1): #852 makes a directly granted platform owner hold the owner role in every section check.
2. Open (SEO.12 `handlingTime`): unchanged, needs VEN.20 closures.
3. Open (I18N.10 part 2): not started.
4. Open (#757 housekeeping): ticking done items is for the list owner.

Findings:

1. Major, lane-b and lane-a (SEC.5): the security-nightly ReDoS slow sweep (`tests/security/test_redos.py`, marked `security_slow`, the 100 KB sweep from #853) is red. It is split between the lanes: lane-b fixes are in progress; lane-a owns the compliance email, core observability and the Wagtail URL patterns. Nothing blocks CI on pull requests (the fast sweep passes), but the nightly stays red until both lanes land their fixes.
2. Minor, lane-b (VEN.40): staff are told that approving or rejecting a return policy needs the passkey window only through the prompt itself and the spec row; no help text (`help/*/route/admin__index.md` or the vendor returns guide) says so. Docs follow-up.
3. Minor, lane-b (EVT): three tests in `apps/events/tests/integration/test_templates.py` fail under `MPMV_LANGUAGES=it,de` because they assert English text. They are not a CI job, so nothing is red; they should compare translated text or force `en`.
4. Minor, lane-a (#757 housekeeping): the CORE.18 item "move the vendor-approval section check into `services.approve_claim`" is done (`approve_claim` raises `PermissionDenied` without the section, #765), and ING.3 (#861), ING.4 (#863) and VEN.40 (#847, #848, #851) are done: tick them.
5. Note, lane-b (SEC.5): 3a-2 (lane-a areas) and 3b (integrations) are not written; the API arrays without `maxItems`, the free-form event `body` schema and the HTML 405 on API v1 are `xfail(strict)` and wait for decisions.
6. Note, lane-b (SEO.12): `handlingTime` still missing.
7. Note, lane leftovers: listed in the PR body, not deleted (the owner decides).

Verdict: NO-GO until lane-a decides on finding 1 (release.md rule 3); no other Major finding.

### Coherence check 47 (main at 29631960)

Scope: everything merged on `origin/main` since check 46 (#880, merge commit 87580ee7): 22 task PRs (#870, #878, #883, #868, #888, #872, #889, #893, #892, #890, #895, #900, #874, #901, #896, #899, #902, #904, #905, #898, #910, #913) in 12 lane-a trains, 118 files, about 7 600 lines added (`git diff --shortstat 87580ee7 origin/main`). Five new migrations: `cms/0028`, `seo/0003` (SEO.8 per-page image), `events/0006` (EVT.10 offers and postponed), `fitment/0005` and `0006` (FIT.5). One new model, `GarageProfile`; one new route, `/account/garage/`; one new nightly job; new audit actions `fitment.garage_profile.*` and `fitment.garage.merged`. Open pull requests at the time: #875 (EVT.10 2/2, ready to merge), #914 and #917 (SEO.7), #915, #918, #919 (FIT.7 1/n), #920, #912, #922, #923, #924.

| Area | Result |
|---|---|
| Code vs spec | FIT.5 (3 of 3, #896, #898, #905): the Done-when is met (profiles exported and erased with tests in `test_garage_gdpr.py`, reset keeps profiles and delete asks to confirm in `test_garage_page.py`, the special-category switch gives a 404); the 24-month limit is a constant, not a setting (finding 3). FIT.6 (#910, #913): the merge service, dry run and sign-out clearing are tested; the merge notice itself is FIT.7 UI, so regression case 8 is not end to end and the row stays `partial`. SEO.8 (#899, #901): the per-page fallback chain is the pure `meta_engine.py` and the opening checklist is tested, so the row is now `done`. EVT.10 follow-up (#874): model and editor only, no output yet. I18N.17 (#895, #904) and I18N.20 3c (#872) match their spec rows, with the two owner decisions (#902, #904) added to the spec text. SEC.5: `strip_hidden` is a `unicodedata` sweep (#878, #893) and the ReDoS rewrites have parity cases (#892) |
| Spec and backlog | `make docs` gives the same backlog as the spec tables; the spec rows of I18N.17 and I18N.20 gained the owner decisions of #904 and #902, and spec 17 §17.4.5 gained the staged cookie entry (#913). Progress rows back-filled in this PR: SEO.8, FIT.5 (new), FIT.6, THM.9, I18N.10, I18N.17 (new), I18N.20, EVT.10, SEC.5 part 2, VEN.40, and 22 merge-list rows (the check 46 row now carries #880) |
| Registries | Help: `garage_account` exists in en, it, de, fr and es (`ROUTE_GUIDE`, `tests/help_routes.py`); #888 closes check 46 finding 2 in `admin__index.md`; the SEO and editor panels of #899 and #901 are Wagtail admin pages with no help route; the Manager event form got a "Tickets and changes" section with no help text (finding 2). Site map: `ACCOUNT_PAGES` has the garage page. Tenancy and endpoint-security registries have the six garage routes (#898). GDPR: `fitment.garageprofile` is in the handled map, export section `garage_profiles` and erasure (#905); no other new personal data (`seo_image`, `price` and the ticket link are not personal). Audit trail and background jobs docs list the new actions and the nightly cleanup. `GarageSignOutMiddleware` is in the settings |
| i18n | `i18n_catalog check` passes; the `.po` and `.mo` of de, es, fr and it moved together in every PR that touched them. The EXCLUDED list in `test_language_catalogs.py` holds only editor, admin and developer templates; a stale email comment there is removed here. The de, fr and es legal wording of #872 is an AI draft behind I18N.19 (human review) |
| Stale-items sweep | (a) No `TODO`, `FIXME` or `XXX` added since check 46. No row is marked done with an unmet Done-when (FIT.6 and I18N.20 stay `partial`; SEO.8 moved to `done` with the Done-when met). #757 was last updated 2026-11-20 03:15 UTC and has no item older than a week; it has no line yet for the open items of finding 5. (b) New files are all referenced; `merge_pending` and `dismiss_pending` have no caller until the FIT.7 notice (tested, not dead). (c) Lane leftovers: 2 worktrees under `.claude/worktrees` and 50 local branches already merged into `origin/main` (`git branch --merged origin/main` here); nothing deleted |
| Gates | The ReDoS slow sweep (`test_redos.py`, `security_slow`) passes on main 29631960 (37 s locally). See the PR body for `make check`, the docs tests and the guard tests |

Check 46 open items:

1. Closed locally (SEC.5, check 46 finding 1): the slow ReDoS sweep passes on current main. The last scheduled run of the security nightly (2026-11-20 02:31 UTC) is still red, from before the fixes; the run of 2026-11-21 confirms it.
2. Closed (VEN.40, check 46 finding 2): #888.
3. Closed (EVT, check 46 finding 3): #889 makes the events template tests language-agnostic.
4. Open (#757 housekeeping, check 46 finding 4): done items still to tick are for the list owner.
5. Open (SEO.12 `handlingTime`): unchanged, needs VEN.20 closures.

Findings:

1. Note, lane-a (SEC.5): confirm the nightly of 2026-11-21 is green, then drop the "still open" wording of the SEC.5 part 2 row. Release gate: no Major finding from this check.
2. Minor, lane-b (EVT.10): the Manager shop event form has a "Tickets and changes" section (#874) and `help/*/route/manager__shop__event_new.md` and `event_edit.md` do not mention the ticket link, price, free entry or postponed state. Add to the help when slice 2/2 (#875) lands.
3. Minor, lane-b (FIT.5): spec 17 §17.4.5 says the 24 months of inactivity are "a setting" and the notice 30 days before; `apps/fitment/engine.py` has them as constants (`INACTIVITY_MONTHS`, `NOTICE_DAYS`). Either make them a setting or correct the spec wording. Owner-visible only if the retention changes.
4. Minor, lane-b (FIT.6, FIT.7): `merge_pending` and `dismiss_pending` have no UI caller; the FIT.7 notice (banner with Add and Not now) must call them and add the regression case 8 test "sign-in shows the merge notice once". Track it on FIT.7 (#919 is 1/n).
5. Minor, lane-b (#757): add follow-up lines for EVT.10 slice 2/2 (#875), I18N.10 part 2 (importers, other `InlinePanel`s to `NFCInlinePanel`) and the I18N.20 admin and editor templates of 3b.
6. Note, lane-a (owner questions, from the PR bodies): ZWJ and ZWNJ are stripped in llms.txt, ICS and RSS text (#878); the notice confirmation page register against the formal notice emails (#872); the EVT.10 offer, postponed and currency assumptions (#874); the garage notice 30 days before the limit and the constants (#905); the sign-out rule of #913. No decision is needed to ship, each is recorded in the PR.
7. Note, lane-b (SEO.12): `handlingTime` still missing.
8. Note, lane leftovers: listed in the PR body, not deleted (the owner decides).

Verdict: GO from the docs side. No Major finding; the nightly confirmation of finding 1 is the only pending check.

### Coherence check 48 (main at 10cc931b)

Scope: 1930aaf0..10cc931b (everything merged on `origin/main` since check 47, #925; the first draft stopped at bb197dce): 29 task PRs (#914, #926, #931, #920, #929, #922, #933, #930, #927, #923, #918, #938, #959, #946, #944, #917, #940, #937, #948, #941, #936, #875, #924, #915, #962 to #966) in 11 lane-a trains, 269 files, about 7 100 lines added. Three new migrations: `fitment/0007` (FIT.5 retention fields), `events/0007` (https ticket links) and `vendors/0038` (the vendor name on a sales terms version). One new module, `apps/payments/stray.py`; one new nightly job, `payments.sweep_stray_captures`; one new system check, `core.E915` and `core.W915` (I18N.19); one shared outbound opener (#924). No new model, no new route. New spec rows: THM.19 to THM.35 and the colour palettes (#948), VEN.41, VEN.42, I18N.21 to I18N.25. At the time 35 pull requests were open (the open train is #989).

| Area | Result |
|---|---|
| Code vs spec | FIT.5 (#933): the retention is now `FitmentScheme` fields with validation and a form, which is the spec 17 wording; the 36-month cap is open in #958. PAY.4 (#931, #938): the stray-capture alert, manual close and 14-day auto-close match the spec 07 rules; the row stays `partial` (fixes 4 to 6, chargebacks, PAY.7). SEC.5 3a-2 (#923): 13 payment and refund scenarios pass, but coupon stacking and a payout after an IBAN change are not in the file, so part 3a is `partial` again (a row that said `done` for 3a-1 only). CMP.41 (#922): the phone gate matches the owner decision of 2026-11-19, with `sells_to_consumers` true for every vendor (finding 6). VEN.36 (#926): the config format is version 3 and reads versions 1 and 2. I18N.19 (#918): slice 1 only, no real catalog is marked, the row stays `partial`. ARC.37 (#929): the three vendor guide sections exist, the STAGE comparison is still open. SEO.7 (#914, #917, #875): the widget lists and the Event offers are in, the row stays `partial` until lane-a confirms the whole Done-when. EVT.10 2/2 (#875) matches its row. ORD.19 (#915): an item without a decidable consumer price is refused, no line is created. I18N.10 (#936, #940): the lane-a half and the Manager forms are done; the importers are not, so the row stays `partial`. I18N.20 (#941): the three vendor texts are AI drafts for the translator. I18N.15 (#920 to #966): the language-agnostic work is merged, the row stays `done`. SEC.5 (#924): no redirect is followed by an outbound transport |
| Spec and backlog | `make docs` leaves no diff; the PAY.4 spec row changed in #938 and the generated backlog moved with it. Progress rows updated here: SEC.5 3a, ARC.37, SEO.7, CMP.41, VEN.36, FIT.5, I18N.19, PAY.4, EVT.10, and 13 merge-list rows (the check 47 row now carries #925). ORD.19, ORD.32, FIT.6, I18N.10 and I18N.20 stay as they are: their follow-ups (#915, #912, #945, #936, #940, #941) are open |
| Registries | No new route, so no help, site map, tenancy or endpoint-security entry was needed. `payments.payment.stray_closed` and `stray_reminded` are in `docs/dev/audit-trail.md` and the job in `docs/dev/background-jobs.md` (#938). The three vendor guide pages exist in en, it, de, fr and es (#929). `docs/dev/translations.md` describes `core.E915` |
| i18n | `core.W915` now prints in `make check` for de, fr and es (the catalogs are AI drafts and unmarked); expected, and `E915` refuses a LIVE deploy that lists them. The de, fr and es vendor guide pages and the phone messages are AI drafts behind I18N.19 |
| Stale-items sweep | (a) The only `TODO` in the code, `apps/vendors/engine.py:84`, carries `CMP.4`. No `FIXME`. No row is marked done with an unmet Done-when after the SEC.5 3a correction. as of 2026-11-22 01:29 UTC (lane-a updated it after this check was drafted) #757 has no item older than a week, and it now carries lane-b's I18N.10 part 2 and I18N.20 part 3b lines (check 47 finding 5) plus new lines for the I18N.25 help/en spelling, the I18N.23 role-group rename migration, the lane-a `it,de` tests and the MIG legacy ids. The VEN.36 `not_returnable_by_post` line is ticked (#926) and the VEN.36 minors are partly done (#937: the terms PDF cache and the vendor name snapshot; the bulky-goods notice waits for ORD.17). (b) New files are all referenced: `payments/stray.py` is used by the payments hook, tasks and checks; `merge_pending` and `dismiss_pending` still have no caller until #945. No unused template, static file, fixture or script was found among the added files. (c) Lane leftovers, listed and not deleted: 16 worktrees under `.claude/worktrees` (including this one), of which `i19` (#918) and `tla` (#927) are merged and the others have open PRs; 208 local branches merged into `origin/main`, 120 of them `lane-*` remote branches; 16 local branches not merged |
| Gates | `make check` passes on 10cc931b (ruff, mypy, import contracts, backlog freshness, Django checks, migration check). Spot check on bb197dce (not repeated on 10cc931b), RAM first and under the shared test slot: `tests/unit`, `apps/fitment`, `apps/payments` and `apps/seo`, 2138 passed in 4 minutes 47 seconds, run with `-n 0` |

Backlog pass (before the sweep):

- Open pull requests with red CI: #875 (EVT.10 2/2, unit and integration shard 1/4 red, not diagnosed here), #945 (FIT.7 2/n, all four shards red, changes requested), #943 (PAY.4 Manager list, shard 4/4 red). #956 and #949 (I18N.15 tests) show red Lane review and e2e runs that were still being re-run.
- Conflicts: #919 (FIT.7 1/n, also changes requested), #958 (FIT.5 cap), #954 (PAY.4 Check-now throttle).
- Waiting reviews: #912 and #950 have changes requested by the lane-b account and are unanswered; #919 and #945 have changes requested by the owner account; #951, #960 and the seven I18N.15 e2e PRs (#959, #961 to #966) have no review yet.
- Ready to merge and skipped by the train, as of main 10cc931b: #919 (FIT.7 1/n, labelled but still conflicting when listed earlier), #949 (I18N.15 fitment tests, red Lane review), #954 (PAY.4 Check-now throttle) and #951 (THM.9, in the open train #989) carry the label and are unmerged; #982 and #984 are newly labelled. The ones skipped at the first draft (#948, #937, #936, #915, and train #957 with #917, #946, #944, #940) have merged.
- #757 items with no owner: none (every line carries lane-a or lane-b).
- Unanswered asks on #726 (read only): none; lane-a answered the 20:53 UTC request and asked for this check at 20:54 UTC.
- Owner questions not yet asked: none labelled `owner-question` is open. The new ones are in finding 6.

Check 47 open items: 1 SEC.5 nightly (lane-a to confirm; #923 added tests but no fix). 2 closed by #930. 3 closed by #933. 4 open (the notice is #945, red). 5 open (the #757 lines are below). 7 SEO.12 `handlingTime`: unchanged, needs VEN.20 closures.

Findings:

1. Minor (queue), lane-b: #919 (conflicting) and #945 (all shards red) block FIT.6 regression case 8. Merge main into #919, fix the shards of #945 (stacked on it), then ask for re-approval. Also #958 conflicts with main: merge main.
2. Minor, lane-b: #875 shard 1/4 red. Re-run once and read the failing test; if it is a language-dependent test, fix it in the PR.
3. Minor, lane-a: #943 shard 4/4 is red; #912, #950 and #971 have changes requested; #954 is labelled and not yet in a train. (#915, #936, #937, #948 and the rest of the first draft's list merged.)
4. Minor, lane-a (SEC.5): part 3a-2 lacks the payout-after-IBAN-change scenario and coupon stacking (waits for PRM). The row now says `partial`.
5. Minor, lane-b (I18N.19): staff preview on STAGE and counsel sign-off remain; until then de, fr and es cannot be on LIVE (`core.E915`). No action beyond the row.
6. Note, lane-a (owner questions, from the PR bodies): `sells_to_consumers` is true for every seller (#922), so a business-only seller must also give a phone number; the 14-day auto-close of a stray capture only on a confirmed full refund and no step-up for the manual close (#938); the 36-month cap is decided by the owner (2026-11-21, 36 months) and the open work is the clamp migration in #958.
7. Note, #757 (as of 2026-11-22 01:29 UTC these lines are already in or ticked on #757, written by its owner; this PR did not edit it):
   - tick: VEN.36 `not_returnable_by_post` (done: #926); ARC.37 seller guide sections (done: #929, but the STAGE comparison stays open, so keep the line and add "STAGE comparison" only);
   - `- [ ] (lane-b) I18N.10 part 2: NFC for the importers and for the other InlinePanels on page models (switch to NFCInlinePanel); #870 and #890 did the CMS page forms and the site settings.`
   - `- [ ] (lane-b) I18N.20 part 3b: the Wagtail admin and editor templates are outside the catalog scope; bring them in or list them in the exclusion list with a reason.`
   - `- [ ] (lane-a) SEC.5 3a-2: add the payout-after-IBAN-change scenario now, and coupon stacking when PRM exists (#923 covers the rest).`
   - `- [ ] (lane-b) FIT.7: the sign-in merge notice calls merge_pending and dismiss_pending and adds the regression case "sign-in shows the merge notice once" (#945).`
8. Note, lane leftovers: listed above and in the PR body; nothing deleted.

Verdict: GO from the docs side. No Major finding in merged code; finding 1 is a queue item, not a release blocker.
