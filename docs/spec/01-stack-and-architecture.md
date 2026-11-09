# 01 — Stack and architecture

## 1.1 Evaluation of the Python alternatives

Criteria: fit with the domain (multivendor, per-vendor VAT, shipping by zone/weight, quotes,
split payment), cost of running it (components to keep in production, updates), development
speed, multilingual CMS and SEO.

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **A. Django + custom commerce + Wagtail (content and backoffice)** | Full control over the domain; one language and one deploy; Wagtail provides CMS, StreamField, images with renditions, page i18n, a modern admin; Postgres for everything | Commerce (cart, orders, payments) has to be written, but it is the small part | **Recommended** |
| B. Saleor (headless GraphQL) | Complete commerce, multi-channel, well maintained | No native multivendor; needs a separate frontend (Next.js): **two stacks**; custom VAT and shipping still have to be written as plugins or external apps; extra components (Celery, Redis) | Oversized and more expensive to run |
| C. django-oscar | Mature Django commerce, "partner" concept | Customization by forking apps; dated architecture; multivendor is partial; tax and shipping must be rewritten anyway | Adaptation cost exceeds the benefit |
| D. Wagtail at the center (August plan) | A single admin | Modeling products and orders as Wagtail pages is unnatural; tying the domain to the CMS complicates tests and API | Wagtail yes, but at the edges |
| E. "Turnkey" Python multivendor framework | — | None mature and maintained in 2026 (e.g. Shuup effectively stalled) | Rejected |

**Recommendation.** The hard domain (VAT, shipping, quotes, split) is already custom today and
would remain so with any framework. At the marketplace's scale (dozens of vendors, thousands of products,
dozens of orders per day) a lean commerce core in Django costs less than a generic framework
that has to be bent. Wagtail is used where it excels: pages, blog, taxonomy landing pages, vendor storefronts,
image management and staff backoffice.

## 1.2 Proposed stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.13+ | The current pipelines run on 3.11: easy alignment |
| Framework | **Django 6.1**, upgrading to **6.2 LTS** (expected April 2027) before go-live | Decided in ADR-0001 (D-11). From 6.0 the task framework and CSP are built in |
| CMS and backoffice | **Wagtail** (latest LTS) | Pages, blog, images, snippets; ViewSets for the staff admin |
| DB | **PostgreSQL 17** | Single datastore: data, queues, full-text and trigram search, session cache |
| API | **django-ninja** (Pydantic schemas) | The pipelines already use Pydantic: shared contracts |
| Async tasks and cron | **Procrastinate** (PostgreSQL queue, periodic tasks, locks) behind `apps.core.tasks` | No Redis or Celery. Decided in ADR-0004 |
| Frontend | Django templates + **HTMX** + minimal Alpine.js; CSS with a lightweight build | No SPA; accessible (EAA) and fast; themes with design tokens, see [14](14-frontend-themes.md) |
| Search | Postgres FTS + `pg_trgm` (SKU prefix, typos) | Wagtail search backend on Postgres |
| Media | Hetzner Object Storage (S3 compatible, EU) + CDN (Cloudflare already in use); private buckets and signed URLs for documents | WebP/AVIF renditions generated in the platform |
| Payments | Stripe Connect (Express), behind a `PaymentGateway` interface | See [07](07-cart-orders-payments.md) |
| Email | EU transactional provider via API (SMTP as fallback) behind an `EmailSender` interface, sent through an outbox on the task queue (ARC.16) | Per-language templates; provider choice pending (owner question Q-48), wired on STAGE/LIVE in ARC.19 |
| Antibot | Cloudflare Turnstile | Already in use |
| Container | OCI images from a `Containerfile`; locally **Apple `container`** (Containerization framework, macOS 26), no Docker Desktop | `container` 1.0 has no compose: local orchestration via scripts/Makefile |
| Deploy | Same OCI images on a Hetzner Cloud VPS (EU, x86) under Podman Quadlets (app, worker, Caddy) + self-managed PostgreSQL 17 on a dedicated database VPS on the private network | Versioned images, rollback by tag. Decided in ADR-0007 (D-12), which supersedes ADR-0005 |
| Quality | ruff, mypy, pytest-django, coverage, pip-audit, secret scanning | See [12](12-security-infra-operations.md) |
| Testing | TDD; three suites: unit (pure functions), integration (real Django + PostgreSQL, no mocks), e2e (Playwright on a live server, screenshots) | [ADR-0006](../adr/0006-testing-strategy.md) |
| Observability | JSON logs + error tracking (Sentry or self-hosted GlitchTip) + healthcheck | |

