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

### CMP.1 — DAC7 `[P1]`
- Dataset: owned by VEN.12 (see [04](04-vendors-commissions-ledger.md)). The vendor's legal
  identity holds the DAC7 fields, the seeded `LegalFieldRequirement` rows require them for a
  payout for every vendor, non-EU sellers included, and `engine.dac7_missing()` reports what is
  missing. CMP.1 does not collect or validate them again; it reads them.
- Aggregation per vendor and quarter from the ledger (consideration, commissions, number of transactions).
- Annual export in the format of the authority of the **platform's country of establishment**
  (`PlatformOperator`). ⚖️ Verify the exclusion thresholds (e.g. vendors with fewer than 30 sales and less than €2,000).

### CMP.2 — Commission invoicing `[P1]`
- Platform → vendor commission document per period, generated from the ledger. The vendor's party
  block comes from VEN.12 (`engine.fatturapa_party()`); CMP.2 writes the XML and validates it
  against the official schema.
- Channel depends on the `PlatformOperator`: for an Italian platform it is SDI (FatturaPA), with the
  wording of the platform's tax regime ⚖️; abstract `EInvoiceChannel` interface (SDI via an intermediary
  today, other channels in the future, ready for ViDA).
- Invoices to customers remain the vendor's: the platform provides the data per line and per order
  (export per vendor). ⚖️ Clarify the boundary in the vendor contract.

### CMP.3 — GPSR `[P1]`
```python
class GpsrInfo(Model):
    manufacturer_name
    manufacturer_address
    manufacturer_contact
    eu_responsible_name
    eu_responsible_address
    eu_responsible_contact  # mandatory if the manufacturer is outside the EU
    product_identifiers(batch, model, GTIN)
    warnings(translated)
    safety_documents(file)
```
- Visible on the product page; **publication blocked** if mandatory fields are missing; the
  import contract requires them. Per-vendor defaults (the manufacturer often is the vendor).
- Rapid recall or removal procedure upon notice from the authorities or a safety report.

### CMP.4 — Operational GDPR `[P1]`
- Customer data export (JSON/PDF) and erasure as **anonymization** (orders are kept
  for accounting obligations).
- Record of processing activities and sub-processors (Stripe, carriers, email provider, AI provider, hosting),
  retention per data type, DPA.
- Non-production environments only with synthetic or anonymized data (today LIVE is cloned to STAGE).
- Cookie consent with Consent Mode (see [10](10-content-seo-ai.md)).

### CMP.5 — DSA and P2B `[P2]`
- Form for reporting illegal content (notice & action) with tracking, a reasoned decision and
  notification to the vendor.
- Vendor traceability (KYBC): identification data verified before activation (also covers D.1).
- Versioned platform-vendor terms with tracked acceptance and advance notice of changes are built
  in VEN.5 and VEN.13 to VEN.16 (see the vendor contract section below). CMP.5 keeps notice of
  suspensions and the transparency on the ranking criteria of results (dedicated page).

### Vendor contracts: language and acceptance evidence
- The vendor contract follows the vendor's language (IT, EN, DE, FR, ES), one text per language,
  and each version declares a prevailing language that wins in case of discrepancies; the clause
  is printed in every rendered PDF. Without a text in the vendor's language the vendor accepts the
  English text and explicitly acknowledges it. Details in [04](04-vendors-commissions-ledger.md).
- Acceptance is a simple electronic signature under eIDAS (email one-time code plus MFA step-up),
  with the exact personalized PDF, its SHA-256 hash and an RFC 3161 timestamp. A qualified
  signature upload (VEN.17, P2) gives stronger evidence. ⚖️ Check with legal counsel which
  clauses need specific written approval (Italian civil code, articles 1341 and 1342) and whether
  a simple electronic signature is enough for them, or the qualified option is needed.
- Retention: the PDF, the evidence package, the timestamp token and any signed upload are kept in
  the private bucket for the whole vendor relationship and then for the retention period (proposed
  default: 10 years after the relationship ends, the ordinary limitation period in Italy ⚖️). The
  signer's personal data in the evidence are kept on the basis of establishing and defending legal
  claims (GDPR article 17(3)(e)), so a CMP.4 erasure request does not delete them; the CMP.4
  retention job deletes the files when the period ends. Evidence files are append-only and are
  never rewritten.
- Timestamps come from a TSA behind the `TimestampAuthority` interface; the choice between a free
  TSA and a qualified one is an owner decision.

### CMP.6 — Consumer rights `[P1]`
- **Online withdrawal function** (EU directive 2023/2673, amending directive 2011/83/EU):
  clearly visible button for the entire withdrawal period, minimal data, confirmation, receipt on a durable
  medium. ⚖️ Verify the Italian transposition and the details of the button label.
- Pre-contractual information per language; legal guarantee; 14-day withdrawal by consumer country.
- Geo-blocking: no discrimination based on residence in the countries served.
- Omnibus: strikethrough price = lowest price in the last 30 days (price history in `catalog`);
  information on how reviews are verified.

### CMP.7 — European Accessibility Act `[P1]`
- E-commerce services fall under the EAA. ⚖️ Service **microenterprises** (fewer than 10 employees
  and turnover or balance sheet ≤ €2M) are exempt: verify the platform's status.
- Recommendation: design to **WCAG 2.1 AA** (EN 301 549) anyway. It costs little if done
  from the start, it is a de facto requirement for growth, and vendors with more than 10 employees may
  ask for it. Published accessibility statement.

### CMP.8 — AI-generated content `[P3]`
- Product descriptions are AI-generated and reviewed. ⚖️ Assess any AI Act transparency obligations
  for published content; in any case track the origin in `ProductTranslation`
  (`machine` / `reviewed`).

## 11.3 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| CMP.1 | DAC7 quarterly aggregates + annual export, reading the dataset and completeness from VEN.12 | P1 | 12 | VEN.5, VEN.4, VEN.12 | Test export validated by the advisor |
| CMP.2 | Commission document from the ledger (party block from VEN.12) + `EInvoiceChannel` interface + export per vendor | P1 | 12 | VEN.4, VEN.12 | Test document generated |
| CMP.3 | `GpsrInfo` with per-vendor defaults, publication block, product page, recall procedure | P1 | 8 | CAT.1 | Product without GPSR cannot be published |
| CMP.4 | GDPR: customer export and anonymization, sub-processor register, retention job | P1 | 10 | ORD.2 | Erasure request on a seed order |
| CMP.5 | DSA notice & action, notice of suspensions and ranking transparency page (vendor terms acceptance is VEN.5, VEN.13 to VEN.16) | P2 | 6 | VEN.5 | Notice with tracked outcome |
| CMP.6 | Online withdrawal, 30-day price history (Omnibus), pre-contractual information | P1 | 8 | ORD.2, CAT.1 | Correct strikethrough price in tests |
| CMP.7 | Automated accessibility audit in CI (axe) + manual review + statement | P1 | 8 | CMS.7 | Zero critical violations on key pages |
