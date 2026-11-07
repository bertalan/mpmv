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
| Email | EU transactional provider via API (SMTP as fallback) behind an `EmailSender` interface, sent through an outbox on the task queue (ARC.16) | Per-language templates; provider choice pending |
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
```

- An arrow `A → B` means that **B may import A**, never the other way round.
- `tax` and `shipping` do not know about `orders`: they receive a *context* (dataclass) and return a
  *decision*. This keeps them testable in isolation.
- `api` and `mcp` contain no logic: they call the services.
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
| Platform operator | Country, VAT number, regime, commission VAT mode, e-invoice channel (informational only: the platform sends no e-invoices, see [00](00-vision-and-review.md)) and DAC7 authority come from the versioned `PlatformOperator` (`apps.core.services.current_operator(at)`); never a literal such as `"IT"` in application code (guarded by `tests/unit/test_no_platform_country_literal.py`; tests and migrations excluded) |
| Identifiers | Internal `bigint` PK; UUID `public_id` for APIs and technical URLs; natural key `(vendor, sku)` |
| History | Configuration tables with `valid_from` / `valid_to`; orders store snapshots, not live references |
| Dry-run | Every mutating service accepts `dry_run=True` and returns a `ChangeSet` (diff) |
| Audit | Every sensitive mutation writes an `AuditLog` (who, what, before/after, from where) |
| Languages | Order EN (primary, source) → IT → DE → FR → ES → later languages; all five at go-live for interface text, visitor content in EN only until after go-live, except the existing IT texts migrated from the current site and vendor-authored events, written in any enabled language (D-18, [15](15-events.md)); languages are configuration; no hardcoded text in templates |
| Time | UTC in the DB; Europe/Rome for cron jobs and documents |

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
| ARC.13 | STAGE environment on Hetzner: provision app and DB VPS, deploy from tag, rollback (including a deliberately unhealthy release), smoke tests; includes the STAGE checks of CORE.7 (OpenAPI published) and I18N.1 (`/it/`, `/de/`, `/fr/` and `/es/` active) | P1 | 8 | ARC.5 | Tag deploy and automatic rollback proven on STAGE; CORE.7 and I18N.1 STAGE checks pass |
| ARC.15 | Media storage: S3-compatible Hetzner Object Storage (EU) behind the CDN, private buckets for documents, signed URLs, django-storages | P1 | 6 | ARC.5, ARC.13 | Uploads work on STAGE with a read-only container filesystem; private documents only via signed URLs |
| ARC.16 | Transactional email: provider behind an `EmailSender` interface (EU provider, choice pending), per-language templates (gettext), DKIM/SPF/DMARC, bounce and complaint handling, outbox with retries on the task queue, test backend | P1 | 8 | ARC.9, ARC.13 | OTP, reminder and order emails are sent through the outbox with retries and bounces recorded |