## 1.3 Architecture: modular monolith

A single Django project, one app per *bounded context*. The logic lives in **services** (`services.py`)
and **pure functions** (`engine.py`). Views, APIs, commands and the MCP server are thin
adapters on top of the same services.

```
mpmv/                     # settings (base/dev/stage/live), urls, asgi/wsgi
apps/
  core/                   # base model, Money, Country, PlatformOperator, AuditLog, FeatureFlag, Geocoder, map tile proxy
  accounts/               # User, roles, MFA, sessions
  vendors/                # Vendor, VendorMember, VendorPlan, KYC/DAC7 documents, policies
  catalog/                # Product, Variant, translations, navigation taxonomies, attributes, media, redirects
  tax/                    # TaxRate, VendorTaxProfile, tax engine (resolve_tax)
  shipping/               # Carrier, Zone, VendorRate, weight bands, calculator, health, simulator
  cart/                   # Cart, CartLine, checkout service
  orders/                 # Order, VendorOrder, lines, snapshots, state machine, quotes
  payments/               # gateway interface, Stripe Connect, webhooks
  ledger/                 # LedgerEntry, commissions, payouts, reconciliation
  ingest/                 # supplier sources, adapters, staging, catalog upsert
  cms/                    # Wagtail pages: Home, Standard, Blog, Taxonomy landing, Vendor storefront, Legal
  events/                 # EventIndexPage and EventPage (Wagtail), recurrence, moderation, RSS/ICS feeds, maps (spec 15)
  themes/                 # theme registry, theme-aware template loader, design tokens (spec 14); theme files in themes/<slug>/
  seo/                    # sitemap, JSON-LD, Google/Meta feeds, llms.txt, robots
  compliance/             # DAC7, GPSR, GDPR requests, DSA reports, withdrawal
  api/                    # django-ninja router /api/v1
  mcp/                    # MCP server: tools on top of the services
  manager/                # the Manager backoffice: shop and platform spaces, setup wizard views (adapter, spec 02)
```

### Dependency rules between apps

```mermaid
flowchart TD
  core --> accounts
  core --> themes
  core --> vendors
  themes -.pure palette functions, THM.11.-> vendors
  accounts --> vendors
  vendors --> catalog
  vendors --> tax
  vendors --> shipping
  catalog --> cart
  catalog --> cms
  tax --> cart
  shipping --> cart
  cart --> orders
  orders --> payments
  orders --> ledger
  payments --> ledger
  catalog --> ingest
  catalog --> seo
  cms --> seo
  catalog --> events
  cms --> events
  events --> seo
  events --> compliance
  orders --> compliance
  ledger --> compliance
  api -.adapter.-> orders & catalog & ingest & shipping & tax & events
  mcp -.adapter.-> api
  manager -.adapter.-> orders & catalog & ingest & shipping & tax & events & ledger & compliance
```

- An arrow `A → B` means that **B may import A**, never the other way round.
- `tax` and `shipping` do not know about `orders`: they receive a *context* (dataclass) and return a
  *decision*. This keeps them testable in isolation.
- `api`, `mcp` and `manager` contain no logic: they call the services.
- `events` sits one layer above `catalog` and `cms` ([15](15-events.md)); `shipping`, `cart`,
  `orders`, `catalog` and `cms` never import `events`. Vendor holiday closures are a vendor setting
  (VEN.20): the `shipping` service reads them and passes them to the shipping engine as data
  (`ClosedPeriod`), and `cart` gets the dispatch date from `shipping`.
