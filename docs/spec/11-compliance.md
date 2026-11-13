# 11 — Marketplace regulatory compliance

> This document lists **product requirements** to implement. It is not legal advice: every
> item marked ⚖️ must be confirmed with a tax advisor or legal counsel (see [92-open-decisions.md](92-open-decisions.md)).

## 11.1 Current state

| Obligation | Today |
|---|---|
| DAC7 (vendor revenue reporting) | Data scattered across WCFM meta; no structured export |
| GPSR (product safety, since 2024-12-13) | **No fields** |
| E-invoicing of commissions | Outside the system |
| GDPR | Complianz for cookies; no export/erasure flow; LIVE dumps copied to STAGE |
| DSA (notices, transparency) | No dedicated flow |
| P2B (transparency towards vendors) | Terms in static pages |
| Omnibus (lowest price in 30 days, reviews) | Not handled (no structured promotions) |
| European Accessibility Act (since 2025-06-28) | Not assessed |
| Online withdrawal function (from 2026-06-19) | **Missing** |

## 11.2 Requirements

### Principle: non-fiscal documents only, platform duties are reporting and verification
Owner decision of 2026-11-07 (see [00](00-vision-and-review.md)). The marketplace issues no
invoices and keeps no bookkeeping, neither for the operator nor for vendors. It issues only
non-fiscal documents: per-vendor order confirmations for customers (ORD.9), commission statements
(CMP.2), payout statements and ledger extracts (VEN.9). Each one, as PDF and as data export, is
labelled "Documento non fiscale / Non-fiscal document" in all five interface languages, never uses
an invoice numbering sequence, and has a stable reference ID that the external invoice can cite.

The platform keeps its own legal duties, which are reporting and verification, not accounting:
DAC7 reporting (CMP.1), DSA notice and action and trader traceability (CMP.5, CMP.11), the GPSR
marketplace duties (CMP.3), the VAT records of facilitated supplies (CMP.12), and the marketplace
checks of the Batteries Regulation, PPWR and national EPR laws (CMP.11). Obligations that belong
to the vendor are listed in the vendor obligations checklist and confirmed by the vendor under its
own responsibility (CMP.10).

### CMP.1 — DAC7 `[P1]`
- Dataset: owned by VEN.12 (see [04](04-vendors-commissions-ledger.md)). The vendor's legal
  identity holds the DAC7 fields, the seeded `LegalFieldRequirement` rows require them for a
  payout for every vendor, non-EU sellers included, and `engine.dac7_missing()` reports what is
  missing. CMP.1 does not collect or validate them again; it reads them.
- Aggregation per vendor and quarter from the ledger (consideration, commissions, number of transactions).
- `Consideration` (T-6, adviser opinion 2026-11-11) is the amount paid or credited to the vendor **net of the
  platform's fees and taxes**; the fees go in the separate `Fees` field. On a 100 order with a 10
  coupon and platform fees of 15 before the coupon: a vendor coupon gives Consideration 75 and Fees
  15; a Q-90 platform coupon (commission reduced by the coupon) gives Consideration 85 and Fees 5.
- Annual export in the format of the authority of the **platform's country of establishment**
  (`PlatformOperator`). For Italy (legislative decree 32/2023): XML schema DPI 1.0 sent to the
  Agenzia delle Entrate by 31 January of the following year, and the same information given to
  each reported vendor by the same date.
- Excluded sellers (directive (EU) 2021/514, verified 2026-11): a vendor of goods with fewer than
  30 sales **and** total consideration of €2,000 or less in the year is not reported. Reportable
  sellers are EU residents; non-EU vendors' data is collected anyway (owner decision) but not
  reported for goods.
- DAC7 is reporting, not accounting: the aggregates are read from the operational ledger.

### CMP.2 — Commission statement and data export `[P1]`
- Owner decision of 2026-11-07: the platform issues no commission invoices and has no e-invoicing
  channel. CMP.2 produces a **commission statement** (a pro-forma) per vendor and period from the
  operational ledger: commissions, payment fees, refunds and reversals, with the VEN.12 party data
  of the operator and of the vendor.
- The statement is a PDF per vendor language plus a data export (CSV and a structured JSON whose
  party block maps one to one to FatturaPA `CedentePrestatore`/`CessionarioCommittente` fields), so
  the operator's accountant or invoicing tool issues the invoice outside the platform. Statement
  and export are labelled "Documento non fiscale / Non-fiscal document, not an invoice" in all five
  languages, carry a stable reference ID (never an invoice number sequence), and show the
  commission VAT mode of the `PlatformOperator` only as information.
- Invoices and receipts to customers are the vendor's (ORD.9 sends a non-fiscal order
  confirmation). ⚖️ The vendor contract states the boundary.
- Statement lines (adviser opinion 2026-11-11): the refund fee is a platform service with the commission's VAT
  treatment, on a line of its own (T-8). Commission given back before the period is invoiced shows
  as the gross commission line plus a negative "commission on refunded orders" line; after it is
  invoiced, either a credit note or a negative line on the next periodic statement referencing the
  earlier one. A platform coupon or platform-paid free shipping (Q-90 model, T-1 and T-5) reduces
  the commission; an excess over the order's commission is netted against the vendor's
  commissions of the period. The total of a period never goes below zero: an excess larger than
  the vendor's commissions of the whole period carries over to the next period as a vendor credit
  against future commissions, and no cash is paid out (owner, 2026-11-11).

### CMP.3 — GPSR `[P1]`
```python
class GpsrInfo(Model):                 # apps.compliance; product.gpsr (one-to-one)
    manufacturer_name
    manufacturer_address
    manufacturer_contact                # email or http(s) web address
    manufacturer_country                # outside the EU: EU responsible person mandatory
    eu_responsible_name
    eu_responsible_address
    eu_responsible_contact  # mandatory if the manufacturer is outside the EU
    product_identifiers(batch, model)   # plus Product.gtin and Product.mpn
    safety_documents(file)              # follow-up (private document storage, ARC.15)

class VendorGpsrDefaults(Model):        # same manufacturer and EU person blocks, per vendor
class GpsrSafetyText(Model):            # warnings and safety information, one row per language
class SafetyCase(Model):                # article 22: authority order or safety notice,
    kind; vendor; product; received_at; due_on; status; resolution; overdue_alerted_at
class CountryWarningLanguages(Model):   # platform data: country -> accepted warnings languages
```
- Vendor defaults fill a block (manufacturer, EU responsible person) the product leaves empty,
  one whole block at a time, never field by field. The catalog never imports compliance:
  compliance registers its check in `apps.catalog.publishing`, which the publishing services
  run before a product becomes published. A published product never silently loses its data: a
  product edit, a product GPSR write or a vendor defaults write that would remove mandatory data
  from a published product is refused (unpublish first), and a nightly job lists every published
  product that fails the check anyway (log error and one email a day to the staff alert
  address). Imports (MIG, CAT.10 pipelines) publish only through the catalog services, so the
  same checks apply.
- Working days are Monday to Friday; the day of receipt is day 0 and counts in business time
  (`BUSINESS_TIME_ZONE`). A receipt time in the future is refused. Public holidays are not skipped, which gives the earliest deadline (owner
  decision 2026-11-09); the engine keeps a holiday set, empty unless the
  `COMPLIANCE_PUBLIC_HOLIDAYS` setting lists dates.
- Visible on the product page with at least one product picture (article 19); **publication
  blocked** if mandatory fields are missing; the import contract requires them. Per-vendor defaults
  (the manufacturer often is the vendor). Warnings and safety information in the language of the
  destination country (owner decision 2026-11-09): when a product has warnings, sale into an EU or
  EEA country is blocked until the warnings exist in at least one of that country's accepted
  languages, so one German text unblocks DE, AT and LU at once. The country -> accepted languages
  map is platform data (`CountryWarningLanguages`), seeded from the official languages of the EU
  and EEA states and edited by staff through an audited service and the admin; the engine
  receives it as input and the services cache it per process (dropped on edit). The product stays
  published and on sale for the other countries. The check is registered in
  `apps.catalog.selling`, which the checkout calls per line; the product page and the checkout
  show a notice for a blocked country with links to the warnings in the languages that exist, and
  the vendor sees the countries the product is not sold into, with the languages that would
  unblock each. Whenever warnings exist the product page shows them: in the page language, else
  a language the destination accepts, else the primary language, else any, with a label saying
  which language it is.
- An overdue authority order or safety notice alerts staff once: an error in the log, an audit
  entry, an email to the operator's compliance address (else its support address) through the
  transactional outbox, and an in-app notice for the Catalog and moderation role (CMP.26).
- Online marketplace duties of GPSR article 22, which apply whatever the platform's size: register
  on the Safety Gate portal with a single point of contact for authorities, and give consumers a
  single point of contact; act on an authority order without undue delay and within **2 working
  days**; handle a product safety notice (DSA article 16 channel, CMP.5) within **3 working days**;
  check Safety Gate alerts and take them into account; collect the trader's self-certification
  (CMP.10, CMP.11); suspend repeat offenders after a warning; cooperate on recalls, including
  contacting buyers.
