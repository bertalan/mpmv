# MPMV — Rebuilding a multivendor marketplace in Python

Functional and technical specification for replacing the WordPress + WooCommerce + WCFM + WPML stack
with a **Django + Wagtail** system that is simpler to run.

The document was derived from an analysis of the current code: custom VAT and shipping plugins,
mu-plugins, the legacy API routes, Python import pipelines, audit reports and LIVE configurations.
It also reviews the August 2026 rebuild plan.

## How to read this spec

> **Public version.** Vendors are pseudonymized (A–E), reference orders are identified by case
> name (e.g. FEE-NETTO) and security details of the current system are omitted. Identifiers from
> the current system (plugins, classes, meta) use the generic `legacy` prefix. The full version and the
> pseudonym map are in `private/`, outside git.

- Every domain document has the same structure:
  1. **How it works today**: classes, functions and data of the WordPress system, with known bugs.
  2. **How it will work**: models (Django classes), services and pure functions with their signatures, algorithms, rules.
  3. **Regression cases**: historical bugs that become mandatory tests (where relevant).
  4. **Tasks**: micro-tasks with ID, priority, hours, dependencies and a "done" criterion.
- Macro-task = ID prefix (e.g. `TAX` = tax engine). Micro-task = row (e.g. `TAX.3`).
- Priority: **P1** required for go-live · **P2** right after · **P3** later evolution.
- The full backlog ([90-backlog.md](90-backlog.md) and `backlog.csv`) is **generated** from the documents'
  tables: `make docs` (or `uv run tools/build_docs.py`). The script also checks for unique IDs,
  existing dependencies and absence of cycles. The same command regenerates `build/mpmv-spec.html`
  (not under version control); `make docs-check` flags an out-of-date backlog.

## Contents

| # | Document | Content |
|---|---|---|
| 00 | [Vision and review](00-vision-and-review.md) | Idea, principles, delta from the August plan |
| 01 | [Stack and architecture](01-stack-and-architecture.md) | Wagtail and alternatives compared, modular monolith, conventions |
| 02 | [Identity, permissions, API, backoffice](02-core-identity-permissions.md) | Roles, vendor isolation, API v1 replacing the legacy custom API |
| 03 | [Catalog](03-catalog.md) | Products, variants, navigation taxonomies, SKU slugs, redirects, search |
| 04 | [Vendors, commissions, ledger](04-vendors-commissions-ledger.md) | Plans, commission rules, ledger, payouts |
| 05 | [Tax engine](05-tax.md) | VAT regimes, decision tree, special territories, tests |
| 06 | [Shipping and quotes](06-shipping-and-quotes.md) | Zones, weight bands, postcodes, quote flow |
| 07 | [Cart, orders, payments](07-cart-orders-payments.md) | Vendor sub-orders, Stripe Connect, refunds, withdrawal |
| 08 | [Import and pipelines](08-import-pipelines.md) | Reuse of the Python pipelines, per-vendor adapters, jobs |
| 09 | [Multilingual](09-multilingual.md) | Model translations, wagtail-localize, workflow |
| 10 | [Content, SEO, AI channels](10-content-seo-ai.md) | Wagtail pages, JSON-LD, feeds, MCP |
| 11 | [Compliance](11-compliance.md) | DAC7, GPSR, GDPR, DSA, online withdrawal, EAA |
| 12 | [Security, infrastructure, operations](12-security-infra-operations.md) | Immediate actions, requirements, deploy, backups |
| 13 | [Migration and cutover](13-migration-cutover.md) | ETL, parity, go-live and rollback runbook |
| 14 | [Frontend themes](14-frontend-themes.md) | Theme system, design tokens, CSP, WCAG 2.2 AA, first vertical theme |
| 15 | [Events](15-events.md) | Event pages (promotions, trade fairs and meetings) in any site language, moderation, RSS and ICS feeds per language, maps and geocoding |
| 16 | [Trust and community](16-trust-community.md) | Proposal D-37: vendor trust badges, customer reviews, buyer consent, social and blog section (under review) |
| 17 | [Garage and fitment](17-garage-fitment.md) | D-38 (decided, P1 in slices): the customer's garage (fitment profiles), a sector-neutral fitment taxonomy, editorial make and model articles, filter over ranking, EU rules |
| 90 | [Backlog](90-backlog.md) | All micro-tasks by phase (generated) |
| 91 | [Estimates and plan](91-estimates.md) | Hours, schedule, phases, external costs, measured agent velocity |
| 92 | [Open decisions](92-open-decisions.md) | 21 decisions with proposals (D-11, D-12 and D-18 decided) |

## Summary

- **Recommended stack**: Django (6.x → 6.2 LTS) + a lean custom commerce layer + Wagtail for content and
  backoffice, PostgreSQL as the single datastore (also for queues and search), HTMX, Stripe Connect.
- **Main simplifications**: a single entity per product (not two EN/IT posts), a single source for
  shipping, our own ledger and commissions, import pipelines in the same project, no operational
  scripts outside version control.
- **Go-live scope**: 174 P1 micro-tasks, **≈ 1,873–2,098 h** including acceptance testing, review and contingency
  ([91](91-estimates.md) §91.2).
  The August plan estimated 1,740–2,540 h.
- **Do immediately**, regardless of the rebuild: the confidential security actions on the current
  system (see [12](12-security-infra-operations.md) §12.1).

## Approval checklist

- [ ] 00 Vision and principles
- [ ] 01 Stack (decisions D-11, D-12)
- [ ] 02–15 Domain documents (one tick per document)
- [ ] 92 Open decisions: proposals accepted or amended
- [ ] 91 P1 scope and schedule
- [ ] Import of `backlog.csv` into the task management tool