- An import-linter rule in CI enforces the graph.

### Cross-cutting conventions

| Area | Rule |
|---|---|
| Money | `Decimal` with 2 decimal places in the DB (`MoneyField` = `DecimalField(12,2)`), internal calculations to 4 decimal places, `ROUND_HALF_UP` rounding **per line**; explicit EUR currency |
| Countries | ISO 3166-1 alpha-2 (`GR`, not `EL`); EU special territories modeled (see [05](05-tax.md)) |
| Platform operator | Country, VAT number, regime, commission VAT mode, e-invoice channel (informational only: the platform sends no e-invoices, see [00](00-vision-and-review.md); removal proposed in ARC.17) and DAC7 authority come from the versioned `PlatformOperator` (`apps.core.services.current_operator(at)`); never a literal such as `"IT"` in application code (guarded by `tests/unit/test_no_platform_country_literal.py`; tests and migrations excluded) |
| Identifiers | Internal `bigint` PK; UUID `public_id` for APIs and technical URLs; orders, ledger, stock, carts, feeds and URLs reference the internal identity. The vendor SKU is a label: unique per vendor on its match key, used only to match imports ([03](03-catalog.md)) |
| History | Configuration tables with `valid_from` / `valid_to`; orders store snapshots, not live references |
| Dry-run | Every mutating service accepts `dry_run=True` and returns a `ChangeSet` (diff), or takes its caller's `ChangeSet`; the only exceptions are listed under [Dry-run exceptions](#dry-run-exceptions) (guarded by `tests/unit/test_dry_run_guard.py`) |
| Audit | Every sensitive mutation writes an `AuditLog` (who, what, before/after, from where) |
| Languages | Order EN (primary, source) → IT → DE → FR → ES → later languages; all five at go-live for interface text, visitor content in EN only until after go-live, except the existing IT texts migrated from the current site and vendor-authored events, written in any enabled language (D-18, [15](15-events.md)); languages are configuration; no hardcoded text in templates |
| Time | UTC in the DB; Europe/Rome for cron jobs and documents |
| Pure engines | `apps.*.engine` modules import no Django, no `models` and no `services` module (import-linter contract, ARC.18); the pure Django helpers they need come through `apps.core.text` |

### Dry-run exceptions

Owner decision Q-18 accepts four kinds of mutating service without `dry_run`: telemetry,
throttle counters, one-time code consumption and migration hooks. A preview makes no sense for
them, because the write is the point of the call or the call consumes something that cannot be
replayed. The guard test `tests/unit/test_dry_run_guard.py` names the same services and fails
when a new mutating service has neither `dry_run` nor a place in this table (ARC.18).

| Service | Category | Reason |
|---|---|---|
| `apps.accounts.services.start_totp_enrolment` | one-time code | Creates the pending device with a fresh secret; a preview would issue one too |
| `apps.accounts.services.confirm_totp_device` | one-time code | Verifying the code consumes it |
| `apps.accounts.services.use_recovery_code` | one-time code | Verifying a recovery code consumes it |
| `apps.accounts.services.register_passkey` | one-time code | Stores a registration whose one-time WebAuthn challenge is already spent |
| `apps.accounts.services.authenticate_passkey` | one-time code | Spends the one-time WebAuthn challenge and advances the signature counter |
| `apps.accounts.services.redeem_action_token` | one-time code | Spends the single sign-in use of a secure action link (CORE.15) |
| `apps.api.services.authenticate` | telemetry | Records when an API key was last used |
| `apps.cms.services.sync_locales` | migration hook | Post-migrate handler that adds the missing Wagtail locales |
| `apps.cms.services.seed_site` | migration hook | Idempotent setup seed (`seed_cms`) that only adds missing sample pages |
| `apps.core.services.record_heartbeat` | telemetry | Operational heartbeat of a scheduled job |
| `apps.core.services.record_throttle_failure` | throttle counter | Counts one failed attempt |
| `apps.core.services.clear_throttle` | throttle counter | Forgets the failures of a key after a success |
| `apps.core.services.purge_expired_throttles` | throttle counter | Deletes expired counters |
| `apps.core.services.consume_rate_limit` | throttle counter | Counts one request against a fixed-window rate limit |
| `apps.tax.services.record_vat_validation` | telemetry | Append-only record of a VIES answer |
| `apps.tax.services.verify_vat_number` | telemetry | Asking VIES stores its answer in the append-only validation log; a check is a lookup |
| `apps.tax.services.recheck_vat_number` | telemetry | Same lookup as `verify_vat_number`, forced past the cache by the background job |