- Rapid recall or removal procedure upon notice from the authorities or a safety report, with the
  working-day deadlines tracked per notice.

### CMP.4 — Operational GDPR `[P1]`
- Customer data export (JSON/PDF) and erasure (owner decision D-29, see "Retention per data
  type" below). A customer erasure anonymises the login profile and the non-fiscal data (carts,
  preferences, logs) and unsubscribes the user. It never touches concluded orders: the order
  snapshots stay unchanged until their retention period ends (GDPR article 17(3)(b)), the same
  restriction model as Q-16 for vendor owners. The user is told, at the request and in the
  confirmation, that the order data is kept for the legal period. The flow is CMP.30: a web
  flow in the customer area (not the vendor API) with a POST confirm and re-authentication;
  guests use a verified-email request. A request with open orders is accepted at once and
  completes when the last one closes (owner decision D-30, below).
- Vendor owners (owner decision Q-16, [02](02-core-identity-permissions.md)): an owner is a
  contractual party, so the owner membership is kept and the last owner is never removed. An
  erasure request from a closed vendor's owner restricts processing (GDPR article 18); data that
  the law requires us to keep stay for their retention period (GDPR article 17(3)(b), and
  17(3)(e) for contract evidence): contract and signature evidence, tax and DAC7 records, ledger
  entries and order records. Only data not covered by a retention duty is deleted or anonymised
  at once; the retention job anonymises the retained data when its period ends.
- Record of processing activities and sub-processors (Stripe, carriers, email provider, AI provider, hosting, geocoding providers and map tile upstream of [15](15-events.md)),
  retention per data type, DPA.
- No tracking pixels in emails without consent (Garante privacy, provision 284 of 2026-04-17,
  compliance from about 2026-10-29): transactional and service emails (ARC.16, CMP.10 reminders)
  carry no open-tracking pixel; marketing emails carry one only with consent, revocable on its own.
- Non-production environments only with synthetic or anonymized data (today LIVE is cloned to STAGE).
- Cookie consent with Consent Mode (see [10](10-content-seo-ai.md)).

### Order data retention and archive (owner decision D-29)
- Legal basis: GDPR article 6(1)(c) and 17(3)(b); VAT directive 2006/112/EC article 242a;
  regulation (EU) 282/2011 article 54c; Italian civil code article 2220; DPR 633/72 article 39.
- **Every order record is kept 10 years from the end of the calendar year of the order**, B2C and
  B2B alike. The fiscal snapshot fields (name or company, VAT and tax ids, billing and shipping
  addresses, email, goods description, VIES log with time and consultation number, proof of
  dispatch) are immutable for that period: the ORD.2 trigger allows no rewrite.
- The only way to change a retained order is the ORD.2 scrub service (`AuditLog`, `dry_run`):
  (a) early minimisation after the active phase (default 24 months, configurable) drops the
  non-fiscal fields, such as the carrier contact phone and support notes; (b) final
  anonymisation on 31 December of year 10 replaces the personal fields with fixed placeholders
  or hashes and keeps amount, country and rate. Both run as scheduled jobs (CMP.29).
- Archive phase (the `restricted_tax_only` state, from the end of the active phase to year 10):
  masked in the ordinary vendor and support UI; the personal fields are visible only to the
  vendor owner, the vendor "accounting" role and the platform Tax/Audit permission, and every
  access is audited (ORD.14).
- **Order privacy lifecycle (owner decision D-30)**, driven by `Order.privacy_status` and
  `Order.tax_retention_until` ([07](07-cart-orders-payments.md)):
  - `active`: from order build until 24 months (configurable) or an account erasure, whichever
    comes first.
  - `restricted_tax_only`: personal fields are masked (for example `j***@example.com`) in every
    ordinary UI and API. In clear only for the vendor owner, a vendor role "accounting"
    (assigned by the owner) and the platform Tax/Audit permission (a CORE.18 role). Every
    in-clear view writes an `AuditLog` entry with actor, order, time and reason. Non-fiscal
    fields (carrier phone, delivery notes) are dropped on entry.
  - `anonymized`: from the day after `tax_retention_until` in the business time zone (D-31),
    or at once for orders never paid (draft, payment failed). Personal fields get fixed
    placeholders; transport-proof files that hold names or signatures are deleted from the
    private bucket; amounts, countries, dates, rates and nature codes stay, so the ledger,
    payouts and the CMP.12 records stay consistent. The run is audited.
  - The vendor's fiscal evidence package (order snapshot, VIES log, transport proofs) is
    downloadable while the order is `active` or `restricted_tax_only` (ORD.15). The platform
    issues no invoices, so there are none in it.
  - Account erasure with open orders (in shipment, return or withdrawal window, open
    dispute): the request is accepted at once, never refused with an error (GDPR answers are due
    within a month). Login is blocked, marketing removed, saved carts, addresses and payment
    methods deleted; the rest completes automatically when the last open order closes and the
    user is told so. Concluded orders go to `restricted_tax_only` at once. The confirmation
    email is sent before the address is removed, with the article 17(3)(b) wording as a
    translatable template. Third parties (email provider, any CRM or analytics added later) are
    revoked.
  - Legacy orders (MIG.4): older than 10 years are imported already `anonymized`, between 2 and
    10 years `restricted_tax_only`, under 2 years `active`; the dates come from the original
    order date.
- Payment data: never a full card number (Stripe holds it); the issuer country or IP country is
  kept only as location evidence (article 24b of regulation (EU) 282/2011).
- The privacy policy states the period (CMP.31).

#### Retention per data type

| Data | Legal basis | Period | End action |
|---|---|---|---|
| Order snapshot (`Order`, `VendorOrder`, `OrderLine`: amounts, country, rate, nature code, commission rule) | GDPR 6(1)(c), 17(3)(b); c.c. art. 2220 | 10 years from the end of the calendar year of the order (`tax_retention_until`); `active`, then `restricted_tax_only`, then `anonymized` | Personal fields anonymised from the day after `tax_retention_until`, at once for never-paid orders; amounts, countries, dates, rates and nature codes kept |
| Invoice and fiscal fields (name or company, VAT and tax ids, billing and shipping addresses, email, goods description) | VAT directive art. 242a; DPR 633/72 art. 39; c.c. art. 2220 | 10 years from the end of the calendar year; immutable | Final anonymisation (placeholders or hashes) |
| VIES log (consultation time, number and the name VIES returned) | Reg. 282/2011 art. 54c; DPR 633/72 art. 39 | 10 years from the end of the calendar year of the order | Final anonymisation |
| Proof of dispatch (place of dispatch and arrival, carrier reference; transport-proof files in the private bucket) | Reg. 282/2011 art. 54c; VAT directive art. 242a | 10 years from the end of the calendar year of the order | Fields anonymised; files with names or signatures deleted |
| Non-fiscal order fields (carrier contact phone, support notes) | Contract performance, then none | Active phase, 24 months after the order (configurable) or until an account erasure | Dropped on entry to `restricted_tax_only` |
| Account profile (login, name, phone, language, MFA) | GDPR 6(1)(b) | While the account exists | Anonymised on erasure (at once, or when the last open order closes); the order snapshots are untouched except for the move to `restricted_tax_only` |
| Carts, preferences, logs | GDPR 6(1)(b) and (f) | While the account exists; logs per the operations retention setting | Deleted on erasure, together with saved addresses and payment methods |
| Contract evidence (vendor contracts, signatures, timestamp tokens) | GDPR 17(3)(e); limitation period | Whole relationship plus 10 years (proposed default) | Files deleted by the retention job |
| CMP.12 records (art. 242a facilitated supplies) | VAT directive art. 242a; Reg. 282/2011 art. 54c | 10 years from the end of the year of the transaction | Deleted or anonymised by the retention job; outside erasure |
| Payment data | GDPR 6(1)(b) and (c) | Stripe holds the card; we keep the payment reference per the order snapshot, and the issuer or IP country only as location evidence for the order period | Anonymised or deleted with the order |

### CMP.5 — DSA and P2B `[P1]`
- DSA (regulation (EU) 2022/2065) duties of every hosting service and online platform, whatever
  its size, so P1 for go-live: points of contact for authorities and for users (articles 11, 12),
  terms with the content moderation rules (article 14), a form for reporting illegal content and
  unsafe products (notice and action, article 16) with tracking, a statement of reasons to the
  vendor for every restriction (article 17), and notification of suspected criminal offences
  (article 18). Notices can target products, vendor storefronts and event pages; hiding an event
  after a notice unpublishes it through the events service ([15](15-events.md), EVT.11).
  Part 1 (D-27): the authority contact languages are an official language of the operator's
  country plus English (article 11(3)); the platform terms and the ranking page exist in every
  configured language, and the terms carry tagged sections for restrictions, moderation policy,
  internal complaint handling (article 20) and out-of-court redress (article 21); `golive_readiness`
  checks them with the native and counsel reviews.
