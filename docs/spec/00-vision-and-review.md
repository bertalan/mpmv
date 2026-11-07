# 00 — Vision, scope and review of the August plan

## 0.1 The idea on one page

The current system is a curated, vertical multivendor marketplace. Customers also browse
**by navigation taxonomies** (today a two-level taxonomy, such as brand → model). The site is
bilingual EN/IT; the new system launches with EN, IT, DE, FR and ES for interface text, and
visitor content in EN until the owner decides on its translation after go-live (D-18).

Current operating model (to be kept):

| Aspect | How it works |
|---|---|
| Platform role | Facilitator: the **vendor invoices and ships**, the platform retains the commission and the payment fee |
| Payment collection | Stripe Connect (vendors' Express accounts), automatic split of funds |
| Active vendors | 5 profiles, pseudonymized here as A–E: A (large catalog, pipeline from the supplier's site), B (XLSX price list), D (exempt regime), E (quote-only catalog), C (0 products) |
| Catalog | Thousands of products per language, mostly imported by **Python pipelines** (scraping, extraction, Gemini AI, images) |
| Taxation | Per-vendor regimes: OSS, below threshold (`domestic_only`), exempt (art. 74 / flat-rate regime (forfettario)); B2B reverse charge; non-EU export |
| Shipping | Zones per carrier × vendor × weight band (main carrier, a vendor's own carrier, aggregator, local pickup) + **quote** for countries not covered |
| Markets | EU, UK and rest of the world |
| Expected growth | From a few vendors to dozens, with thousands of products |

Value for the vendor (from the sales presentation): assisted catalog import, translations, SEO for
taxonomy pages, editorial articles and featured vendors, cross-selling between partners. Today all of this
requires manual work on WordPress: **the new system must make it cheap to add vendor
number 6, 10, 25.**

## 0.2 Why rebuild

Structural problems that emerged from the audits (details in the domain documents):

1. **The business core is already custom.** VAT (`legacy-vendor-tax-override`), shipping and quotes
   (`legacy-multicarrier-shipping`), SKU slugs and redirects (`legacy-sku-permalink`), ~20 mu-plugins.
   WooCommerce and WCFM act as a "chassis", but they constantly have to be worked around.
2. **Structural WCFM bugs in commissions**: `tax_enable` flag read with `isset()`; transaction
   charge calculated on the net amount; wrong frozen percentages (cases REGOLA-ZERO, PERC-CONGELATA, FEE-NETTO in [04](04-vendors-commissions-ledger.md)). Payouts
   depend on a ledger we do not control.
3. **Duplicate data representation**: custom-plugin zones and WCFM zones that must be kept in sync; EN and IT products
   as separate WPML posts, with many IT products ending up in EN categories; per-store taxonomy cache
   populated only in EN.
4. **Heavy operations**: 132 PHP routes (~35% one-off fixes or diagnostics), dozens of
   `fix_*` / `diagnose_*` scripts uploaded to the servers, cross-DB STAGE→LIVE cloning, blocked WPML
   premium updates, WCFM tested only up to WP 7.0.7.
5. **Compliance gaps**: no GPSR fields; publishing VAT indistinguishable from export and reverse
   charge (same `zero-rate` class); EU special territories not handled.

## 0.3 Principles of the new system ("less burdensome to run")

| # | Principle | Practical consequence |
|---|---|---|
| P1 | **A single source of truth** for every piece of data | One product with translations (not two posts); shipping zones in one place; our own ledger |
| P2 | **Business logic in pure, tested functions** | `resolve_tax()`, `quote_shipping()`, `compute_commission()` are functions without I/O, covered by the regression cases from the audits |
| P3 | **Few components in production** | Django + PostgreSQL + object storage. No Redis, Celery or Elasticsearch until they are needed: queues and search live on Postgres |
| P4 | **Every operation is a versioned command with dry-run** | The `fix_*.php` scripts become management commands or MCP tools with a diff preview and audit |
| P5 | **Configuration, not constants** | Rates, platform country, thresholds, plans, bands: all in the DB with time validity |
| P6 | **Immutable snapshots on orders** | Prices, rates, shipping and commission frozen at order time; recalculations only explicit and tracked |
| P7 | **Parity first, new features later** | Cutover only at P1 functional parity; promotions, reviews, affiliation and the like come later |
| P8 | **Adding a vendor = data, not code** | Onboarding via form + mapping + standard import adapters (YAML / CSV / Store API / scraping) |
| P9 | **Vertical-independent** | No industry concept in the code: taxonomies, attributes, navigation pages and catalog rules are configuration. The same system must be able to serve different markets (e.g. flowers, sewing supplies) |

## 0.4 Review of the August plan (2026-08-17)

The August plan remains valid as a **risk map** (tax, split payment, compliance,
security). It does, however, need a thorough review on seven points.

| # | August plan | Review (November 2026) | Reason |
|---|---|---|---|
| R1 | Scope only, "no implementation details" | This document defines **models, services, function signatures and algorithms** | Needed to derive estimable, verifiable tasks |
| R2 | Wagtail at the center of the system, commerce "to be decided" | **Django is the center**, with a lean custom commerce layer; Wagtail only for content and staff backoffice (see [01](01-stack-and-architecture.md)) | The hard domain (VAT, shipping, quotes, split) is custom anyway; generic commerce frameworks add weight without removing work |
| R3 | Celery/Redis, Sentry, dedicated queue | **Queues and cron on PostgreSQL** (Django task framework + DB worker), Postgres full-text and trigram search | Principle P3: one less service to run |
| R4 | Full vendor dashboard (WCFM replacement) | **Minimal dashboard**: orders, quotes, payouts, tax change requests. The catalog comes mostly from the pipelines | Today vendors only use the dashboard, products, orders and the quote panel; onboarding is manual |
| R5 | Membership, affiliate, staff as modules | Plans = **commission rules and limits** (no subscription billing at launch); affiliate and staff postponed | Affiliate and Groups&Staffs are not in use; the advertised plans do not govern the real vendors (plan 2241 "Special Free") |
| R6 | Missing EAA, online withdrawal, agent commerce | Added: **European Accessibility Act**, **online withdrawal function** (EU dir. 2023/2673), **GPSR as P1**, **MCP / agent commerce** channel (see [11](11-compliance.md), [10](10-content-seo-ai.md)) | Regulatory and market developments 2025–2026 |
| R7 | Estimate 1,740–2,540 h, AI −15–20% | **Revised estimate** with reduced scope and agentic development: see [91-estimates.md](91-estimates.md) | Leaner scope (R3–R5) and a mature AI workflow; the tax and payment parts remain under human review |

Other factual corrections to the August plan:

- There are **5 vendor profiles**, not 4: Vendor C exists, but has no products.
- The Stripe split **does not work for everyone**. Orders paid with `stripe_cc` are not split
  (orphaned vendor credits), and Vendor E is not connected to Stripe Connect.
- The Google feed `g:id` is the **WordPress post ID**: it must be preserved (`legacy_wp_id`), otherwise
  the history in Merchant Center is lost.
- Some **immediate security actions** on the current system have emerged, independent of the
  rebuild (restricted list, see [12](12-security-infra-operations.md) §12.1).

## 0.5 Out of scope (declared)

- Custom graphics and UX beyond a clean, accessible theme (can be estimated separately).
- Tax and legal advice: the open questions are collected in [92-open-decisions.md](92-open-decisions.md).
- Retroactive tax reprocessing of historical orders: migrated read-only.
- WCFM features not in use: Analytics, Groups&Staffs, Delivery, Product Hub, Elementor.
