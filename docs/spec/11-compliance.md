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
- Visible on the product page with at least one product picture (article 19); **publication
  blocked** if mandatory fields are missing; the import contract requires them. Per-vendor defaults
  (the manufacturer often is the vendor). Warnings and safety information in the language of the
  destination country.
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
- Customer data export (JSON/PDF) and erasure as **anonymization** (orders are kept
  for accounting obligations).
- Record of processing activities and sub-processors (Stripe, carriers, email provider, AI provider, hosting),
  retention per data type, DPA.
- No tracking pixels in emails without consent (Garante privacy, provision 284 of 2026-04-17,
  compliance from about 2026-10-29): transactional and service emails (ARC.16, CMP.10 reminders)
  carry no open-tracking pixel; marketing emails carry one only with consent, revocable on its own.
- Non-production environments only with synthetic or anonymized data (today LIVE is cloned to STAGE).
- Cookie consent with Consent Mode (see [10](10-content-seo-ai.md)).

### CMP.5 — DSA and P2B `[P1]`
- DSA (regulation (EU) 2022/2065) duties of every hosting service and online platform, whatever
  its size, so P1 for go-live: points of contact for authorities and for users (articles 11, 12),
  terms with the content moderation rules (article 14), a form for reporting illegal content and
  unsafe products (notice and action, article 16) with tracking, a statement of reasons to the
  vendor for every restriction (article 17), and notification of suspected criminal offences
  (article 18).
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
  every obligation and of the reminder emails gets a one-time review by legal counsel ⚖️ (L).
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
  are configurable per country and stream. ⚖️ Whether the EU checks bind a small platform and
  whether the national checks bind a foreign marketplace is Q-67.

### CMP.12 — VAT records of facilitated supplies `[P1]`
- A platform that facilitates supplies must keep records of them (article 242a of directive
  2006/112/EC, article 54c(2) of implementing regulation (EU) 282/2011): vendor name, postal and
  electronic address or website, VAT or tax number, bank account if available, description of the
  goods, value, place of dispatch and arrival, time of supply, order number. They are kept 10
  years from the end of the year of the transaction and made available electronically on request.
- Built from the order snapshots (ORD.2) and the VEN.12 identity: an export per vendor and period,
  and a retention rule that keeps the data out of CMP.4 erasure. This is a record, not bookkeeping.

### CMP.13 — Deemed-supplier flow gate `[P1]`
- Under article 14a of the VAT directive the platform itself becomes the VAT supplier when a vendor
  not established in the EU sells goods already in the EU to consumers (widened from 2027-01-01 by
  directive (EU) 2025/516), or when goods are imported in consignments of €150 or less. Similar
  rules make the platform liable in the UK (consignments of £135 or less sold to UK consumers) and
  in Switzerland (article 20a of the Swiss VAT act, from CHF 100,000 of small consignments). These
  flows conflict with the no-invoicing principle.
- `Vendor.established_in_eu` derives from the registered office and any permanent establishment;
  checkout applies the rules decided in D-21 (proposal: EU-established vendors only, B2C
  consignments to Great Britain of £135 or less refused, a counter of small consignments to
  Switzerland with an alert before CHF 100,000). Blocked by D-21.

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
  application about 21 months after publication. Stripe is the licensed provider; D-08 stays open.
- ViDA (directive (EU) 2025/516): Single VAT Registration from 2028-07-01, intra-EU digital
  reporting and e-invoicing from 2030-07-01, both vendor duties; the article 14a change from
  2027-01-01 is CMP.13.
- EU customs reform: a €3 duty per item on low-value consignments from 2026-07-01 (regulation (EU)
  2026/382) and a €2 handling fee per item from 2026-11-01 apply to imports only, so only to
  non-EU vendors if D-21 allows them.