- Micro and small platforms are exempt from DSA articles 19–32 (articles 19 and 29) and from the
  transparency report (article 15(2)), until 12 months after they stop being small. The marketplace
  duties of articles 30–32 are still built at go-live (CMP.11), because GPSR article 22 and the
  Batteries and PPWR marketplace checks rely on them and the exemption ends as the platform grows.
- P2B (regulation (EU) 2019/1150): statement of reasons on a restriction or suspension and 30
  days' notice before termination (article 4), ranking parameters (article 5, dedicated page),
  any differentiated treatment (article 7) and the data access policy (article 9) described in the
  vendor terms. The internal complaint system and mediators (articles 11, 12) are not required for
  a small enterprise. Versioned vendor terms with tracked acceptance and 15 days' notice of changes
  are VEN.5 and VEN.13 to VEN.16.
  Part 4 (termination): staff give a vendor notice that its whole service ends; the notice carries
  the reasons (it is the statement of reasons) and takes effect at least 30 days later
  (`TERMINATION_NOTICE_DAYS`, never below 30; the database refuses less). Only an exception from a
  closed list (legal or regulatory obligation, repeated infringements of the terms, illegal
  content) allows a shorter notice. The vendor is emailed in its language (English fallback), keeps
  selling, sees the reason and can download its data during the notice; staff can withdraw the
  notice; an hourly job closes the vendor through the vendor closure service at the effective time,
  never earlier. Giving or withdrawing a notice is a group 2 session-level action
  (`termination_notice`, CORE.25) of the platform owner (read for catalog staff).

### Vendor contracts: language and acceptance evidence
- The vendor contract follows the vendor's language (EN, IT, DE, FR, ES), one text per language,
  and each version declares a prevailing language that wins in case of discrepancies; the clause
  is printed in every rendered PDF. Without a text in the vendor's language the vendor accepts the
  English text and explicitly acknowledges it. Details in [04](04-vendors-commissions-ledger.md).
- Acceptance online is a simple electronic signature under eIDAS (email one-time code plus MFA
  step-up), with the exact personalized PDF, its SHA-256 hash and an RFC 3161 timestamp.
- Double signature for onerous clauses (owner decision of 2026-11-07). Under articles 1341 comma
  2 and 1342 of the Italian civil code, clausole vessatorie in standard terms drafted by one party
  have no effect unless the other party approves them specifically in writing; this applies
  between businesses, so to vendor contracts. Templates and vendor clauses mark them (VEN.13), the
  PDF has a second signature block with the formula "Ai sensi e per gli effetti degli artt. 1341
  e 1342 c.c. si approvano specificamente le clausole n. ..." and the vendor approves them online
  in a second act, separate from the acceptance of the whole contract, with its own one-time
  code (VEN.14). The evidence of both acts is stored and timestamped.
- Completing the specific approval. The specific approval counts as completed only when the
  vendor signs with a qualified or advanced electronic signature (VEN.17) or sends the paper
  original signed by hand, checked and approved by staff on receipt (VEN.16). Until then a
  pending requirement with a deadline and a consequence applies, and the contract text warns the
  vendor about it. Staff can require a qualified signature per version or per vendor.
- ⚖️ Legal risk, recorded as it is (not legal advice). A simple electronic signature such as the
  email one-time code has an evidentiary value that the judge assesses freely (CAD, legislative
  decree 82/2005, article 20 comma 1-bis), and case law is not uniform on whether it satisfies the
  written specific approval of article 1341. A qualified or advanced electronic signature
  satisfies the written form under the same article, and so does the paper original signed by
  hand. The online record of both acts remains evidence of the vendor's intent, but until one of
  the two routes is complete the marked clauses may be held ineffective while the rest of the
  contract stands. The owner's legal counsel must confirm the approach, the list of marked
  clauses, the text of the clause that warns the vendor, and whether to exclude the paper route
  and make the qualified signature mandatory.
- Retention: the PDF, the evidence package, the timestamp tokens, any signed upload and the scan
  of a paper original are kept in
  the private bucket for the whole vendor relationship and then for the retention period (proposed
  default: 10 years after the relationship ends, the ordinary limitation period in Italy ⚖️). The
  signer's personal data in the evidence are kept on the basis of establishing and defending legal
  claims (GDPR article 17(3)(e)), so a CMP.4 erasure request does not delete them; the CMP.4
  retention job deletes the files when the period ends. Evidence files are append-only and are
  never rewritten. Paper originals are kept in the physical archive recorded by staff for the
  same period.
- Timestamps come from a TSA behind the `TimestampAuthority` interface; the choice between a free
  TSA and a qualified one is an owner decision.

### CMP.6 — Consumer rights `[P1]`
- **Online withdrawal function** (EU directive 2023/2673, amending directive 2011/83/EU, applies
  from 2026-06-19; Italy: article 54-bis of the Codice del Consumo, legislative decree 209/2025, GU
  2026-01-08): button "withdraw from contract here" (IT "recedere dal contratto qui") clearly
  visible for the entire withdrawal period, minimal data, confirmation "confirm withdrawal" (IT
  "conferma recesso"), acknowledgement on a durable medium with content, date and time; the
  pre-contractual information says where the function is (article 49(1)(h)). ⚖️ Counsel checks the
  label in DE, FR and ES against each destination country's transposition.
- Online marketplace information (article 49-bis Codice del Consumo, from the Omnibus directive):
  on each offer, whether the vendor is a trader (its own declaration, CMP.10), that consumer law does
  not apply when it is not, the ranking parameters, and how obligations are split between vendor
  and platform.
- Legal guarantee notice: the harmonised notice on the legal guarantee of conformity and, where a
  vendor offers one, the harmonised label for a commercial durability guarantee (directive (EU)
  2024/825, Implementing Regulation (EU) 2025/1960, both from 2026-09-27; Italy: legislative decree
  30/2026) shown in the pre-contractual information.
- Pre-contractual information per language; legal guarantee; 14-day withdrawal by consumer country.
- Geo-blocking: no discrimination based on residence in the countries served.
- Omnibus: strikethrough price = lowest price in the last 30 days (price history in `catalog`);
  information on how reviews are verified.
  - Decided (owner, 2026-11-11): a variant's own price override wins over the product's sale; a
    sale never reaches an overridden variant (a vendor who wants it discounted sets that variant's
    price separately). The reference price follows the same rule.
  - Decided (owner, 2026-11-11): history stores the list price as entered (net); the reference
    price is converted at display time with the same tax decision as the price shown.

### CMP.7 — European Accessibility Act `[P1]`
- E-commerce services fall under the EAA. Service **microenterprises** (fewer than 10 employees
  and turnover or balance sheet ≤ €2M) are exempt (D-15, decided: adviser opinion 2026-11-11), so a micro-enterprise
  operator is exempt; the platform keeps WCAG 2.2 AA anyway because non-micro vendors have their
  own duties, and the vendor terms make vendors provide accessible descriptions and attachments (CMP.5).
- Recommendation: design to **WCAG 2.2 AA (EN 301 549 V4.1.1)** anyway. It costs little if done
  from the start, it is a de facto requirement for growth, and vendors with more than 10 employees may
  ask for it. Published accessibility statement.
- Standard version: EN 301 549 V4.1.1 (published 2026-09-02) moves to WCAG 2.2 A/AA and maps to
  the EAA. It still awaits citation in the Official Journal (expected late 2026); until then V3.2.1
  (WCAG 2.1 AA) is the legal reference. WCAG 2.2 AA covers 2.1 AA (except the removed 4.1.1
  Parsing), so the 2.2 target meets both.
- Automated audit: axe-core with the `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa` and `wcag22aa`
  tags on the key pages, reusing the THM.5 harness; the manual review covers what axe cannot
  check (focus not obscured, consistent help, redundant entry, accessible authentication). Theme
  rules are in [14](14-frontend-themes.md) 14.6.

### CMP.8 — AI-generated content `[P1]`
- AI Act (regulation (EU) 2024/1689, article 50 from 2026-08-02, as amended by regulation (EU)
  2026/1744). The marking duty of article 50(2) falls on the AI provider. As deployer, the platform
  must disclose AI-generated or AI-edited realistic images that could falsely appear authentic
  (deep fakes, article 50(4)): a product image produced or altered by AI carries a visible "AI
  generated" or "AI edited" label and the same flag in its metadata. AI-written product text
  reviewed by a person under editorial responsibility needs no label; the origin is tracked in
  `ProductTranslation` (`machine` / `reviewed`), and unreviewed machine text is never published as
  if reviewed.
- AI literacy (article 4, from 2025-02-02, as amended): staff who review AI drafts get a short
  documented briefing on the tool's limits.