### Marketplace configuration pages (ARC.24)

Owner decision 2026-11-09. The `PlatformOperator` and the other platform settings that have no
page today get pages in the Manager platform space, section "Configuration and tax", for the
Configuration and tax role ([02](02-core-identity-permissions.md), CORE.18):

| Page | What it edits | Owning service |
|---|---|---|
| Operator | Legal name, VAT number, country, regime, commission VAT mode, DAC7 authority (versioned `PlatformOperator`) | `core` |
| Countries | Selling countries and destination countries; the allowed vendor establishment countries are shown read-only and changed only by the platform owner (CMP.13) | `core`, `compliance` |
| Languages | Enabled languages within the supported set (D-18, [09](09-multilingual.md)) | `core` |
| Currency | EUR, read-only until a multi-currency task exists (spec 01 conventions) | `core` |
| VAT rates and regimes | Rates with validity, regimes and exemption reasons per country (TAX.13, [05](05-tax.md)) | `tax` |
| Commissions | Plans and versioned commission rules (VEN.2, [04](04-vendors-commissions-ledger.md)) | `vendors`, `ledger` |
| Shipping rules | Carriers, zones and default rates; the advanced editor is SHP.6 ([06](06-shipping-and-quotes.md)) | `shipping` |
| Impersonation policy | Per platform role: may start, modes, maximum length, allowed "act as" groups; full-access grants to named users; the fixed hard floor shown with its reasons (CORE.10, [02](02-core-identity-permissions.md)). Changed **only by a superuser**, with a reason, so the Configuration role cannot raise its own power | `accounts` |

Every page follows the same flow: form, then a **dry-run preview** of the `ChangeSet` (the diff,
the effective date, how many vendors or open carts it touches), then confirmation behind an
**MFA step-up**. The service writes a new version with `valid_from` (history rule above) and an
`AuditLog` entry with before and after; past versions are read-only and can be restored only as
a new version. Orders keep their snapshots. The pages call the owning app's services; a setting
that has no service yet gets one in its own app, never logic in `manager`.

### First-start setup wizard (ARC.25, ARC.26)

Owner decision 2026-11-09. A fresh installation (STAGE, LIVE, a demo) is configured through a
guided wizard at `/setup/`, not by hand in the database.

| Step | What it sets |
|---|---|
| 1. Administrator | The first user: email, password, mandatory MFA enrolment (TOTP or passkey) before the next step; gets the platform owner and Configuration and tax roles (CORE.18) |
| 2. Operator | Legal name, VAT number, country, regime: the first `PlatformOperator` version (ARC.24 services) |
| 3. Languages and countries | Selling languages and countries |
| 4. Currency and tax | Currency (EUR), VAT rates from the reference source, regime check |
| 5. Payments | Shows whether the payment provider keys are present in the server environment and in which mode (test or live), never their values; the wizard stores no secret |
| 6. Basic shipping | One carrier, zone and default rate from a template, editable later |
| 7. Initial data | Empty start or demo data (ARC.25); import from a file or feed, or from an existing shop (ARC.26) |

The wizard is resumable (the state is saved after each step, as an `InstallationState`
singleton with the current step) and ends on an editable summary that links every value to its
ARC.24 page.

**No unauthenticated takeover.** The chosen design:

- `/setup/` and every URL below it answer **404** unless the installation is not completed
  **and** a valid setup token exists. There is no public setup URL by default.
