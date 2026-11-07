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
  rules are in [14](14-frontend-themes.md) §14.6.

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
| CMP.7 | Automated accessibility audit in CI (axe, WCAG 2.2 AA tags) + manual review + statement | P1 | 8 | THM.5 | Zero axe violations with the `wcag22aa` tag on key pages; manual review recorded; statement published |
| CMP.9 | WCAG 2.2 SC 3.3.8 (accessible authentication) on the authentication templates of CORE.4 and CORE.5: paste allowed in every password and code field, `autocomplete` tokens (`current-password`, `new-password`, `one-time-code`, `username webauthn`), passkeys offered, Turnstile without a puzzle | P1 | 3 | CORE.4, CORE.5, THM.3 | e2e test pastes a password and a TOTP code; the template lint finds the `autocomplete` tokens; Turnstile runs in managed or invisible mode |