- Vendor texts follow the ECGT bans on generic environmental claims (directive (EU) 2024/825, from
  2026-09-27): the enrichment policy (ING.5) never generates them.

### CMP.10 — Vendor obligations checklist with self-attestation `[P1]`
Owner decisions of 2026-11-07. The platform does not fulfil the vendor's own legal obligations
and does not check them one by one. It tells each vendor which obligations apply to it and asks
the vendor to answer each one under its own responsibility; the record of what the platform told
the vendor, and when, is evidence that the vendor was informed.

- **Obligations are data.** `VendorObligation` rows form a versioned checklist edited by staff and
  audited: code, title and explanation in the five interface languages, legal basis, competent
  authority, applicability (vendor country, EU or non-EU, legal form, product categories,
  destination countries), reminder settings and an optional consequence. The legal wording of
  every obligation and of the reminder emails is marked (L): the team drafts it and legal counsel
  approves it through CMP.24 before it reaches production ⚖️.
- **Three answers per binding obligation**: "yes, compliant"; "no"; "in progress", which requires
  an expected completion date. Each answer is a new append-only `ObligationAttestation` row with
  timestamp, checklist version, actor (user and VendorMember role) and an AuditLog entry; an answer
  is never overwritten. A new checklist version asks the vendor to re-attest the changed items.
- **Reminders**, all service emails through ARC.16 in the vendor's language (no marketing consent
  needed, no tracking pixel), each with a one-click link to that checklist item:
  - "In progress": a reminder a configurable lead time before the expected date, repeated at a
    configurable cadence, asking the vendor to review its profile data and update the answer. If
    the date passes and the answer is still "in progress", another reminder is sent and the vendor
    is flagged to staff.
  - "No": periodic reminders at a cadence set per obligation, with a global default. The message
    asks, in substance: "Did you know about this obligation? When do you plan to comply, to avoid
    sanctions from the competent authority or suspension of your marketplace account?" Sanctions
    are mentioned generically, naming the competent authority and the legal basis, never amounts.
    Suspension is mentioned only if a consequence is configured for that obligation. When the
    answer changes to "in progress" with a date, or to "yes", the "no" reminders stop and the
    date-based reminders take over.
  - After a configurable number of unanswered reminders the vendor is flagged to staff.
  - Every reminder sent is logged: an AuditLog entry plus the email outbox record.
- **Consequences**: by default a reminder and a staff flag. Each obligation can also carry an
  optional consequence (warn, block payouts, block sales, suspend) applied through the VEN.16
  deadline and consequence mechanism, audited and reversible by staff.
- **Staff view** of open answers: "no", "in progress", overdue dates and flagged vendors, with
  filters by obligation and country.
- **Platform duties are not self-attestations.** The DAC7 dataset, the DSA article 30 trader
  traceability data and KYC (Stripe) keep their blocking rules in the VEN.12 payout requirement
  table and in VEN.5 activation. The DSA article 30(1)(e) self-certification (the vendor offers
  only products that comply with Union law), which GPSR article 22, Batteries article 62(6)(b) and
  PPWR article 45(4) also require, is collected on the same screen but is a condition of
  activation, not a three-answer item. EPR registration numbers that the law asks the platform to
  obtain are CMP.11.

Seed obligations (first checklist version; staff can add more):

| Code | Obligation | Applies to | Legal basis (Italian authority as example) |
|---|---|---|---|
| VAT | VAT registration in the vendor's country; OSS for EU B2C distance sales above €10,000 a year | Every vendor; OSS for EU vendors selling to other member states | Directive 2006/112/EC art. 59c and Title XII; tax authority of the vendor's country (IT: Agenzia delle Entrate) |
| DISPATCH | Goods are dispatched from inside the EU, with no drop-shipping from outside the EU, while the platform admits only EU-established vendors (D-21) | Every vendor | Directive 2006/112/EC art. 14a(1); the platform would otherwise be the deemed supplier (CMP.13) |
| INVOICE | Issue the invoice or receipt for every sale, including the e-invoicing mandate of the vendor's country: IT SDI (B2B since 2019); BE Peppol (B2B from 2026-01-01); PL KSeF (2026-02-01 large, 2026-04-01 others, micro 2027-01-01); FR (receipt for all and issuing for large and mid-size from 2026-09-01, issuing for SMEs from 2027-09-01); DE (receipt from 2025-01-01, issuing from 2027-01-01 above €800,000 turnover and from 2028-01-01 for all); HR (B2B from 2026-01-01); intra-EU B2B e-invoicing in EN 16931 with digital reporting from 2030-07-01 (ViDA, directive (EU) 2025/516) | Every vendor | National VAT law; tax authority of the vendor's country |
| CONSUMER | 14-day withdrawal, refunds, legal guarantee of conformity | Every vendor selling to consumers | Directive 2011/83/EU, directive (EU) 2019/771; IT: Codice del Consumo, AGCM |
| GPSR | Manufacturer and, for non-EU manufacturers, EU responsible person data and safety information for each product | Vendors of consumer products | Regulation (EU) 2023/988; market surveillance authority (IT: MIMIT) |
| BATTERIES | Battery producer registration in each country of sale, an authorised representative where not established, battery labelling | Vendors of batteries or of products containing batteries | Regulation (EU) 2023/1542 arts 13, 55, 56; IT: legislative decree 29/2026, RENAP register |
| WEEE | WEEE producer registration in each country of sale, information on take-back | Vendors of electrical and electronic equipment | Directive 2012/19/EU; IT: legislative decree 49/2014, Registro AEE |
| PACKAGING | Packaging EPR registration in each country of sale | Vendors shipping goods in packaging | Regulation (EU) 2025/40 arts 44, 45 (from 2026-08-12); IT: CONAI |
| TYRES | End-of-life tyre (PFU) contribution and registration | Vendors selling tyres into Italy | Legislative decree 152/2006 art. 228, D.M. 182/2019; MASE |
| OILS | Used oil consortium membership and contribution | Vendors selling lubricating oils into Italy | Legislative decree 152/2006 art. 236; CONOU |
| CONFORMITY | Product-specific conformity: PPE marking for protective clothing and gloves, UNECE R22.06 approval for helmets, type approval for regulated vehicle parts | Vendors in those categories | Regulation (EU) 2016/425; UNECE R22; regulation (EU) 168/2013 |
| GREEN | No generic environmental claims or uncertified sustainability labels in product texts | Every vendor | Directive (EU) 2024/825; IT: Codice del Consumo arts 21–23, AGCM |
| DATA | Pre-contractual data information for connected products | Vendors of connected products | Regulation (EU) 2023/2854 art. 3(2) |

### CMP.11 — Marketplace verification duties: DSA trader traceability and EPR checks `[P1]`
- **DSA article 30 (KYBC)**: before a vendor can sell, the platform holds its name, address,
  telephone and email, identity document, payment account (Stripe), trade register entry where
  registered, and the self-certification (CMP.10); it makes best efforts to check them (VIES, trade
  register where available) and suspends a vendor whose data is inaccurate. Name, address,
  contacts, trade register number and self-certification are shown to consumers on the vendor's
  offers. The data is kept at least 6 months after the relationship ends.
- **DSA article 31 (compliance by design)**: the vendor interface collects the pre-contractual,
  product identification, safety and labelling information (CMP.3, battery and packaging labels,
  EPR numbers, Data Act information for connected products); an offer is checked for completeness
  before it goes live, and random checks against Safety Gate follow.