- The token comes from the server: `manage.py setup_token` prints a one-time random token and
  stores only its hash, with a 60-minute expiry; a new token replaces the old one. For automated
  provisioning `MPMV_SETUP_TOKEN` in the server environment works the same way (compared by
  hash, spent on first use).
- The token is typed into a form and sent by POST, never in a query string (so it stays out of
  access logs, browser history and the Referer header); constant-time comparison, per-IP rate
  limit (CORE.5), and five failed attempts invalidate it.
- A valid token opens step 1 only and is spent there. From step 2 on, the wizard needs the new
  administrator's session with a verified second factor; the token gives no further access.
- When the summary is confirmed the installation is **completed** (audited): `/setup/` answers
  404 even with a token, and `setup_token` refuses to issue one.
- `manage.py setup_reset` reopens the wizard only where `LIVE_DATA` is off, after a typed
  confirmation, with an `AuditLog` entry; it keeps the data, and the steps prefill from it. On
  LIVE it refuses and changes nothing.
- A data migration marks an installation that already has a `PlatformOperator` and a staff user
  as completed, so the wizard never opens on a running STAGE.
- Setup pages are not cached and send `Referrer-Policy: strict-origin`.

**Initial data sources** (step 7). Each one shows a preview (dry-run: counts per entity, sample
rows, conflicts) before anything is written, and none of them changes existing data: they only
add rows, and a record that matches an existing one is reported and skipped, never updated.

| Source | Where it comes from | Guard |
|---|---|---|
| Empty start | Nothing | — |
| Demo data | The synthetic seed (ARC.10) | Offered only where `ALLOW_SEED` is on and `LIVE_DATA` off; the service refuses on LIVE as `seed.E001` does ([demo seed](../dev/demo-seed.md)) |
| Import from a file or feed | The supplier adapters of [08](08-import-pipelines.md) (ING.9, ING.13) | Dry-run first, per-source write flag |
| Import from an existing shop | A source connector ([13](13-migration-cutover.md), MIG.12 to MIG.14) | Read-only connector, schema validation, preview |

## 1.4 Example of the service + pure function pattern

```python
# apps/tax/engine.py  — no I/O, no ORM
@dataclass(frozen=True)
class TaxContext:
    vendor_regime: VendorRegime  # OSS | DOMESTIC_ONLY | EXEMPT
    vendor_country: str
    product_fiscal_category: FiscalCategory
    destination_country: str
    destination_territory: str | None  # e.g. "ES-CN" Canary Islands
    customer: CustomerTaxStatus  # B2C | B2B_VALIDATED(vat_country)
    rates: RateTable  # rates valid on the order date


def resolve_tax(ctx: TaxContext) -> TaxDecision: ...


# apps/tax/services.py — reads the DB, builds the context, calls the engine
def tax_for_cart_line(line: CartLine, checkout: CheckoutData, on: date) -> TaxDecision: ...
```

