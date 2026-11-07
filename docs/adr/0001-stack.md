# ADR-0001: Application stack

- **Status:** Proposed (awaiting approval of the spec and decisions D-11, D-12)
- **Date:** 2026-11-07
- **Deciders:** project owner, tech lead
- **Backlog:** ARC.1 · **Open decisions:** D-11 (Django version line), D-12 (hosting model)

## Context

We are replacing the WordPress + WooCommerce + WCFM + WPML marketplace with a Python system that
is cheaper to run. The hard parts of the domain are custom in every option: per-vendor VAT,
shipping by zone and weight, the quote flow, and split payments. Full analysis:
[spec 01](../spec/01-stack-e-architettura.md).

## Options considered

1. **Django + lean custom commerce core + Wagtail for content and staff backoffice**: one language,
   one deploy, and we own the domain model.
2. **Saleor**: complete headless commerce, but no native multivendor. It needs a separate JS
   frontend (two stacks) plus Celery and Redis.
3. **django-oscar**: customization means forking apps, its multivendor support is partial, and
   tax and shipping would be rewritten anyway.
4. **Wagtail-centric (August 2026 plan)**: modelling products and orders as pages couples the
   domain to the CMS.

## Decision

Option 1:

- Python 3.13+, Django 6.x, moving to 6.2 LTS before go-live (D-11), with Wagtail (latest LTS).
- PostgreSQL 16+ is the only datastore: data, task queue (django-tasks DB backend or Procrastinate;
  decided in ADR-0004, ARC.2), and full-text/trigram search.
- django-ninja for `/api/v1`; Django templates + HTMX + minimal Alpine.js on the frontend.
- Stripe Connect Express using separate charges and transfers, behind a `PaymentGateway` protocol.
- A modular monolith with the app layout and import rules in spec 01 §1.3, enforced by import-linter.
- OCI images; local development runs on Apple `container` (see [ADR-0002](0002-local-containers-apple.md)).

Exact versions are pinned in the lockfile created by ARC.3. Once this ADR is accepted, the lockfile
is the source of truth for versions.

## Consequences

- We write and own cart, orders, payments and ledger. This is the smaller part of the domain, and
  it is fully testable.
- No Redis, Celery or Elasticsearch to operate. Postgres capacity and backups become critical
  (OPS tasks).
- Wagtail stays at the edges (pages, media, admin). A Wagtail upgrade cannot break checkout.
- Revisit this ADR if the vendor count or traffic grows by an order of magnitude, or if a
  multivendor commerce framework becomes viable.
