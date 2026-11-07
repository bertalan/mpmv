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
- Dataset collected at vendor onboarding: legal name, VAT number/tax code, registered office address,
  country of tax residence, IBAN, permanent establishment. Completeness = precondition for payouts.
- Aggregation per vendor and quarter from the ledger (consideration, commissions, number of transactions).
- Annual export in the format of the authority of the **platform's country of establishment**
  (`PlatformOperator`). ⚖️ Verify the exclusion thresholds (e.g. vendors with fewer than 30 sales and less than €2,000).

### CMP.2 — Commission invoicing `[P1]`
- Platform → vendor commission document per period, generated from the ledger.
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
- Versioned platform-vendor terms with tracked acceptance; advance notice of changes and
  suspensions; transparency on the ranking criteria of results (dedicated page).

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
| CMP.1 | DAC7 dataset on the vendor + completeness + quarterly aggregates + export | P1 | 12 | VEN.5, VEN.4 | Test export validated by the advisor |
| CMP.2 | Commission document from the ledger + `EInvoiceChannel` interface + export per vendor | P1 | 12 | VEN.4 | Test document generated |
| CMP.3 | `GpsrInfo` with per-vendor defaults, publication block, product page, recall procedure | P1 | 8 | CAT.1 | Product without GPSR cannot be published |
| CMP.4 | GDPR: customer export and anonymization, sub-processor register, retention job | P1 | 10 | ORD.2 | Erasure request on a seed order |
| CMP.5 | DSA notice & action + versioned vendor terms with acceptance | P2 | 8 | VEN.5 | Notice with tracked outcome |
| CMP.6 | Online withdrawal, 30-day price history (Omnibus), pre-contractual information | P1 | 8 | ORD.2, CAT.1 | Correct strikethrough price in tests |
| CMP.7 | Automated accessibility audit in CI (axe) + manual review + statement | P1 | 8 | CMS.7 | Zero critical violations on key pages |