## 1.5 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| ARC.1 | ADR-0001 stack (`docs/adr/`) approved, versions pinned | P1 | 4 | — | ADR signed off in the repo |
| ARC.2 | ADR-0003 boundaries of the custom commerce layer (what is not built in-house); ADR-0004 task queue (django-tasks vs Procrastinate) | P1 | 4 | ARC.1 | ADRs in the repo |
| ARC.3 | Repo bootstrap: app structure, per-environment settings, env, pre-commit, Makefile | P1 | 8 | ARC.1 | `make check` and `make test` green locally |
| ARC.4 | CI: ruff, mypy, pytest, coverage, import-linter (dependency graph), pip-audit, secret scan | P1 | 8 | ARC.3 | Pipeline green on PRs |
| ARC.5 | Containers: OCI `Containerfile`, local stack with Apple `container` (network, volumes, app, worker, Postgres), Linux runtime for STAGE/LIVE, versioned images | P1 | 10 | ARC.3 | `make dev` with `container`; release image, Quadlet units, and deploy and rollback tooling tested locally and in CI |
| ARC.6 | `core`: base model, `MoneyField`, Country, `ChangeSet`/dry-run, `AuditLog` | P1 | 12 | ARC.3 | Tests on rounding and audit |
| ARC.7 | `PlatformOperator` (country, VAT number, regime, e-invoice channel, DAC7 authority) as configuration | P1 | 4 | ARC.6 | No "IT" constant in the tax code |
| ARC.8 | Feature flags per environment and per vendor + kill switch (checkout, import, payments) | P2 | 6 | ARC.6 | Flags manageable from the admin |
| ARC.9 | Task worker + cron scheduler on Postgres, with locks and retries | P1 | 8 | ARC.2 | Test job scheduled and observable |
| ARC.10 | Synthetic data seed: vendors with all regimes, EN/IT products, zones, orders | P1 | 10 | CAT.1, TAX.1, SHP.1 | Reproducible `make seed`, used by the tests |
| ARC.11 | Test harness per ADR-0006: unit / integration / e2e suites, auto-markers, Playwright with screenshots, CI reports | P1 | 6 | ARC.3 | `make test-unit`, `make test`, `make test-e2e` green; e2e screenshots saved |
| ARC.12 | Adopt factory_boy for test data: one DjangoModelFactory per model in `apps/<app>/tests/factories.py`, Faker with a fixed seed, synthetic data only; convert hand-written builders; document in ADR-0006 and CONTRIBUTING | P1 | 4 | ARC.6 | Every model has a factory used by the tests; no hand-written model builders left |
| ARC.13 | STAGE environment on Hetzner: provision the single STAGE VPS (app, worker, Caddy and PostgreSQL together, owner decision 2026-11-08), multi-arch release image (amd64 and arm64, the arm64 variant built on a native GitHub arm64 runner, no QEMU), deploy from tag, rollback (including a deliberately unhealthy release), smoke tests; database TLS with `sslmode=verify-full`, the server certificate signed by a free internal CA (openssl or step-ca) whose root certificate the app container mounts (owner decision MQ-11); a worker health check on the ARC.9 heartbeat that restarts a stuck worker automatically and alerts staff (owner decision MQ-10); includes the STAGE checks of CORE.7 (OpenAPI published) and I18N.1 (`/it/`, `/de/`, `/fr/` and `/es/` active) | P1 | 11 | ARC.5 | Tag deploy and automatic rollback proven on STAGE; the release image is published for amd64 and arm64; on STAGE the app connects to PostgreSQL with `verify-full` and refuses a server certificate not signed by the internal CA; a worker whose heartbeat stops is restarted and staff get an alert; CORE.7 and I18N.1 STAGE checks pass |
| ARC.15 | Media storage: S3-compatible Hetzner Object Storage (EU) behind the CDN, private buckets for documents, signed URLs, django-storages | P1 | 6 | ARC.5, ARC.13 | Uploads work on STAGE with a read-only container filesystem; private documents only via signed URLs |
| ARC.16 | Transactional email code: `EmailSender` interface with test (in-memory) and console backends, outbox on the task queue with retries, exponential backoff and idempotent sends, per-language templates (gettext, EN fallback) with a text and a CSP-safe HTML body and no tracking pixels, provider-neutral bounce and complaint ingestion with a signed-webhook stub and address suppression | P1 | 5 | ARC.9 | OTP, reminder and order emails are sent through the outbox; a transient failure retries and a permanent one stops; a hard bounce suppresses later sends; no email contains a tracking pixel |
| ARC.17 | Remove the e-invoicing leftovers of the no-invoicing principle (coherence check 5): `PlatformOperator.einvoice_channel` and its choices (migration), admin, factory and tests, and comments that say the platform writes FatturaPA XML | P1 | 1 | ARC.7 | No model, admin, service, factory or test refers to an e-invoice channel; `makemigrations --check` is clean |
| ARC.18 | Architecture guards (coherence checks 2 and 5, owner decision Q-18): an import-linter contract that forbids `django.db` and any `services` or `models` module in `apps.*.engine`; the accepted exceptions to the dry-run rule (telemetry, throttle counters, one-time code consumption, migration hooks) listed in the §1.3 conventions with a guard test that names them | P1 | 2 | ARC.4, ARC.6 | `lint-imports` fails when an engine imports `django.db`; a new mutating service without `dry_run` that is not on the list fails the guard test |
| ARC.19 | Transactional email on STAGE/LIVE: EU provider adapter behind `EmailSender` (provider choice pending, owner question Q-48), DKIM/SPF/DMARC DNS, provider webhook endpoint registration, deliverability check | P1 | 3 | ARC.16, ARC.13 | STAGE sends an OTP email through the provider with DKIM, SPF and DMARC passing; a test bounce reaches the webhook and suppresses the address |
| ARC.20 | Trusted input for AI agents: on GitHub, only comments, reviews and issues by the owner account and known bots (Dependabot, GitHub Actions) count as instructions or coordination for the lanes; everything else is untrusted data and is never acted on; external spam reviews are hidden as spam and the repository keeps interaction limits for collaborators only | P1 | 1 | — | `docs/dev/parallel-lanes.md` and `CLAUDE.md` state the rule; interaction limits are `collaborators_only` with a renewal date before they expire |
| ARC.21 | STAGE abuse tests: deliberately stress and break STAGE before go-live. Scenarios: load test of storefront, API and admin with a scripted tool chosen in the task (record the choice in the PR); kill the app and worker containers mid-request and check systemd restarts them; restart PostgreSQL under load; fill the disk until the alerts fire; expire or swap the database certificate and check the app refuses to connect; deploy a deliberately unhealthy release and check the automatic rollback; restore the database from backup to an empty server (with OPS.1). App-to-database network failures need a two-VPS setup, so they are repeated on LIVE-equivalent servers before cutover (MIG.9) | P1 | 8 | ARC.13, OPS.2 | Each scenario has a scripted run, a short report with numbers (requests per second, p95 latency, time to recover), and every finding is fixed or becomes a new backlog row |
| ARC.22 | Public STAGE demo mode (owner decision 2026-11-08): once STAGE is up it becomes a public demo that anyone can reach, with published demo passwords, data reset every 60 minutes and no search engine indexing. A `MPMV_DEMO_MODE` setting, off by default; a Django system check refuses to start when it is on in the LIVE settings. Demo accounts: a customer, a vendor owner and a staff member with limited rights; never a superuser, and no account can manage users, permissions, groups or API keys; the technical Django admin stays restricted (OPS.5). Demo passwords come from server environment configuration (never the repo) and are shown in a demo panel on the STAGE login page. Demo accounts are exempt from MFA and from account lockout (otherwise anyone could lock them for everyone); a per-IP rate limit applies instead. No outbound email: messages land in an in-app demo mailbox; payments use the provider's test mode only. Hourly reset: the database is restored from a snapshot of the synthetic seed (ARC.10) and uploaded media are wiped; a site-wide banner shows "Demo: data resets at HH:MM, do not enter real data"; uploads accept images only, with a size cap. Not indexable: an `X-Robots-Tag: noindex, nofollow` header on every response, a robots meta tag in the base layout and a `robots.txt` that disallows crawling. ARC.21 abuse tests run with demo mode off, in a maintenance window | P1 | 6 | ARC.13, ARC.10, CORE.5, ARC.16 | On STAGE with demo mode on, each demo login works from the login-page panel; a demo account cannot reach user, permission or key management; no email leaves the server; a reset restores the seed and wipes uploads within the hour, and the banner shows the next reset time; every response carries the noindex header; LIVE settings with demo mode on fail the system check |
| ARC.23 | Release gate with production parity (STAGE findings 2026-11-08: no writable media, volume ownership, a Podman flag missing on the host, the deploy script off PATH over ssh, units copied by hand, slow rollback): a CI parity smoke on amd64 and arm64 deploys the built image with its own `mpmv-deploy` under the real Quadlet units (systemd --user, Podman's quadlet generator), the production Caddyfile, env files shaped like the examples with throwaway values and PostgreSQL over TLS `verify-full`, then migrates, seeds and checks pages, a media file and `/healthz`; the release workflow pushes only after it passes; units, Caddyfile and `mpmv-deploy` ship inside the image and are installed and rolled back with the tag; `mpmv-deploy doctor` before every deploy; post-deploy check of health, home page and a media file through Caddy within 60 s; release rules in `docs/dev/release.md` | P1 | 8 | ARC.13 | A release image that cannot write media, cannot migrate, cannot serve a page, a `/media/` file or `/healthz` under the real units fails CI before it is pushed; the server's units always match the deployed tag; a bad release rolls back with its units in under 60 s |
| ARC.24 | Marketplace configuration pages (owner decision 2026-11-09, spec 01 §1.3): "Configuration and tax" section of the Manager platform space for the operator (`PlatformOperator`), countries, languages, currency (read-only EUR), VAT rates and regimes, commissions and shipping rules; every page form → dry-run `ChangeSet` preview (diff, effective date, impact counts) → confirmation with MFA step-up → new version with `valid_from` and AuditLog before and after; past versions read-only, restore only as a new version; pages call the owning app's services (missing services added in their own app); owner-only settings (CMP.13) read-only for the Configuration role; the impersonation policy page (CORE.10) on the same flow, changed only by a superuser with step-up and a mandatory reason | P1 | 17 | ARC.7, CORE.17, CORE.18 | A Configuration user changes the operator VAT number: the preview shows the diff and writes nothing (row counts unchanged), the confirmation without step-up is refused, with step-up a new version exists and the AuditLog has before and after; an order placed before keeps its snapshot; Finance, Support, Catalog and vendor users get 404 on every configuration URL, GET and POST; a Configuration user cannot change the allowed vendor establishment countries; a Configuration user's change to the impersonation policy is refused and a superuser's change without a reason is refused; the no-country-literal guard stays green |
| ARC.25 | First-start setup wizard at `/setup/` (owner decision 2026-11-09, spec 01 §1.3): `InstallationState` singleton, steps administrator with mandatory MFA, operator, languages and countries, currency and tax, payments (presence and mode of the environment keys only, no secret stored), basic shipping, initial data (empty or demo); resumable, editable summary at the end, completion audited; access only with a one-time setup token from `manage.py setup_token` or `MPMV_SETUP_TOKEN` (hash stored, 60 min expiry, POST form only, constant-time check, per-IP rate limit, invalidated after five failures, spent on step 1, then the administrator's MFA session); 404 when completed or without a valid token; `manage.py setup_reset` audited and refused where `LIVE_DATA` is on; data migration that marks running installations completed; `consume_setup_token` added to the dry-run exceptions table as a one-time code | P1 | 14 | ARC.24, CORE.4, CORE.5, CORE.18, ARC.10 | Without a valid token every `/setup/` URL answers 404 on GET and POST; a wrong, expired or used token is refused, and the sixth attempt is refused even with the right token; a token in the query string is ignored; the wizard cannot pass step 1 without a confirmed second factor; after a closed browser the administrator logs in with MFA and resumes at the saved step; after completion `/setup/` is 404 with a fresh token and `setup_token` refuses; `setup_reset` with LIVE settings exits non-zero and changes nothing, on STAGE it is in the AuditLog; with LIVE settings the demo option is absent and a forged demo POST writes nothing; an e2e test with screenshots runs a fresh install to the summary; an existing installation is marked completed by the migration |
| ARC.26 | Wizard initial data from imports (owner decision 2026-11-09): step 7 offers "import from a file or feed" (ING.9 adapters, ING.13 source wizard) and "import from an existing shop" (MIG.12 connectors) with a dry-run preview (counts per entity, sample rows, conflicts); imports only add rows, a record matching existing data is reported and skipped, never updated; the same flow is reachable later from the platform space "Imports" section for the Catalog and moderation role (catalog data) and the platform owner (customers and orders) | P2 | 8 | ARC.25, ING.13, MIG.13 | The preview writes nothing; an import over a populated installation leaves every existing row unchanged (checksums before and after); a WooCommerce fixture shop imported through the connector shows its preview counts and then loads them; a Catalog user cannot start a customer or order import (404) |
