# 92 — Open decisions to approve

Every decision has a default **proposal**. Items marked ⚖️ require the opinion of the tax advisor or
legal counsel. Once made, the decision is recorded as an ADR in the repository and the row is marked ✅.

| ID | Area | Question | Proposal | Decided by | Blocks |
|---|---|---|---|---|---|
| D-01 ⚖️ | VAT | Is the B2C country of taxation the country of **delivery** (EU distance-selling rule) or of billing (as today)? | Delivery for B2C; billing only to validate B2B | Tax advisor | TAX.3 |
| D-02 ⚖️ | VAT | Northern Ireland (`XI`): EU goods rules (Windsor Framework) or non-EU as today? | EU goods rules for `BT*` | Tax advisor | TAX.7 |
| D-03 ⚖️ | VAT | Does the carrier flag `force_vat` (vendor-country VAT on shipping even for exports or exempt sales) have a legal basis? | Remove it | Tax advisor | TAX.4 |
| D-04 | Business | Are paid-plan fees invoiced from day 1? | No: at launch plans are only commission rules and limits; billing in phase 2 | Owner | VEN.2 |
| D-05 | Business | On a full refund, does the platform return the commission **and** the payment fee? And on a partial refund? | Full: both; partial: proportional | Owner | PAY.4 |
| D-06 | Shipping | Weight above the last band: last band (today), quote or block? | Quote | Owner | SHP.3 |
| D-07 | SEO | Do the tag pages `/tag-product/{slug}/` become `/{taxonomy}/{term}/` with a 301, or stay identical? | New descriptive URLs with 301 (everything else unchanged) | Owner + SEO | CAT.8, MIG.6 |
| D-08 ⚖️ | Payments | With *separate charges and transfers* funds pass through the platform's Stripe balance: is a PSD2 check needed? | Check with the advisor; transfers immediately on payment confirmation | Legal counsel | PAY.1 |
| D-09 | Payments | Payment methods beyond cards, Apple Pay and Google Pay (SEPA, PayPal via Stripe, Klarna)? | Only cards and wallets at launch; others after checking compatibility with transfers | Owner | PAY.1 |
| D-10 | Privacy | Consent management: certified external CMP or self-hosted solution? | Certified external CMP (less maintenance) | Owner | SEO.5 |
| D-11 ✅ | Technical | Django 6.x moving to 6.2 LTS before go-live, or 5.2 LTS right away? | **Decided:** Django 6.1 now, 6.2 LTS before go-live ([ADR-0001](../adr/0001-stack.md)) | Tech lead | ARC.1 |
| D-12 ✅ | Technical | Self-managed VPS or EU PaaS with managed Postgres? | **Decided (2026-11-07):** Hetzner Cloud (EU, x86): app VPS with Podman Quadlets, self-managed PostgreSQL 17 on a dedicated database VPS on the private network, PITR with pgBackRest to Hetzner Object Storage ([ADR-0007](../adr/0007-hosting-hetzner-self-managed-postgres.md), supersedes ADR-0005) | Tech lead | ARC.5 |
| D-13 | Technical | AI provider for enrichment: do we stay on Gemini? | Yes, behind `TextEnricher`; the provider is configurable | Tech lead | ING.5 |
| D-14 | Product | Can pipeline-fed vendors edit products manually from the dashboard? | Only "local" fields (revised copy, cover); price and stock remain with the supplier | Owner | CORE.9 |
| D-15 ⚖️ | Compliance | Is the platform a microenterprise for EAA purposes? | Design for WCAG 2.2 AA (EN 301 549 V4.1.1) regardless | Legal counsel | CMP.7 |
| D-16 | Migration | Historical orders: all, or only the last 24 months? Customers without orders? | All orders (read-only); customers with no orders in 3 years excluded | Owner + DPO | MIG.4 |
| D-17 | Business | Affiliation, ambassadors and club/community referrals: when? | After go-live, as tracked coupons or referral codes | Owner | — |
| D-18 ✅ | Business | Which languages at go-live, and for which texts? | **Decided (2026-11-07, revised the same day):** five languages at go-live in this order: EN (primary and source), IT, DE, FR, ES. Interface text is translated into all five at go-live (AI draft, human review of checkout, emails and legal/tax wording; I18N.7). Visitor content stays EN only until after go-live, with the explicit fallback (EN canonical, no hreflang for missing translations); the owner then decides how to translate it (I18N.8). Images are language neutral unless dedicated to one language. Supersedes the earlier "German content at go-live" decision. Open: whether legal pages count as visitor content | Owner | I18N.1, I18N.7, I18N.8 |
| D-19 | Operations | Vendor E without Stripe Connect: catalog on request until they connect? | Yes (`sales_mode=on_request`) | Owner | VEN.6 |
| D-20 | Migration | Quotes open at cutover time: closed beforehand or migrated? | Closed beforehand (7-day freeze on new requests) | Owner | MIG.9 |
