# ADR-0001: Application stack

- **Status:** Accepted
- **Date:** 2026-11-07
- **Deciders:** project owner, tech lead
- **Backlog:** ARC.1 · **Closes:** D-11 (Django version line); D-12 is closed by [ADR-0005](0005-hosting.md)

## Context

We are replacing the WordPress + WooCommerce + WCFM + WPML marketplace with a Python system that
is cheaper to run. The hard parts of the domain are custom in every option: per-vendor VAT,
shipping by zone and weight, the quote flow, and split payments. Full analysis:
[spec 01](../spec/01-stack-and-architecture.md).

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

- Python 3.13+, **Django 6.1** now, upgrading to **6.2 LTS** (due April 2027) before go-live (D-11).
  **Wagtail 8.x**, the current release that supports Django 6.1; we follow Wagtail releases until the
  next LTS that supports Django 6.2.
- PostgreSQL 16+ is the only datastore: data, task queue (django-tasks DB backend or Procrastinate;
  decided in [ADR-0004](0004-task-queue.md)), and full-text/trigram search.
- django-ninja for `/api/v1`; Django templates + HTMX + minimal Alpine.js on the frontend.
- Stripe Connect Express using separate charges and transfers, behind a `PaymentGateway` protocol.
- A modular monolith with the app layout and import rules in spec 01 §1.3, enforced by import-linter.
- OCI images; local development runs on Apple `container` (see [ADR-0002](0002-local-containers-apple.md)).

Exact versions are pinned in `uv.lock` (created by ARC.3), which is the source of truth for versions.
Versions checked on 2026-11-07: Django 6.1.3, Wagtail 8.1, django-ninja 1.7, psycopg 3.3.

## Consequences

- We write and own cart, orders, payments and ledger. This is the smaller part of the domain, and
  it is fully testable.
- No Redis, Celery or Elasticsearch to operate. Postgres capacity and backups become critical
  (OPS tasks).
- Wagtail stays at the edges (pages, media, admin). A Wagtail upgrade cannot break checkout.
- Revisit this ADR if the vendor count or traffic grows by an order of magnitude, or if a
  multivendor commerce framework becomes viable.
