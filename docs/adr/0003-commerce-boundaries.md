# ADR-0003: Commerce boundaries (build vs buy)

- **Status:** Accepted
- **Date:** 2026-11-07
- **Deciders:** project owner, tech lead
- **Backlog:** ARC.2

## Context

[ADR-0001](0001-stack.md) chose a lean custom commerce core. To keep it lean, we must say clearly what
we write ourselves and what we delegate. Anything we write, we also maintain, test and secure.

## Decision

**We build** (domain logic that is our differentiator, or that no product models the way we need):

- Catalog with navigation taxonomies, variants and translations (spec 03)
- Tax engine for per-vendor VAT regimes, OSS and special territories (spec 05)
- Shipping zones, weight bands and the shipping quote flow (spec 06)
- Cart, multi-vendor orders and sub-orders, order snapshots and the state machine (spec 07)
- Commission rules, append-only ledger, reconciliation and payouts (spec 04)
- Supplier import adapters and staging (spec 08)

**We delegate** (commodity, regulated, or operationally heavy):

| Concern | Delegated to | Our side |
|---|---|---|
| Card and wallet payments, KYC, payouts | Stripe Connect (Express) | `PaymentGateway` protocol and webhooks |
| E-invoicing transmission | Accredited SDI intermediary | `EInvoiceChannel` interface |
| Transactional email delivery | EU email provider API | Templates and events |
| Cookie consent | Certified CMP (D-10) | Consent Mode wiring |
| VAT number validation | VIES | Cached `VatValidation` records |
| Antibot | Cloudflare Turnstile | Server-side verification |
| CMS, media renditions, staff admin | Wagtail | Page types, snippets, ViewSets |

**We do not build** (out of scope until a decision says otherwise): subscription billing, affiliate
programs, a marketplace-wide promotions engine, a native mobile app, or a search engine beyond
PostgreSQL full-text and trigram search.

## Consequences

- Each delegated concern sits behind an interface we own, so we can switch providers without
  touching domain code.
- Out-of-scope items need a new ADR before any code is written.