- **DSA article 32**: when the platform learns that an illegal or unsafe product was sold, it
  informs the buyers of the last 6 months (the illegality, the vendor's identity, the remedies), or
  publishes the notice when it has no contact.
- **Product liability** (directive (EU) 2024/2853, products placed on the market after 2026-12-08):
  the platform can identify the vendor and its EU economic operator within one month of a request,
  and product pages never present an offer as the platform's own (DSA article 6(3)).
- **EPR registration checks**: per vendor, per EPR stream (batteries, packaging, WEEE; tyres and
  oils for Italy) and per destination country, the registration number and the vendor's
  self-certification. Required by Batteries article 62(6) (from 2025-08-18) and PPWR article
  45(4) (from 2026-08-12) for platforms within DSA Chapter III Section 4, and by national laws in
  destination countries (DE: ElektroG 6(2), VerpackDG 13(4), BattDG; FR: Code de
  l'environnement L.541-10-9, where an unchecked marketplace takes on the seller's EPR obligations;
  ES: RD 1055/2022, RD 27/2021). Product categories map to EPR streams; an offer is not sold into a
  country whose check is active and for which the vendor has no registration number. The checks
  are configurable per country and stream.
- **Activation per country is an owner decision** (owner answer to Q-67, 2026-11-07). The checks
  are built at go-live and every country starts **off**. Turning a country's check on or off is a
  two-step flow: a staff operator proposes it (country, streams, reason, legal basis), and a
  platform owner (spec [02](02-core-identity-permissions.md)) approves or rejects it; the proposer
  can never approve their own proposal. Both steps write an AuditLog entry, and the setting shows
  its approval record (proposer, approver, dates, reason). ⚖️ The owner decides country by country
  on legal counsel's advice: whether the EU checks bind a small platform, and whether the national
  checks bind a foreign marketplace.

### CMP.12 — VAT records of facilitated supplies `[P1]`
- A platform that facilitates supplies must keep records of them (article 242a of directive
  2006/112/EC, article 54c(2) of implementing regulation (EU) 282/2011): vendor name, postal and
  electronic address or website, VAT or tax number, bank account if available, description of the
  goods, value, place of dispatch and arrival, time of supply, order number. They are kept 10
  years from the end of the year of the transaction and made available electronically on request.
- Built from the order snapshots (ORD.2) and the VEN.12 identity: an export per vendor and period,
  and a retention rule that keeps the data out of CMP.4 erasure. This is a record, not bookkeeping.

### Deemed-supplier rules: decision D-21
Under article 14a of the VAT directive the platform itself becomes the VAT supplier when a vendor
not established in the EU sells goods already in the EU to consumers (widened from 2027-01-01 by
directive (EU) 2025/516), or when goods are imported in consignments of €150 or less. Similar rules
make the platform liable in the UK (consignments of £135 or less sold to UK consumers) and in
Switzerland (article 20a of the Swiss VAT act, from CHF 100,000 a year of small consignments).
These flows conflict with the no-invoicing principle. Owner decisions of 2026-11-07 (D-21, owner
answers to Q-65 and Q-66):

- **EU**: at go-live only vendors established in the EU can sell (CMP.13). Non-EU vendors (for
  example from Serbia or China) come later; the opening is designed now as P2 rows CMP.17 to
  CMP.22 and switched on only with the tax advisor ⚖️ (L).
- **United Kingdom**: B2C consignments to Great Britain of £135 or less are not sold on the
  platform. The buyer sends an order request to the vendor, who completes the sale outside the
  platform (CMP.14). ⚖️ (L) The tax advisor confirms before go-live that this design is not
  "facilitating" the supply under the UK online marketplace rules.
- **Switzerland**: a yearly counter of small consignments with alerts to the owner (CMP.15); the
  response at the threshold is decided with the tax advisor ⚖️ (L) (CMP.16).

### CMP.13 — Vendor establishment gate `[P1]`
- `PlatformOperator.allowed_vendor_countries`: the set of ISO 3166-1 countries where a vendor may
  be established, seeded with the EU-27 from the shared country constants (never a literal list in
  application code). It is changed only through
  `core.services.set_allowed_vendor_countries(countries, *, actor, reason, dry_run)`. The caller
  needs the platform owner permission (spec [02](02-core-identity-permissions.md)), which this task
  adds. The service creates a new `PlatformOperator` version, writes an AuditLog entry with the
  set before and after and the reason, and returns a `ChangeSet` that also lists the active vendors
  outside the new set. Shrinking the set suspends nobody: staff decide on the vendors listed.
- `engine.vendor_establishment(vendor)` is a pure function of the VEN.12 identity. It returns the
  registered office country and the country of the permanent establishment, when there is one. A
  vendor is admissible when one of them is in the allowed set. ⚖️ Evidence of a permanent
  establishment is checked by staff with the tax advisor's criteria.
- VEN.5 activation (`onboarding` → `active`) calls `assert_establishment_allowed(vendor)`, which
  refuses with a clear message naming the country. Applications from other countries can still be
  received and kept. When an active vendor's registered office or permanent establishment moves
  out of the set, staff get a flag and decide.
- Goods must also be dispatched from inside the EU: an import in a consignment of €150 or less
  makes the platform the deemed supplier whoever the vendor is (for example an EU vendor that
  drop-ships from a non-EU warehouse). At go-live this is a vendor declaration in the contract and
  a seed obligation of the CMP.10 checklist; CMP.17 later stores the ship-from country as data.

### CMP.14 — UK low-value consumer orders as vendor requests `[P1]`
- Scope: buyers in Great Britain who are not businesses, for each vendor consignment (the
  `VendorOrder` package) whose goods are worth £135 or less (intrinsic value, without transport
  and insurance unless they are part of the price). Northern Ireland (`BT` postcodes) follows D-02 (EU goods rules, TAX.18) and is outside this rule. The value is
  converted from EUR at the HMRC monthly exchange rate, kept as data. A buyer is B2B when a UK
  VAT number passes the HMRC check, through a client behind the same interface as the VIES client
  of TAX.5.
- Checkout turns such a consignment into **"send request to vendor"**. It reuses the quote request
  entry point and the vendor notifications of SHP.9 and SHP.10, but creates a `VendorLead`, not an
  order. The buyer sees a clear notice: the platform does not sell these goods, the vendor will
  contact them, and any sale is between the buyer and the vendor. Other vendors' consignments in
  the same cart stay normal sales.
- No Stripe charge, no `Order`, no ledger row and no commission. The platform sets no terms of
  sale and takes no part in payment or delivery: its role is lead referral only.
- The buyer's contact data goes to the vendor only after the buyer confirms it on the request
  screen (transparency notice, and consent where needed under GDPR). The transfer is logged, and
  the lead is in the CMP.4 export, erasure and retention rules.
- The vendor gets the request by email and in the vendor area, and contacts the buyer directly.
- The customer terms (CMS legal pages) say the platform is not a party to these sales.
- The rule is data (destination country, buyer type, value threshold and currency, validity),
  changed through an audited service; the seed row is Great Britain, B2C, £135. CMP.16 can add a
  Swiss row without code changes.
- Goods already in the UK sold by an overseas seller also make the marketplace liable, whatever
  their value. The CMP.13 dispatch rule (goods leave from inside the EU) keeps them out.
- UK orders above £135 stay normal platform sales. The buyer is told before payment that UK import
  VAT and duty are charged at delivery (DAP), unless the vendor's shipping method is marked as
  duties paid (DDP); the flag is stored per method.
- ⚖️ (L) The tax advisor confirms before go-live that the "no facilitation" test holds for this
  design: HMRC treats an operator that only lists, advertises or redirects, with no further
  involvement, as outside the online marketplace rules.

### CMP.15 — Swiss small-consignment counter `[P1]`
- A running counter per calendar year of the platform's turnover from small consignments to Swiss
  consumers: consignments whose computed Swiss import tax is CHF 5 or less, converted to CHF at the
  Swiss tax administration's published rate, kept as data. It is measured against the CHF 100,000
  deemed-supplier threshold of article 20a of the Swiss VAT act.
- Alerts by email to the owner and staff at configurable levels (default 70% and 90%), once per
  level and year. The levels are changed through an audited service.
- The response at the threshold is CMP.16.

### CMP.16 — Swiss threshold response `[P2]`
- ⚖️ (L) Decided with the tax advisor before the counter reaches the threshold. The options are
  to register for Swiss VAT as the supplier of small consignments (against the no-invoicing
  principle, with recurring costs) or to switch Swiss small consignments to the CMP.14
  lead-only flow by adding a Swiss rule row. The second option needs only configuration, a
  checkout test and the matching terms text.

### Opening to non-EU vendors `[P2]`
Designed now, implemented later (D-21). None of this is active at go-live: CMP.13 keeps non-EU
vendors out until the owner, with the tax advisor, widens the allowed set. Each row is switched on
by its own setting, off by default.

- **CMP.17 — Non-EU origin in shipping.** The ship-from country is stored per shipping method or
  stock location and travels with the package to the tax and shipping engines. It decides whether a
  sale is an import or a supply of goods already in the EU. Zones, indicative delivery times and
  extra days for customs are configured per origin, on top of the shipping methods and special
  areas of SHP.12 and SHP.14.
- **CMP.18 — Deemed-supplier VAT flows.** Covers goods already in the EU sold by a non-EU vendor
  (article 14a(2)) and imports in consignments of €150 or less (article 14a(1), IOSS). The tax
  engine gets a `deemed_supplier` outcome. The order snapshot records the two supplies: vendor to
  platform, exempt under article 136a, and platform to customer at the destination rate. The VAT
  of a deemed supply is withheld from the vendor transfer and recorded in the ledger as a
  `deemed_vat` row. The marketplace issues no invoices: the platform produces the deemed-supplier
  VAT data and records (the article 242a records of CMP.12, plus OSS union and IOSS return data per
  member state and period), and the operator's accountant does the fiscal side (registration,
  returns, payment). ⚖️ (L) Turning the flow on is an owner step with the tax advisor.