## 11.3 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| CMP.1 | DAC7 quarterly aggregates + annual export, reading the dataset and completeness from VEN.12 | P1 | 12 | VEN.5, VEN.4, VEN.12 | Test export validates against the Agenzia delle Entrate DPI XML schema and is checked by the advisor; excluded sellers (under 30 sales and €2,000 or less) are left out; each reported vendor gets its data |
| CMP.2 | Non-fiscal commission statement (pro-forma) per vendor and period from the ledger, with VEN.12 party data, as PDF per language and CSV/JSON export mappable to FatturaPA fields; no invoice issuing, no e-invoicing channel | P1 | 8 | VEN.4, VEN.12 | A seed vendor's statement and export are labelled "Documento non fiscale / Non-fiscal document, not an invoice" in all five languages, carry a stable reference ID and no invoice number sequence, and their totals match the ledger |
| CMP.3 | `GpsrInfo` with per-vendor defaults, publication block, product page with picture, recall procedure; GPSR article 22 marketplace duties: points of contact, authority orders within 2 working days, safety notices within 3 working days, Safety Gate checks, repeat-offender suspension | P1 | 12 | CAT.1 | Product without GPSR cannot be published; a safety notice and an authority order show their working-day deadline and an overdue one alerts staff |
| CMP.4 | GDPR: customer export and anonymization, sub-processor register, retention job | P1 | 10 | ORD.2 | Erasure request on a seed order |
| CMP.5 | DSA duties of every platform (points of contact, terms, notice and action, statement of reasons, criminal offence notification) and P2B (reasons for restrictions, 30 days' notice of termination, ranking page, differentiated treatment and data access in the terms); vendor terms acceptance is VEN.5, VEN.13 to VEN.16 | P1 | 10 | VEN.5 | A notice gets a tracked outcome and the vendor a statement of reasons; a termination cannot take effect before 30 days from its notice |
| CMP.6 | Online withdrawal, 30-day price history (Omnibus), pre-contractual information, marketplace information (art. 49-bis Codice del Consumo), harmonised legal guarantee notice | P1 | 10 | ORD.2, CAT.1 | Correct strikethrough price in tests; every offer shows the vendor's trader status and the split of obligations; checkout shows the harmonised legal guarantee notice |
| CMP.7 | Automated accessibility audit in CI (axe, WCAG 2.2 AA tags) + manual review + statement | P1 | 8 | THM.5 | Zero axe violations with the `wcag22aa` tag on key pages; manual review recorded; statement published |
| CMP.8 | AI Act transparency: "AI generated" or "AI edited" label and metadata flag on AI-produced product images, origin tracking of AI text, staff AI literacy briefing | P1 | 4 | CAT.4, ING.5 | An AI-edited image shows its label on the product page and in its metadata; unreviewed machine text cannot be published as reviewed |
| CMP.9 | WCAG 2.2 SC 3.3.8 (accessible authentication) on the authentication templates of CORE.4 and CORE.5: paste allowed in every password and code field, `autocomplete` tokens (`current-password`, `new-password`, `one-time-code`, `username webauthn`), passkeys offered, Turnstile without a puzzle | P1 | 3 | CORE.4, CORE.5, THM.3 | e2e test pastes a password and a TOTP code; the template lint finds the `autocomplete` tokens; Turnstile runs in managed or invisible mode |
| CMP.10 | Vendor obligations checklist with self-attestation: versioned `VendorObligation` data with applicability rules, three answers (yes, no, in progress with date) as append-only attestations, date-based and "no" reminders with one-click links, staff flags after N unanswered reminders, optional VEN.16 consequence per obligation, staff view of open answers, seed obligations; email wording reviewed once by counsel (L) | P1 | 24 | VEN.12, VEN.16, ARC.16, ARC.9, CORE.9, SEC.4 | A seed vendor sees only the obligations that apply to it; an "in progress" answer without a date is refused; changing an answer adds a row and an AuditLog entry; a "no" answer sends reminders at the configured cadence that stop when the answer becomes "in progress" or "yes"; an overdue date and N unanswered reminders flag the vendor to staff; every reminder is in the audit log and the outbox; suspension is mentioned only when a consequence is configured |
| CMP.11 | DSA articles 30–32 (trader data, display on offers, compliance by design, buyer notification) and EPR registration checks per vendor, stream and destination country (Batteries art. 62(6), PPWR art. 45(4), DE/FR/ES national checks), vendor identification on request for product liability | P1 | 16 | VEN.12, CMP.3, CMP.10, CAT.1 | An offer in a category mapped to an EPR stream is not sold into a country whose check is active without the vendor's registration number there; the trader data shows on the offer; an illegal-product decision produces the list of buyers of the last 6 months to inform |
| CMP.12 | VAT records of facilitated supplies (art. 242a VAT directive, art. 54c Reg. 282/2011): export per vendor and period, 10-year retention outside CMP.4 erasure | P1 | 4 | ORD.2, VEN.12 | The export of a seed period holds every field of article 54c(2) and an erasure request leaves the records in place |
| CMP.13 | Deemed-supplier flow gate: `Vendor.established_in_eu`, checkout rules per D-21 for non-EU vendors, GB consignments and the Swiss threshold counter | P1 | 6 | VEN.12, ORD.3 | The D-21 rules hold in checkout tests: a refused flow is blocked with a clear message and an allowed one passes |