- **CMP.19 — Imports above €150 and IOSS per order.** Before payment the buyer sees who pays
  import VAT, customs duty and the carrier's clearance fee: at delivery (DAP, the default) or
  included in the price (DDP, flag per shipping method, shared with CMP.14). The low-value duty of
  €3 per item and the €2 handling fee of the EU customs reform are shown where they apply. For
  consignments of €150 or less under CMP.18, the IOSS number is recorded per order and passed only
  to the carrier and the vendor for the customs declaration, never shown to buyers.
- **CMP.20 — GPSR for products of non-EU manufacturers sold by non-EU vendors.** A product of a
  non-EU manufacturer needs an economic operator established in the EU (GPSR article 16, Market
  Surveillance Regulation article 4): manufacturer, importer, authorised representative or
  fulfilment service provider. CMP.3 already collects and shows the EU responsible person. CMP.20
  adds its type, its EU country and evidence of the mandate or contract. A non-EU vendor cannot be
  the importer itself. A listing without a valid EU operator is blocked, on the import pipelines
  too.
- **CMP.21 — DSA trader traceability for non-EU traders, and sanctions screening.** The article 30
  data of CMP.11 for traders outside the EU: identity document, trade register of the vendor's
  country where one exists, best-effort checks without VIES, and documents kept in their original
  language with staff notes. The spec had no sanctions screening. CMP.21 adds it: the vendor and
  its legal representative are screened against the EU consolidated financial sanctions list at
  activation and on every list update, matches go to staff review, and countries under
  comprehensive EU restrictions are never in the allowed set. It is a prerequisite for the first
  non-EU vendor.
- **CMP.22 — Stripe Connect for non-EU vendor countries.** Which vendor countries an EU platform can
  onboard as connected accounts, and which can receive only cross-border payouts under the
  recipient service agreement, is kept as data and checked against Stripe at review time
  (UNCERTAIN for Serbia and China). Charges and transfers stay in EUR, the payout currency and its
  conversion follow the Stripe account. A country without support keeps the vendor `on_request`
  (the D-19 pattern) or out of the allowed set. Builds on VEN.6 and PAY.3.

### CMP.23 — Order-data export for vendors' invoicing tools `[P2]`
- Owner answer to Q-68 (2026-11-07). Per `VendorOrder`, a non-fiscal export of the order snapshot
  mapped to the EN 16931 business terms (seller and buyer parties, lines, VAT category and rate,
  totals), so that a vendor's own invoicing tool (for example a Peppol or SDI tool) can import it
  and issue the invoice. Formats are CSV and JSON, plus an optional UBL-like XML.
- Every format is labelled "Documento non fiscale / Non-fiscal document, not an invoice". It has no
  invoice number, and the XML uses its own document type, so that no tool takes it for an issued
  invoice.

### CMP.24 — Approval of legal wording `[P1]`
- Owner answer to Q-70 (2026-11-07): the team and the agents draft the legal wording, and legal
  counsel approves it. Vendor contracts already have their own versioning and approval (VEN.13);
  CMP.24 covers every other text marked (L): obligation texts and reminder emails (CMP.10), the
  non-fiscal label, pre-contractual and marketplace information (CMP.6), withdrawal labels and
  emails (ORD.8), the CMP.14 notices, and legal notices in emails.
- Each (L) text, in every language, stores a version (SHA-256 hash of the text), its draft author
  (person or agent) and the counsel approval (approver name or role, date, approved hash). gettext
  keys carry an `(L)` context; database texts carry the same fields.
- A text whose current hash has no matching approval cannot be published to production. The
  release check fails on it, and a production system check refuses to start. STAGE shows the text
  with a "pending legal approval" marker. Approvals are audited.

### Regimes reviewed and not applicable at launch
Checked on 2026-11-07 ([regulatory check](../dev/regulatory-check-2026-11.md)); reassess at every
regulatory check and when the platform grows.

- NIS2 (directive (EU) 2022/2555, Italy legislative decree 138/2024): online marketplaces are in
  Annex II, but only medium and large entities are in scope (50 staff or more, or turnover and
  balance sheet above €10M). Out of scope at launch.
- Cyber Resilience Act (regulation (EU) 2024/2847): the marketplace software is a service, not a
  product with digital elements, and a mere marketplace is not a distributor. Vendors selling
  connected products have distributor duties from 2027-12-11 (a CMP.10 item to add then).
- DAC8 (directive (EU) 2023/2226): crypto-asset service providers only. Not applicable.
- PSD3 and the Payment Services Regulation: provisionally agreed on 2025-11-27, not yet published;
  application about 21 months after publication. Stripe is the licensed provider; D-08 is decided
  (adviser opinion 2026-11-11): no PSD2 licence while every customer payment goes through Stripe Connect.
- ViDA (directive (EU) 2025/516): Single VAT Registration from 2028-07-01, intra-EU digital
  reporting and e-invoicing from 2030-07-01, both vendor duties. The article 14a change from
  2027-01-01 does not apply at go-live, since CMP.13 admits only EU-established vendors; CMP.18
  handles it when non-EU vendors are admitted.
- EU customs reform: a €3 duty per item on low-value consignments from 2026-07-01 (regulation (EU)
  2026/382) and a €2 handling fee per item from 2026-11-01 apply to imports only. At go-live D-21
  admits only EU-established vendors, so they matter only after the opening to non-EU vendors
  (CMP.19).

## 11.3 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| CMP.1 | DAC7 quarterly aggregates + annual export, reading the dataset and completeness from VEN.12; a confirmed operator-owned vendor's sales follow D-22 (not reported, VEN.26); `Consideration` (T-6, adviser opinion 2026-11-11) is the amount paid or credited to the vendor net of the platform's fees and taxes, and the fees go in the separate `Fees` field; the PR is flagged for human review (DAC7) | P1 | 12 | VEN.5, VEN.4, VEN.12 | Test export validates against the Agenzia delle Entrate DPI XML schema and is checked by the advisor; excluded sellers (under 30 sales and €2,000 or less) are left out; each reported vendor gets its data; regression cases (T-6): on a 100 order with a 10 coupon and fees of 15 before the coupon, a vendor coupon reports Consideration 75 and Fees 15, and a Q-90 platform coupon reports Consideration 85 and Fees 5 |
| CMP.2 | Non-fiscal commission statement (pro-forma) per vendor and period from the ledger, with VEN.12 party data, as PDF per language and CSV/JSON export mappable to FatturaPA fields; no invoice issuing, no e-invoicing channel; statement lines (adviser opinion 2026-11-11, T-8, T-1, T-5): the refund fee as a platform service line of its own with the commission's VAT treatment; commission given back before the period is invoiced as the gross line plus a negative "commission on refunded orders" line; after invoicing, a credit note or a negative line on the next statement referencing the earlier one, both supported; a platform coupon or platform-paid free shipping reduces the commission and an excess over an order's commission is netted against the vendor's commissions of the period; the total of a period never goes below zero, and an excess larger than the whole period's commissions carries over to the next period as a vendor credit against future commissions, never paid out in cash (owner, 2026-11-11) | P1 | 8 | VEN.4, VEN.12 | A seed vendor's statement and export are labelled "Documento non fiscale / Non-fiscal document, not an invoice" in all five languages, carry a stable reference ID and no invoice number sequence, and their totals match the ledger; a refund before invoicing gives a gross commission line and a negative "commission on refunded orders" line; a refund after invoicing can produce either a credit note or a negative line that references the earlier statement; the refund fee is a separate line; a platform coupon larger than its order's commission reduces the other commissions of the period; no period total is negative; regression case: an excess larger than a period's commissions leaves that period at zero, is carried as a vendor credit, reduces the next period's total by the remainder, no payout or cash line is created, and no period total is negative |
| CMP.3 | `GpsrInfo` with per-vendor defaults, publication block, product page with picture, recall procedure; GPSR article 22 marketplace duties: points of contact, authority orders within 2 working days, safety notices within 3 working days, Safety Gate checks, repeat-offender suspension | P1 | 12 | CAT.1 | Product without GPSR cannot be published; a safety notice and an authority order show their working-day deadline and an overdue one alerts staff |
| CMP.4 | GDPR: customer export and erasure that anonymises the profile and non-fiscal data and never touches concluded orders (D-29), sub-processor register, retention job; erasure for a closed vendor's owner as restriction plus deletion of the data not under a retention duty (Q-16) | P1 | 12 | ORD.2 | Erasure request on a seed order; an erasure request from a closed vendor's owner keeps the membership, contract evidence, DAC7, ledger and order records, deletes the rest, and the retention job anonymises the kept data once the period ends (test with a frozen clock) |
| CMP.5 | DSA duties of every platform (points of contact, terms, notice and action, statement of reasons, criminal offence notification) and P2B (reasons for restrictions, 30 days' notice of termination, ranking page, differentiated treatment and data access in the terms); vendor terms acceptance is VEN.5, VEN.13 to VEN.16; contract clauses (adviser opinion 2026-11-11; owner, 2026-11-11), each stated in the terms as a requirement: customer terms (a) payment through the platform discharges the customer's debt towards the vendor (Italian civil code art. 1188, D-08); (b) compatibility data of the garage is declared by the vendor and an incompatibility is a conformity defect (Codice del consumo art. 129 and following) borne by the vendor (Q-G8); (c) a description of the Garage filter (Q-G2); vendor terms (d) Stripe provides the payment service under its Connected Account Agreement (D-08); (e) the vendor agrees to contractual set-off of fees and refund fees against transfers (art. 1252), with SEPA debit under its mandate for a negative balance (D-08); (f) the refund-fee clause of VEN.33 with the exact EUR 2.00 fixed part, the platform-error exception and 15 days' notice of a change (L-10); (g) vendors provide accessible descriptions and attachments (D-15); (h) the vendor answers for its declared compatibility (Q-G8); the "How we rank results" page describes the Garage filter (Q-G2) | P1 | 10 | VEN.5 | A notice gets a tracked outcome and the vendor a statement of reasons; a termination cannot take effect before 30 days from its notice; the customer and vendor terms versions in force carry clauses (a) to (h) in the five languages, each with its CMP.24 approval record; the ranking page describes the Garage filter |
| CMP.6 | Online withdrawal, 30-day price history (Omnibus), pre-contractual information, marketplace information (art. 49-bis Codice del Consumo), harmonised legal guarantee notice | P1 | 10 | ORD.2, CAT.1 | Correct strikethrough price in tests; every offer shows the vendor's trader status and the split of obligations; checkout shows the harmonised legal guarantee notice |
| CMP.7 | Automated accessibility audit in CI (axe, WCAG 2.2 AA tags) + manual review + statement; D-15 decided (adviser opinion 2026-11-11): a micro-enterprise operator is exempt from the EAA and the platform keeps WCAG 2.2 AA anyway; the vendor accessibility clause is in CMP.5 | P1 | 8 | THM.5 | Zero axe violations with the `wcag22aa` tag on key pages; manual review recorded; statement published |
| CMP.8 | AI Act transparency: "AI generated" or "AI edited" label and metadata flag on AI-produced product images, origin tracking of AI text, staff AI literacy briefing | P1 | 4 | CAT.4, ING.5 | An AI-edited image shows its label on the product page and in its metadata; unreviewed machine text cannot be published as reviewed |
| CMP.9 | WCAG 2.2 SC 3.3.8 (accessible authentication) on the authentication templates of CORE.4 and CORE.5: paste allowed in every password and code field, `autocomplete` tokens (`current-password`, `new-password`, `one-time-code`, `username webauthn`), passkeys offered, Turnstile without a puzzle | P1 | 3 | CORE.4, CORE.5, THM.3 | e2e test pastes a password and a TOTP code; the template lint finds the `autocomplete` tokens; Turnstile runs in managed or invisible mode |
| CMP.10 | Vendor obligations checklist with self-attestation: versioned `VendorObligation` data with applicability rules, three answers (yes, no, in progress with date) as append-only attestations, date-based and "no" reminders with one-click links, staff flags after N unanswered reminders, optional VEN.16 consequence per obligation, staff view of open answers, seed obligations; email wording approved by counsel through CMP.24 (L); reminder emails link through CORE.15 secure action links | P1 | 24 | VEN.12, VEN.16, ARC.16, ARC.9, CORE.9, SEC.4, CORE.15 | A seed vendor sees only the obligations that apply to it; an "in progress" answer without a date is refused; changing an answer adds a row and an AuditLog entry; a "no" answer sends reminders at the configured cadence that stop when the answer becomes "in progress" or "yes"; an overdue date and N unanswered reminders flag the vendor to staff; every reminder is in the audit log and the outbox; suspension is mentioned only when a consequence is configured |
| CMP.11 | DSA articles 30–32 (trader data, display on offers, compliance by design, buyer notification) and EPR registration checks per vendor, stream and destination country (Batteries art. 62(6), PPWR art. 45(4), DE/FR/ES national checks), vendor identification on request for product liability; per-country activation off by default, proposed by staff and approved by a platform owner, both steps audited | P1 | 19 | VEN.12, CMP.3, CMP.10, CAT.1, CMP.13 | An offer in a category mapped to an EPR stream is not sold into a country whose check is active without the vendor's registration number there; every country's check is off until a platform owner approves a staff proposal, the proposer cannot approve it, both steps are in the audit log and the setting shows who approved it and when; the trader data shows on the offer; an illegal-product decision produces the list of buyers of the last 6 months to inform |
| CMP.12 | VAT records of facilitated supplies (art. 242a VAT directive, art. 54c Reg. 282/2011): export per vendor and period, 10-year retention outside CMP.4 erasure | P1 | 4 | ORD.2, VEN.12 | The export of a seed period holds every field of article 54c(2) and an erasure request leaves the records in place |
| CMP.13 | Vendor establishment gate (D-21): `PlatformOperator.allowed_vendor_countries` seeded with the EU-27, changed only through an audited service with the platform owner permission; `vendor_establishment()` from the VEN.12 registered office and permanent establishment; VEN.5 activation refused outside the allowed set | P1 | 5 | VEN.5, VEN.12, ARC.7, SEC.4 | A vendor with its registered office in a non-EU country (RS, CN) and no permanent establishment in the allowed set cannot be activated and gets a clear message, while an IT vendor can; the default set is the EU-27; changing the set writes an AuditLog entry with before and after, a dry run writes nothing, and a user without the platform owner permission is refused |
| CMP.14 | UK B2C consignments of £135 or less per vendor as lead-only `VendorLead` requests (D-21): checkout notice, no charge, no order, no ledger or commission, buyer contact passed to the vendor only after confirmation and logged, vendor email and vendor area entry, HMRC VAT number check for B2B, HMRC monthly rate as data, rule rows as data; DAP or DDP notice per shipping method for UK orders above £135; ⚖️ (L) tax advisor confirms the no-facilitation test before go-live | P1 | 12 | ORD.3, SHP.9, SHP.10, TAX.5 | A GB consumer cart with a £100 consignment from one vendor and a £200 one from another creates one lead and one paid order; the lead has no PaymentIntent, ledger row or commission; the vendor receives the request only after the buyer confirms the data transfer, and the transfer is in the audit log; a buyer with a valid UK VAT number pays normally; the £200 order shows the DAP notice |
| CMP.15 | Swiss small-consignment counter: yearly turnover of consignments to Swiss consumers with import tax of CHF 5 or less, CHF rate as data, alerts to owner and staff at configurable levels (default 70% and 90% of CHF 100,000), levels changed through an audited service | P1 | 4 | ORD.2, ARC.9, ARC.16 | Seed orders above and below the CHF 5 import tax limit count correctly; crossing 70% and then 90% sends one alert each, once a year; the counter restarts on 1 January |
| CMP.16 | Swiss threshold response, ⚖️ (L) decided with the tax advisor: Swiss VAT registration as supplier, or a Swiss row in the CMP.14 lead-only rules | P2 | 3 | CMP.14, CMP.15 | With the Swiss rule active, a Swiss consumer's small consignment becomes a lead and a larger one stays a normal order; the customer terms mention Switzerland |
| CMP.17 | Non-EU origin in shipping: ship-from country per shipping method or stock location passed to the tax and shipping engines, zones, indicative delivery times and customs extra days per origin, built on SHP.12 and SHP.14 | P2 | 6 | SHP.3, SHP.4, SHP.12, SHP.14, CMP.13 | A package shipped from RS to IT carries its origin to the tax context and shows the customs extra days; a method without a ship-from country is refused |
| CMP.18 | Deemed-supplier VAT flows for non-EU vendors (art. 14a(1) and (2)): `deemed_supplier` tax outcome, two-supply snapshot (art. 136a exempt vendor supply, platform supply at the destination rate), VAT withheld from the vendor transfer as a `deemed_vat` ledger row, OSS union and IOSS return data per member state and period added to the CMP.12 records, no invoices; ⚖️ (L) switched on by the owner with the tax advisor | P2 | 20 | TAX.3, TAX.10, CMP.12, CMP.17, VEN.4, PAY.3 | With the flow on, a non-EU vendor's sale of goods in an IT warehouse to a DE consumer and a €100 import to an FR consumer get the `deemed_supplier` outcome; the transfer leaves out the VAT; the ledger, the CMP.12 export and the OSS and IOSS data agree to the cent; with the flow off such a checkout is blocked |
| CMP.19 | Imports into the EU: buyer information on import VAT, duty, carrier clearance fee, the €3 low-value duty and €2 handling fee, DAP (default) or DDP per shipping method; IOSS number recorded per order for consignments of €150 or less and passed only to the carrier and vendor | P2 | 12 | CMP.18, CMP.14, CMP.6, ORD.3 | A €300 import shows the DAP charges before payment and a DDP method shows none; a €100 IOSS consignment stores the IOSS number on the order and the shipping data, and the number never appears on buyer pages or emails |
| CMP.20 | GPSR for non-EU vendors: EU economic operator (GPSR art. 16, Market Surveillance Regulation art. 4) with type, EU country and mandate or contract evidence on `GpsrInfo`; a non-EU vendor cannot be its own importer; listing blocked without it, pipelines included | P2 | 6 | CMP.3, CMP.13 | A non-EU vendor's product of a non-EU manufacturer without a valid EU operator cannot be published or imported, with one it shows the operator on the product page |
| CMP.21 | DSA art. 30 trader traceability for non-EU traders (identity document, third-country trade register, checks without VIES, documents in the original language) and EU consolidated financial sanctions screening of the vendor and its legal representative at activation and on each list update, matches to staff review | P2 | 8 | CMP.11, CMP.13, ARC.9 | A non-EU test vendor cannot be activated without its article 30 data; a name on a test sanctions list blocks activation and opens a staff review; a list update re-screens active vendors |
| CMP.22 | Stripe Connect for non-EU vendor countries: supported connected-account and cross-border payout countries (recipient service agreement) as data, EUR charges and transfers, payout currency per account; an unsupported country keeps the vendor `on_request` | P2 | 6 | VEN.6, PAY.3, CMP.13 | A vendor from a country marked unsupported cannot sell online and shows as on request; a supported cross-border country gets a recipient account and its EUR transfers reconcile with the ledger |
| CMP.23 | Non-fiscal order-data export per `VendorOrder` mapped to EN 16931 business terms, as CSV, JSON and optional UBL-like XML with its own document type, labelled not an invoice | P2 | 8 | ORD.2, VEN.12 | A seed order's export maps parties, lines, VAT category, rate and totals to the listed BT codes; every format carries the non-fiscal label and no invoice number; the XML is not accepted as an invoice by an EN 16931 validator |
| CMP.24 | Approval of (L) legal wording: per text and language a version hash, draft author (person or agent) and counsel approval (approver name or role, date, approved hash) for gettext keys with an `(L)` context and database texts; release check and production system check refuse unapproved texts; STAGE shows a pending marker; approvals audited | P1 | 6 | I18N.7, ARC.16, SEC.4 | Changing an approved (L) text in any language makes the release check fail until a new approval matches its hash; production refuses to start with an unapproved text; STAGE shows the marker; each approval is in the audit log |
| CMP.25 | Activation gate for platform duties (coherence check 5): an `activation` purpose in `LegalFieldRequirement` seeded with the DSA article 30 trader data (name, address, telephone, email, trade register entry where registered), the article 30(1)(e) self-certification recorded as an append-only acceptance with text hash, both checked by the VEN.5 activation next to the CMP.13 gate; DAC7 data still blocks payouts only; CMP.11 keeps the checks, display on offers and EPR | P1 | 4 | VEN.5, VEN.12, CMP.13 | An EU vendor missing its telephone, its trade register entry (when registered) or the self-certification cannot be activated and gets the reason; a complete vendor is activated; missing DAC7 data does not block activation; the self-certification row stores version, language and hash |
| CMP.26 | GPSR follow-ups of the owner decisions 2026-11-09 (CMP.3): the checkout refuses a line whose product `apps.catalog.selling.sale_blockers` blocks for the delivery country and shows `{% product_destination_notice %}` (ORD.3, ORD.5); the product page includes the notice for the visitor's country (CAT.7); a Manager shop space page lists the vendor's products with the country languages their warnings miss (`vendor_warnings_language_gaps`, CORE.17); an in-app notice in the Manager platform space for the Catalog and moderation role when a safety case is overdue; open points of the CMP.3 review: link the notice only to languages in which the product page is published, a composite foreign key `(product, vendor)` on `SafetyCase` as in the catalog, `no` vs `nb`/`nn` matching for Norway, and Northern Ireland (GPSR applies, ships as `GB`, not checked) | P1 | 6 | CMP.3, ORD.3, ORD.5, CAT.7, CORE.17 | A cart line with warnings only in German cannot be checked out to a French-only country and the checkout shows why, with a link to the German warnings; the same cart checks out to a German-speaking country; the vendor's Manager page lists the missing country languages; an overdue safety case shows a notice to a Catalog and moderation user and not to a Finance user |
| CMP.27 | Remainder of CMP.3 article 22 duties (spec 11 CMP.3: "check Safety Gate alerts and take them into account", "suspend repeat offenders after a warning", "cooperate on recalls, including contacting buyers", "rapid recall or removal procedure"): a periodic check of published products against EU Safety Gate alerts with a staff review queue; a repeat-offender rule (a warning first, then vendor suspension, with the thresholds as platform data and every step in the `AuditLog`); a recall procedure that removes or blocks the product, lists affected orders and notifies the buyers through the transactional outbox, with the working-day deadlines tracked per notice | P2 | 12 | CMP.3, CMP.26 | A Safety Gate match opens a review item; a vendor with repeated confirmed cases gets a warning and then a suspension, each audited; a recall blocks the product, lists the affected orders and sends the buyer notices once |
| CMP.29 | Order retention jobs (D-29, D-30, D-31) on the ORD.2 scrub service, scheduled in the business time zone with dry-run reports and an `AuditLog` entry per run, following the `privacy_status` states: `active` to `restricted_tax_only` after the active phase (setting, default 24 months), dropping the non-fiscal fields (carrier phone, delivery notes) on entry; `restricted_tax_only` to `anonymized` from the day after `tax_retention_until`, and at once for orders never paid (draft, payment failed), with fixed placeholders and deletion of transport-proof files that hold names or signatures from the private bucket, keeping amounts, countries, dates, rates and nature codes; no job touches the fiscal fields before its period | P1 | 10 | ORD.2, CMP.4, ARC.9, ARC.27 | With a frozen clock a seed order is `active` at 23 months, `restricted_tax_only` at 25 months (setting changed in the test) with the carrier phone and delivery notes dropped, keeps the fiscal fields until 31 Dec of year 10, is still `restricted_tax_only` on that day and `anonymized` on the day after with amount, country, rate and nature code intact; a draft and a payment-failed order are anonymised at once; a transport-proof file with a name is deleted; ledger, payout and CMP.12 totals are identical before and after anonymisation; `dry_run` changes nothing and lists the orders; each run is in the `AuditLog`; the immutability trigger still refuses a direct update |
| CMP.30 | Customer account erasure (D-30; spec 02 covers only the vendor-owner flow of Q-16, so CORE has no task for it): a customer-area web flow with a POST confirm and re-authentication, a verified-email request for guests; always accepted, never refused with an error: login blocked, marketing removed, saved carts, addresses, payment methods, preferences and logs deleted, the profile anonymised, concluded orders moved to `restricted_tax_only` at once and never otherwise touched; with open orders (in shipment, return or withdrawal window, open dispute) the rest completes automatically when the last one closes and the user is told so; the confirmation email goes out before the address is removed with the article 17(3)(b) wording as a translatable template; third parties (email provider) revoked; audited | P1 | 10 | CMP.4, ORD.2, ORD.11, ORD.14, ARC.16 | A customer with two concluded orders erases: profile anonymised, cart, addresses and preferences gone, email unsubscribed, both orders `restricted_tax_only` with their amounts unchanged, confirmation email sent before the address is removed and stating the retention period; a guest erases through a verified-email request; a customer with an open order is accepted, blocked from login, and the erasure completes when the order closes; no refusal path; the audit entry holds no personal data |
| CMP.31 | Privacy-policy retention line (D-29, D-30) as a CMS-managed legal text requirement: the `LegalPage` privacy policy must contain "order, billing, shipping and VAT-validation data are kept 10 years from the close of the fiscal year…" with the legal basis and the account-erasure behaviour (orders kept restricted for the legal period), in all five languages; the period shown comes from the same setting as the CMP.29 jobs; wording approved through CMP.24 (L) | P1 | 2 | CMS.5, I18N.9, CMP.24, CMP.4 | The privacy page of a seed site renders the retention line in each language with the configured years; publishing the privacy page without the line is refused by the release check; the line text is an (L) text needing CMP.24 approval |
| CMP.33 | Data export step-up parity (follow-up of CMP.4 and CMP.30; proposal A of the #266 question, recommended and pending lane-a confirmation): the customer self-service data export (`download_data`) requires a fresh second factor when the account has one, like the account erasure of CMP.30, because the export holds all personal data; an account without a second factor keeps the password re-authentication; the check uses the same fresh-factor helper as erasure (one place, one freshness window), never a weaker path for the export; the refusal is audited without personal data | P2 | 3 | CMP.4, CMP.30, ARC.16 | A customer with a second factor who sends the password alone gets the step-up page and no export; with a fresh factor the export downloads; a customer without a second factor still exports with the password; a stale factor (past the window) is asked again; the export and erasure share one helper (test of both); the refusal writes an `AuditLog` entry without personal data |
