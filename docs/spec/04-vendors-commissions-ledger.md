# 04 — Vendors, plans, commissions, ledger and payouts

## 4.1 How it works today

**Vendor profile data** (WCFM user meta): `wcfmmp_profile_settings` (store name, EN description,
address, commission block), `_store_description_{lang}`, `wcfm_policy_vendor_options_{en|it}`
(shipping, refund, cancellation), `wcfm_store_slug`, logo; Stripe meta (`stripe_user_id`,
`vendor_connected`, `stripe_payouts_enabled`); VTO tax meta (see [05](05-tax.md)).

**Plans** (WCFM Membership, payment `by_wcfm`):

| Plan | Fee | Commission | Products |
|---|---|---|---|
| Basic | free | highest | low limit |
| Intermediate | monthly fee, with trial | medium | medium limit |
| Advanced | higher monthly fee | lowest | unlimited |
| Special | — | from profile | — |

The real vendors are on the Special plan, with the commission defined in each vendor's profile. Time-limited
launch promotions are also planned (reduced commission for the first vendors).

**Commission and payment fee** (declared model):
`admin = taxable × % + (gross × 4% + €2)` and `vendor = gross − admin`.
Under the platform's current tax regime the commission is charged **without VAT**.

**Payouts**: `stripe_split` in `destination_charges`; WCFM creates the `by_split_pay` payout already
`completed` at charge time. Standard (non-split) payout on `wc-completed`, monthly, minimum €50,
7-day waiting period. Four vendors out of five are connected to Stripe Express (Vendor E is not).

**Historical bugs** (they become tests, see 4.4): `tax_enable` read with `isset()`; transaction charge
on the net instead of the gross (case FEE-NETTO: 9.3424 expected, 8.07 computed); VAT computed on
commission + payment fee (34.07 instead of 26.00); rule frozen at `fixed 0` (case REGOLA-ZERO) or 25% (case PERC-CONGELATA);
`stripe_cc` orders without split, with orphaned vendor credits (case NO-SPLIT).

## 4.2 How it will work

### Models

```python
class Vendor(Model):
    legal_name; display_name; slug (UNIQUE)
    status = CharField(choices=["applicant", "onboarding", "active", "suspended", "closed"])
    country; vat_number; tax_code; registered address (structured fields)
    legal and e-invoicing identity: see "Legal and e-invoicing identity" below (VEN.12)
    contact_email; phone; logo (Image)
    plan = FK(VendorPlan)
    sales_mode_default = CharField(choices=["buy", "on_request"])
    enquiries_enabled: bool                    # formerly Vendor E's "catalog status" plugin
    payout_account = FK(PayoutAccount, null=True)
    dac7_complete: bool (derived)              # blocks payouts if False
    legacy_wp_user_id

class VendorTranslation(Model): vendor; language; tagline; description; shipping_policy; refund_policy; cancellation_policy

class VendorPlan(Model):                       # plans are DATA, not code
    code ("basic", "intermediate", "advanced", "special", "launch"); name (translated)
    max_products (null = unlimited); features (JSON: featured, api_sync, custom_tax, vendor_branding…)
    monthly_fee (MoneyField, null)             # plan fee charging: P2, VEN.19 (D-04)

class CommissionRule(Model):                   # versioned: never modified, only new rows
    vendor (null = platform default); plan (null)
    percent = DecimalField(5,2)                # 20.00
    base = CharField(choices=["net_goods", "net_goods_and_shipping"])   # today: net goods only
    fee_percent = DecimalField(5,2)            # 4.00 — payment fee
    fee_fixed = MoneyField()                   # 2.00
    fee_base = CharField(choices=["gross_paid"])   # always the gross amount paid (fixes case FEE-NETTO)
    valid_from; valid_to; promo_label ("launch promo")
    # constraint: percent > 0 or explicit promo; no implicit "fixed 0" rule (fixes case REGOLA-ZERO)

class PayoutAccount(Model):
    vendor; provider ("stripe"); external_id (acct_…); status; payouts_enabled; platform_entity

class LedgerEntry(Model):                      # append-only, never UPDATE/DELETE
    vendor; vendor_order = FK(VendorOrder, null=True)
    kind = CharField(choices=["sale", "shipping", "commission", "payment_fee", "refund",
                              "commission_reversal", "adjustment", "payout", "transfer",
                              "plan_fee"])        # plan_fee: VEN.19 (P2)
    amount = MoneyField()                      # sign: + vendor credit, − vendor debit
    platform_entity = FK(PlatformOperator)     # multi-entity (e.g. relocation to another EU country)
    gateway_account; external_ref (tr_…, po_…, re_…)
    created_at; created_by; memo

class Payout(Model): vendor; amount; status; period; external_ref; ledger_entries M2M
```

`platform_entity` on `LedgerEntry` and `PayoutAccount` points to the `PlatformOperator`
configuration version row that was valid when the entry or account was created, not to a stable
legal-entity key. Each row therefore records exactly how the platform was configured at that
moment (country, VAT number, tax regime), which is what an auditor needs. A change of address or
regime creates a new version, so one company can appear across several version rows: balances and
reports per company are recomposed by grouping the versions that share the same legal identity
(the VAT number of the platform operator). A relocation to a new company with a new VAT number
starts a new group.

**The ledger is not accounting** (owner decision of 2026-11-07, see [00](00-vision-and-review.md)).
`LedgerEntry` is an operational record of what each vendor is owed and paid, kept to compute
balances and payouts and to reconcile them with Stripe. It is not a general ledger, a VAT register
or a bookkeeping record of the operator or of the vendors, and the platform issues no invoices
from it. Ledger extracts and payout statements are non-fiscal documents with a stable reference
ID; the operator's accountant and each vendor do their own bookkeeping and invoicing outside the
platform.

### Pure functions

```python
@dataclass(frozen=True)
class CommissionInput:
    goods_net: Decimal        # taxable amount of the vendor's goods in the order
    shipping_net: Decimal
    gross_paid: Decimal       # vendor's share of the total paid (VAT included)
    rule: CommissionRuleSnapshot

def compute_commission(i: CommissionInput) -> CommissionResult:
    """commission = round(base × percent)          # base = goods_net (default)
       payment_fee = round(gross_paid × fee_percent + fee_fixed)
       vendor_net = gross_paid − commission − payment_fee
       VAT on the commission according to PlatformOperator.commission_vat_mode (today "none")."""

def vendor_balance(vendor, until: datetime) -> Decimal      # ledger sum
def payout_eligibility(vendor) -> Eligibility               # DAC7 complete, account enabled, minimum, waiting period
```

### Flows

1. **Vendor application**: public form (Turnstile) → `applicant` → staff review → `onboarding`
   (DAC7 data, Stripe Express account via onboarding link, acceptance of the versioned contract
   with the signature flow below, tax regime) → `active`. Every step is recorded in `AuditLog`.
2. **Paid order**: for each `VendorOrder` the rule snapshot is frozen, `compute_commission` is
   computed, and the ledger rows are written (`sale`, `shipping`, `commission`, `payment_fee`).
3. **Transfer**: with *separate charges and transfers* (see [07](07-cart-orders-payments.md)) a Stripe
   transfer is created per vendor for the `vendor_net` amount, recorded as `transfer`.
4. **Refund**: proportional `refund` and `commission_reversal` rows; current business rule
   "on a full refund the platform returns the commission and the payment fee" (to be confirmed, D-05).
5. **Nightly reconciliation**: ledger ↔ Stripe transfers and balance; discrepancies → alert and report.

### Plan entitlements and plan fees (VEN.19, P2)

Plans are data (VEN.2), and their `features` keys are the **entitlements**: what a plan allows
beyond commission and product limits (for example `vendor_branding`, spec 14 §14.12). Code never
tests a plan code; it asks `plan_allows(vendor, feature) -> bool` in `apps/vendors/services.py`,
backed by a pure `engine.plan_allows(plan_features, feature)`. A plan change has an effective date
and is audited; losing an entitlement hides the feature and keeps its data.

Plan fees (D-04: not at launch). From P2, a plan with a `monthly_fee` is charged once per month
and vendor by a scheduled job (ARC.9) as a `plan_fee` ledger debit, so it is settled against the
vendor's balance like the commission. The job is idempotent per vendor and period. The
**marketplace issues no invoice** for the fee (owner principle): each charge produces a
**non-fiscal statement** (PDF in the vendor's language, in the document area of VEN.15) that says
it is not a tax invoice, and the operator invoices the fee outside the platform. The statement
shows the plan, period, amount and the ledger reference.

### Legal and e-invoicing identity (VEN.12)

The vendor's legal identity lives on `Vendor` itself, not in a separate profile row: spec 04
already lists these fields on the vendor, DAC7 (CMP.1) reads them from it, and a 1:1 table would
only add a "profile row missing" state.

| Group | Fields | Rules |
|---|---|---|
| Legal form | `legal_form` (sole proprietorship, partnership, S.r.l., S.p.A., cooperative, other or foreign); `first_name`, `last_name` for sole proprietors | FatturaPA `Anagrafica`: `Nome` + `Cognome` for a sole proprietor, `Denominazione` (the legal name) otherwise |
| Registered office | `registered_street`, `registered_street_number`, `registered_postal_code`, `registered_city`, `registered_province`, `registered_country` | Italy: 5-digit postal code and official 2-letter province code. Replaces the former JSON address (data-preserving migration) |
| Tax identity | `vat_number` with `vies_status` (unchecked, valid, invalid, unavailable) and `vies_checked_at`; `tax_code` (codice fiscale in Italy, the national TIN elsewhere); `tax_residence_country` (DAC7); `fiscal_regime` (RF01–RF20 with the official SDI labels; there is no RF03; RF19 = forfettario; RF20 = cross-border VAT franchise for small enterprises, Directive (EU) 2020/285, added by the FatturaPA technical specification 1.9 from 2025-04-01, VEN.18); `ateco_code`; `eori` | Italian VAT checksum; codice fiscale check character (omocodia included) or the 11-digit numeric form. VIES is reached only through the `VatNumberValidator` interface; changing the VAT number resets the status to unchecked |
| Company register (REA) | `rea_office` (province), `rea_number`, `share_capital`, `sole_shareholder` (SU/SM), `liquidation_status` (LS/LN) | FatturaPA `IscrizioneREA` |
| E-invoicing | `sdi_recipient_code`, `pec` | 7 characters; `0000000` when invoices go to the PEC; `XXXXXXX` for foreign vendors only; 6-character codes belong to public administration offices and are refused |
| Non-resident vendors | fiscal representative (`fiscal_representative_name`, `fiscal_representative_vat_number`), permanent establishment address (`establishment_*`) | FatturaPA `RappresentanteFiscale`, `StabileOrganizzazione` |
| Invoicing bank details | `iban`, `bic`, `bank_name` | IBAN mod-97 and length per country, BIC format. The IBAN (like `tax_code`) is encrypted at rest by SEC.3 and masked in the audit log. Payout accounts are VEN.6 |

`fiscal_regime` is what the vendor declares on its invoices; the VAT computation regime stays in
`VendorTaxProfile` ([05](05-tax.md)).

Purpose of the e-invoicing fields. The platform issues no invoices (see
[00](00-vision-and-review.md)). The e-invoicing identity is kept because it is useful data: the
operator's accountant needs it to invoice commissions outside the platform, and it goes into the
non-fiscal commission statement and its data export (CMP.2). It also shows the vendor which data
its own invoicing needs.

**Required fields are data, not code.** Every legal field is optional while the profile is
created and edited: a vendor can always open and save an incomplete profile (a value that is
present must be valid), and completeness never blocks onboarding or a status change. What is
required, and for what, is the staff-editable table `LegalFieldRequirement`: country (empty =
all), region (empty = all, EU member states, or outside the EU), legal form (empty = all), field,
purpose (today only `payout`) and an active flag. Staff edit it in the admin and every change is
audited. Rules are **additive**: every active rule that matches the vendor's country, region and
legal form applies, and staff relax a requirement by deactivating its row. The rule on
`sdi_recipient_code` is the delivery channel: a 7-character code, or `0000000` together with a
PEC. A required field holding an invalid value (for example legacy data) counts as incomplete
too.

These rows are the payout gate, and the seeded rows include the DAC7 dataset of
[11](11-compliance.md) CMP.1 for every vendor, non-EU sellers included (owner decision):

| Vendors | Required for a payout |
|---|---|
| Every vendor | registered office street, postal code, city and country; country of tax residence |
| Italy | PEC, codice fiscale, fiscal regime, delivery channel, registered office province |
| Italian companies (not sole proprietors) | REA office, REA number, liquidation status |
| EU member states, Italy included | VAT number, IBAN |
| Outside the EU | IBAN, foreign TIN (`tax_code`) |

So every vendor that passes the gate has a VAT number or a TIN, and the legal name is always
present. Payout code (VEN.6, VEN.7) calls `assert_payout_allowed(vendor)`, which refuses with
the list of missing or invalid fields (and, from VEN.16, with an overdue contract acceptance). A vendor that passes it has the complete DAC7 dataset
(`engine.dac7_missing()` is empty; a permanent establishment is reported only when there is one)
and enough data for a FatturaPA `CessionarioCommittente` block; tests pin both. Saving a
profile (`save_vendor`, every adapter) runs the model validation, so present values are always
valid.

`engine.fatturapa_party()` turns the identity into a FatturaPA party block (transmission format
FPR12, XSD schema 1.2.3; technical specification 1.9.1, usable from 2026-05-15)
(`CedentePrestatore` or `CessionarioCommittente`: `IdFiscaleIVA`, `CodiceFiscale`,
`Anagrafica`, `RegimeFiscale`, `Sede`, `StabileOrganizzazione`, `IscrizioneREA`, `Contatti`);
companion functions give the transmission recipient (`CodiceDestinatario`, `PECDestinatario`)
and the fiscal representative. A foreign `CessionarioCommittente` without a VAT number is
identified by its national TIN in `IdFiscaleIVA`. The platform writes no FatturaPA XML and sends
nothing to SDI: CMP.2 puts these party data in the non-fiscal commission statement export, in a
structure the operator's invoicing tool can map to FatturaPA.

### Vendor legal documents and signature (VEN.5, VEN.13 to VEN.17)

Owner decisions of 2026-11-07. The vendor contract and the other legal documents a vendor
accepts (for example a data processing agreement) are not visitor content. They are versioned
documents with one text per language, handled apart from the CMS pages of
[09](09-multilingual.md) and [10](10-content-seo-ai.md): new CMS content stays English only until
after go-live (existing IT pages are migrated, D-18), while vendor legal documents exist in every vendor language from the start (IT,
EN, DE, FR, ES). The legal translation of the texts is the owner's business task and a go-live
prerequisite; the platform only stores and serves the texts it is given.

Language rule. The vendor accepts the version in its own language, which is the signer's
preferred language; the signer may switch to any other language that has a published text. Each
version declares a prevailing language, which wins if the texts differ, and every rendered PDF
states it in a fixed clause. When no text exists in the vendor's language, the English text is
shown and the signer must tick an explicit acknowledgement that it accepts the English version;
the acceptance records that flag. Machine drafts are never offered for acceptance: only texts in
the `published` status count. The exact accepted document is stored: language, text hash, the
rendered PDF and its SHA-256 hash.

Personalization (VEN.13) has three levels, and the hash and timestamp always cover the exact
personalized PDF the vendor accepted:

1. Templates. Each version of `VendorContract` holds one template text per language with merge
   fields from a fixed list: the VEN.12 legal identity (legal name, VAT number, codice fiscale or
   TIN, registered office, REA, PEC, legal representative), the vendor's plan and current
   commission rule (VEN.2) and the date. An unknown or missing merge field blocks issuing the
   document; it never renders as an empty string. The PDF is rendered per vendor and language
   through our own `DocumentRenderer` interface (WeasyPrint as the first implementation) and
   stored in the private bucket (ARC.15).
2. Vendor clauses. Staff can add clauses or annexes to one vendor's contract (negotiated terms),
   with one text per language. A clause needs four-eyes approval: a second staff member, never
   its author, approves it before it can be included. Both steps are audited (SEC.4). Approved
   clauses are rendered into that vendor's PDF after the template text.
3. Custom contract. For a specific vendor staff can upload a fully custom contract as PDF, one
   file per language, with its prevailing language (for example a contract drafted by a lawyer).
   It goes through the same four-eyes approval, signature, timestamp, evidence and re-acceptance
   flow as a rendered one.

What a vendor accepts is therefore a `VendorContractDocument`: the per-vendor issue of a template
version with the vendor's approved clauses, or a custom upload. A new template version, or a
change to a vendor's clauses, issues a new document for each vendor concerned, which must be
re-accepted under the deadline rules below. A change to the vendor's own merge-field data (for
example a new address) does not issue a new document by itself; staff may issue one.

Clauses that need specific approval (owner decision of 2026-11-07). Under articles 1341 comma 2
and 1342 of the Italian civil code, onerous clauses ("clausole vessatorie") in standard terms
drafted by one party have no effect unless the other party approves them specifically in
writing. The rule applies between businesses too, so it covers vendor contracts. Each template
version therefore marks its clausole vessatorie (VEN.13): a marked clause has a number, a title
and a full text per language, and the template body places it with a clause tag, so the PDF and
the approval screen show the same text. A staff-added vendor clause can be marked too, with its
number, through the same four-eyes approval. Every rendered PDF of a document with marked
clauses has:

1. Two signature blocks: the first for the whole contract, the second with the formula "Ai sensi
   e per gli effetti degli artt. 1341 e 1342 c.c. si approvano specificamente le clausole n. ..."
   followed by the clause numbers (translated in the other languages, the Italian formula always
   printed).
2. A fixed clause, with text supplied by counsel, telling the vendor that the specific approval
   is complete only when it signs with a qualified or advanced electronic signature or sends the
   paper original signed by hand, within the deadline and with the consequence stated below.
3. A footer on every page with the document number, language, page count and a short PDF hash,
   so a printed and signed copy can be matched to the stored file.

The renderer also produces a one-page specific approval sheet per language (the formula, the
numbers, titles and full texts of the marked clauses, and the SHA-256 of the main PDF), which a
vendor can sign alone with a qualified or advanced signature.

Data model (VEN.5 has the first three models on its branch; the other tasks add the rest):

```python
class VendorContract(Model):            # VEN.5: a template version, locked once accepted
    version (UNIQUE, auto); valid_from; note
    kind = CharField(choices=["vendor_agreement", "data_processing", ...])  # VEN.13
    prevailing_language = LanguageField()                 # VEN.13: wins if the texts differ
    reacceptance_deadline_days = PositiveIntegerField()   # VEN.16: default 30 (owner question)
    default_consequence = CharField(choices=CONSEQUENCES) # VEN.16
    specific_approval_deadline_days = PositiveIntegerField()    # VEN.16: owner question
    specific_approval_consequence = CharField(choices=CONSEQUENCES)  # VEN.16: owner question
    specific_approval_routes = ArrayField(choices=ROUTES)  # VEN.16: default all three

class VendorContractTranslation(TranslationModel):   # VEN.5: one template text per language
    contract; language; title; body                   # body holds merge fields from VEN.13

class MarkedClause(Model):              # VEN.13: a clausola vessatoria of a template version
    contract; number (UNIQUE per contract); position
    # MarkedClauseTranslation, one per language: title; text (placed in the body by a clause tag)

class VendorContractClause(Model):      # VEN.13: negotiated terms for one vendor
    vendor; title and body per language (VendorContractClauseTranslation)
    status = CharField(choices=["draft", "approved", "withdrawn"])
    created_by; approved_by (never created_by); approved_at   # four-eyes, audited
    needs_specific_approval: bool; number               # marked as a clausola vessatoria
    # an approved clause is never edited: a change is a new clause that supersedes it

class VendorSignaturePolicy(Model):     # VEN.16: per-vendor override, audited with a reason
    vendor (1:1); specific_approval_routes; set_by; reason

class VendorContractDocument(Model):    # VEN.13: what one vendor accepts, numbered per vendor
    vendor; number (UNIQUE per vendor)
    contract = FK(VendorContract, null=True)          # null for a custom upload
    clauses = M2M(VendorContractClause)               # approved clauses included
    source = CharField(choices=["template", "custom_upload"])
    prevailing_language
    reacceptance_deadline_days; default_consequence   # copied from the version, staff may change
    specific_approval_deadline_days; specific_approval_consequence  # same rule
    specific_approval_routes          # from VendorSignaturePolicy if set, else from the version
    marked_clauses (JSON snapshot: number, title per language, text SHA-256)  # empty = no second act
    created_by; approved_by                           # four-eyes for clauses and custom uploads
    # VendorContractDocumentFile, one per language: pdf (private storage), pdf_sha256,
    # approval_sheet_pdf, approval_sheet_sha256, merge_data (JSON snapshot), rendered_at

class ContractAcceptance(Model):        # VEN.5: append-only, one per vendor and document
    vendor; contract; user; language; text_hash; accepted_at; ip
    # VEN.14 adds, still written once at acceptance (the first act):
    document = FK(VendorContractDocument); pdf_sha256
    method = CharField(choices=["email_otp", "qualified_signature", "advanced_signature"])
    member_role                         # the signer's VendorMember role at that moment
    english_fallback_acknowledged: bool
    user_agent; otp = FK(SignatureOtp, null=True); mfa_step_up_at

class SpecificApproval(Model):          # VEN.14: append-only, the second act, online record
    acceptance (1:1); user; member_role; clause_numbers (JSON); clauses_sha256
    approved_at; ip; user_agent; otp = FK(SignatureOtp)   # a second, separate code

class SignatureOtp(Model):              # VEN.14: one-time code sent by email
    user; vendor; document; language; pdf_sha256; code_hash; sent_to
    purpose = CharField(choices=["acceptance", "specific_approval"])  # a code serves one act
    created_at; expires_at; attempts; verified_at

class AcceptanceEvidence(Model):        # VEN.14: append-only, one per act
    acceptance; act = CharField(choices=["acceptance", "specific_approval"])  # UNIQUE together
    manifest (JSON); manifest_sha256    # the second manifest includes the first one's hash
    tsa; timestamp_token (RFC 3161 response, DER); timestamped_at
    package (private storage); package_sha256   # built once both acts are recorded

class DigitalSignature(Model):          # VEN.17: append-only, a verified signed upload
    acceptance; signed_file (private storage); file_sha256
    format = CharField(choices=["pades", "cades_p7m"])
    level = CharField(choices=["qualified", "advanced"])
    scope = CharField(choices=["whole_document", "approval_sheet"])
    signer_name; certificate (DER); issuer; trusted_list_status; verified_at; report (JSON)

class PaperOriginal(Model):             # VEN.16: the hand-signed paper route, staff moderated
    requirement; status = CharField(choices=["awaiting", "received", "approved", "rejected"])
    scan (private storage, null); scan_sha256; received_on; archive_location
    reviewed_by; reviewed_at; rejection_reason   # every transition audited

class VendorContractRequirement(Model): # VEN.16: a vendor must (re-)accept or complete a document
    vendor; document; deadline; consequence (null = the document's default)
    kind = CharField(choices=["acceptance", "specific_approval"])
    route = CharField(choices=ROUTES, null=True)   # specific_approval only: the vendor's choice
    status = CharField(choices=["pending", "satisfied", "overdue", "excluded"])
    digital_signature = FK(DigitalSignature, null=True)   # or the approved PaperOriginal
    reminders_sent_at (JSON); consequence_applied_at; lifted_at; reason
```

`ROUTES` are `qualified`, `advanced` and `paper`.

`CONSEQUENCES` are `warn` (banner and email only), `block_payouts`, `block_sales` (listings
hidden and new orders refused), `block_payouts_and_sales` and `suspend` (the VEN.1 status
machine moves the vendor to `suspended`). VEN.5's unique constraint on vendor and version becomes
vendor and document, and `contract` becomes nullable for custom uploads; the append-only trigger
and the lock on accepted versions stay. The legal representative is a new VEN.12 identity field
added by VEN.13.

Signature flow at go-live (VEN.14): a simple electronic signature under eIDAS (SES) with an email
one-time code and the existing MFA step-up.

1. The signer opens the document in the vendor area and reads the stored PDF of the exact version
   in its language (inline viewer and download). The page shows the PDF hash and the prevailing
   language.
2. The signer ticks the acceptance box, plus the English acknowledgement when the vendor's
   language has no text, and clicks accept. A CORE.4 step-up is required if the last one is
   older than the step-up window.
3. A 6-digit one-time code is sent by email to the signer's address, in its language. It is
   stored hashed, valid for 10 minutes with 5 attempts, and bound to the user, vendor, document,
   language and PDF hash, so a code cannot confirm a different file.
4. With the correct code, one transaction writes the `ContractAcceptance` with the evidence: user,
   VendorMember role, time, IP from the trusted-proxy client IP (CORE.5, `apps.core.http.client_ip`),
   user agent, OTP verification id, language, text and PDF hashes, and an AuditLog entry. A
   wrong, expired or reused code writes nothing but the failed attempt. This is the first act.
5. If the document has marked clauses, the second act follows on a separate screen, reachable
   only after the first act is recorded. It lists only the marked clauses (number, title, full
   text) and the formula of the second signature block, with its own checkbox, never pre-ticked,
   and its own confirm button. Confirming sends a second, separate one-time code with purpose
   `specific_approval`; the first act's code is refused here and the other way round. With the
   correct code one transaction writes the `SpecificApproval` with the same evidence (user, role,
   time, IP, user agent, OTP verification id, clause numbers and the hash of their texts). No
   page, form or API call records both acts in one action, and the second act cannot be pre-filled
   from the first. A signer who leaves before the second act finds it pending in the vendor area.
6. A job (ARC.9) builds the evidence manifest of each act (JSON with the data above; the second
   manifest includes the hash of the first), requests an RFC 3161 timestamp on its SHA-256
   through our own `TimestampAuthority` interface, and, once both acts are recorded, builds the
   evidence package: a ZIP with the PDF, the approval sheet, both manifests, both timestamp
   tokens and a readable summary PDF. If the TSA is down the job retries and alerts staff after
   the last retry; the act stands and the timestamp follows. The package is emailed to the signer
   and the vendor's contact address and stays in the vendor's document area (VEN.15).

Both acts online are enough for the vendor to start operating (VEN.5 activation) or to satisfy a
re-acceptance requirement. They are the online record of the vendor's intent, but the specific
approval counts as completed only after one of the two routes below; until then a
`specific_approval` requirement is pending (VEN.16).

Completing the specific approval (owner decision of 2026-11-07). After the second act the vendor
chooses a route, and may change it while the requirement is pending:

- Qualified or advanced electronic signature (VEN.17). The vendor signs the whole PDF, or only
  the approval sheet, and uploads it. No paper is needed.
- Paper original. The vendor prints the PDF, signs both signature blocks by hand (legal
  representative or another authorized signer), and sends the original by post or courier to the
  address shown on the page; it may upload a scan first. A staff moderator checks the original on
  receipt against the stored PDF (document number, language, page footers with the short hash,
  both blocks signed) and approves it, recording the date of receipt, the scan (uploaded by staff
  if the vendor did not) and the physical archive location, or rejects it with a reason. Each
  transition is audited; four-eyes is not required, since the moderator records a physical fact
  and the scan stays as its evidence.

Staff can narrow the allowed routes per contract version or per vendor (`VendorSignaturePolicy`,
audited with a reason), for example to require a qualified signature and exclude paper.

The email one-time code flow sits behind a `SignatureProvider` interface with only the built-in
implementation, so a third-party e-signature provider (for example Yousign, Namirial or InfoCert)
can be added later without changing the vendor pages. No provider is planned for go-live.

Qualified and advanced digital signature (VEN.17, P1 since it is one of the two completion
routes). A vendor with a qualified or advanced signature can download the PDF or the approval
sheet, sign it (PAdES, or CAdES as `.p7m`) and upload it. Qualified signatures created with a
European Digital Identity Wallet (eIDAS as amended by Regulation (EU) 2024/1183; member states
must offer wallets by 2026-12-24, with free qualified signatures for natural persons, which a
member state may limit to non-professional use) are ordinary PAdES or CAdES signatures and are
accepted the same way. The upload is accepted only if it signs
the exact stored file (same hash for CAdES, unchanged signed revision for PAdES) and the
signature and certificate verify (pyHanko, with the EU trusted lists for the qualified status; an
advanced signature only from a trust anchor staff have listed). It is stored as a
`DigitalSignature` that completes the specific approval; a signed whole document is also a
stronger acceptance of the first act, or the acceptance itself with method `qualified_signature`
or `advanced_signature`.

Vendor document area (VEN.15). Every vendor can download all its accepted documents: the PDF,
the approval sheet, the evidence package, the timestamp tokens, any uploaded signed file and the
scan of an approved paper original, through short-lived
signed URLs from the private bucket (ARC.15), scoped to the vendor (CORE.3). The files are kept
for the retention period of [11](11-compliance.md), also after the vendor is closed; staff can
export them.

Contract updates (VEN.16). When a new document is issued for a vendor that accepted an earlier
one, a `VendorContractRequirement` is created with a deadline: the document's default
(`reacceptance_deadline_days` from the issue date) unless staff set another date for that vendor,
shorter for urgent cases or extended. The consequence is the document's default unless staff
override it for that vendor. Every change of deadline or consequence is audited with a reason. A
scheduled job (`apps.core.tasks`, ARC.9) sends reminders by email before the deadline and, when
the deadline passes without acceptance, applies the consequence: `assert_payout_allowed` refuses
with the reason, the sales gate `vendor_sales_blocked(vendor)` (read by catalog listings and
checkout) returns true, or the vendor is suspended. Every consequence is audited and reversible by
staff with a reason. Staff have a view of the vendors pending acceptance with their deadline, days
left and the consequence that will apply, with actions to extend, change the consequence or
exclude the vendor (a suspension or closing through VEN.1, with reason). Accepting the document
later lifts the automatic restrictions, including an automatic suspension, but never a suspension
decided by staff.

The same machinery handles the specific approval. When the second act is recorded, a
`specific_approval` requirement is created with the document's `specific_approval_deadline_days`
and `specific_approval_consequence` (proposed default 60 days, to leave time for the post, and
`block_payouts`; the owner decides). It is satisfied by a verified `DigitalSignature` or an
approved `PaperOriginal`; a rejected original leaves it pending, and the vendor is emailed the
reason. The staff pending view shows, for each vendor, the requirement kind, the chosen route and
the paper status (awaiting, received, approved, rejected with reason), with actions to record
receipt, upload the scan, approve or reject.

⚖️ P2B (regulation 2019/1150) requires at least 15 days' notice of changes to
the terms (article 3(2)), so a new version's `valid_from` is at least 15 days after it is issued
unless a legal obligation requires otherwise. It also requires a statement of reasons on a
restriction or suspension and 30 days' notice before terminating a vendor (article 4); the VEN.1
status machine and VEN.16 consequences record the reason and the notice date (CMP.5). The
Commission's Digital Omnibus proposal of 2025-11-19 would repeal parts of P2B; until it is
adopted the regulation applies in full.

### Vendor dashboard (bare minimum)

Own orders and order lines, entry of shipping quotes, products (editing of texts, price and stock
if the vendor is not fed by a pipeline), balance and payouts, documents (accepted contracts and their
evidence from VEN.15, non-fiscal commission statements from CMP.2, obligations checklist from CMP.10, plan fee statements from VEN.19),
tax regime change request (approved by staff, as today), store policies per language.

## 4.3 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| VEN.1 | Vendor models, translations and policies, statuses, slug | P1 | 8 | ARC.6 | Admin CRUD + status tests |
| VEN.2 | VendorPlan and versioned CommissionRule with constraints (no implicit zero rule) | P1 | 6 | VEN.1 | Tests on overlapping validity periods |
| VEN.3 | `compute_commission` pure function + real-case suite (REGOLA-ZERO, PERC-CONGELATA, FEE-NETTO) | P1 | 8 | VEN.2 | All 4.4 cases green |
| VEN.4 | Append-only LedgerEntry (DB constraint/trigger), balance, per-vendor report | P1 | 10 | VEN.3 | UPDATE/DELETE impossible; balance = sum |
| VEN.5 | Application → onboarding → active flow, with versioned contract and DAC7 checklist | P1 | 12 | VEN.1 | Test vendor activated end-to-end |
| VEN.6 | Stripe Express onboarding (account link, status, `account.updated` webhook) | P1 | 8 | PAY.1 | Account status synchronized |
| VEN.7 | Payouts: eligibility, block if DAC7 incomplete (`assert_payout_allowed`), history | P2 | 8 | VEN.4, PAY.3, VEN.12 | Tests on every blocking condition |
| VEN.8 | Nightly ledger ↔ Stripe reconciliation with anomaly report | P1 | 10 | VEN.4, PAY.3 | Simulated discrepancy → alert |
| VEN.9 | Vendor dashboard: balance, transactions, payouts, documents | P1 | 10 | CORE.9, VEN.4 | Vendor sees only their own transactions |
| VEN.10 | Public vendor storefront per language (bio, policies, products, store categories) | P1 | 8 | CAT.8 | Category sidebar correct in IT too (WCFM bug solved by design) |
| VEN.11 | Tax regime change request by vendor with staff review and email | P2 | 6 | TAX.2, ARC.16 | Parity with VTO 2.4 |
| VEN.12 | Vendor legal and e-invoicing identity (EU + IT): legal form, structured address, VAT with VIES status, codice fiscale, fiscal regime RF01-RF19, REA, SDI recipient code, PEC, EORI, fiscal representative, permanent establishment, invoicing bank details; required fields per country, region (EU or non-EU) and legal form, including the DAC7 dataset for every vendor, defined in a staff-editable table, enforced when the vendor requests a payout | P1 | 8 | VEN.1 | A vendor's data fills a valid FatturaPA party block; a vendor missing a required field for its country cannot request a payout, but can open and edit its profile |
| VEN.13 | Contract templates with merge fields, vendor-specific clauses with four-eyes approval, custom uploaded contracts: per-vendor `VendorContractDocument` rendered to PDF per language through a `DocumentRenderer` interface (WeasyPrint), prevailing language, legal representative field on the VEN.12 identity; clausole vessatorie marked on templates and vendor clauses (number, title, text), two signature blocks with the art. 1341 and 1342 c.c. formula, the fixed clause on how to complete the specific approval, page footers with the short hash, one-page specific approval sheet | P1 | 18 | VEN.5, VEN.2, VEN.12, ARC.15 | A test vendor's IT and EN PDFs carry its legal identity, commission rule and approved clauses; a missing merge field blocks issuing; a clause or custom upload approved by its own author is refused; a new template version or clause issues a new document for each vendor concerned; a document with marked clauses renders two signature blocks whose formula lists exactly their numbers, and an approval sheet with their full texts and the main PDF hash |
| VEN.14 | Vendor contract e-signature in two separate acts: acceptance of the whole contract (checkbox, email OTP, MFA step-up), then the specific approval of the marked clauses on a separate screen with a second, separate email OTP; email OTP simple electronic signature, PDF of the exact version per language with SHA-256 hash and RFC 3161 timestamp per act behind a `TimestampAuthority` interface, evidence per act (user, member role, time, trusted-proxy IP, user agent, OTP id), evidence package by email, completion route choice, `SignatureProvider` interface | P1 | 22 | VEN.13, CORE.4, CORE.5, ARC.9, ARC.16 | A test vendor signs end to end; the stored PDF matches its hash and both timestamp tokens verify against their evidence manifests (fake TSA in tests, a real TSA on STAGE); the evidence email arrives in the signer's language; without a text in its language the vendor can accept only after acknowledging English; a wrong, expired or reused code records no acceptance; the specific approval needs its own screen and its own code, the acceptance code is refused for it, and no single request records both acts; the OTP fields accept paste and carry `autocomplete="one-time-code"` (WCAG 2.2 SC 3.3.8, spec 14 14.6) |
| VEN.15 | Vendor document area: download accepted contracts and evidence (PDF, approval sheet, evidence package, timestamp tokens, uploaded signed file, paper original scan) through signed URLs, kept per the retention rules | P1 | 6 | VEN.14, CORE.9 | A vendor downloads all and only its own accepted documents; another vendor's file gives 404; files stay downloadable for staff after the vendor is closed |
| VEN.16 | Contract updates and specific approval completion: re-acceptance deadlines per vendor, reminders, automatic consequences, specific approval pending requirement with its own deadline and consequence, paper original route with staff moderation (receipt date, scan, archive location, approve or reject with reason), completion routes per version and per vendor, staff pending view with route and paper status | P1 | 20 | VEN.14, ARC.9, SEC.4, ARC.16 | A vendor that misses its deadline gets the configured consequence (warn, payouts blocked, sales blocked or suspended); staff can extend the deadline, change the consequence or exclude the vendor, all audited with a reason; accepting the document lifts the automatic restrictions but not a staff suspension; a vendor with both acts online but no completion gets the specific approval consequence at its deadline, and a staff-approved paper original lifts it; a rejected original stays pending and the vendor is emailed the reason |
| VEN.17 | Qualified and advanced digital signature upload and verification (PAdES/CAdES) of the whole document or of the specific approval sheet, pyHanko, EU trusted lists for qualified and staff-listed trust anchors for advanced; completes the specific approval and is stored as a stronger acceptance | P1 | 18 | VEN.14, VEN.16 | A PAdES and a `.p7m` test file signed with a certificate from a test trusted list are accepted; a file that signs a different PDF, an altered file or an untrusted certificate is refused with the reason; a signed approval sheet satisfies the vendor's specific approval requirement |
| VEN.18 | Fiscal regime RF20 (cross-border VAT franchise, Directive (EU) 2020/285, FatturaPA specification 1.9 from 2025-04-01): add the choice with its official SDI label, update the check constraint with a data migration, and record the FatturaPA specification version (1.9.1) the party block follows | P1 | 2 | VEN.12 | A vendor can save RF20; the constraint accepts RF01–RF20 except RF03; `fatturapa_party()` emits `RegimeFiscale` RF20; a test pins the label list to the specification version |
| VEN.19 | Plan entitlements and plan fees: `plan_allows(vendor, feature)` service over `VendorPlan.features`, plan change with effective date and audit; monthly `plan_fee` ledger debit by a scheduled idempotent job; non-fiscal statement PDF per charge in the vendor's language (no invoice issued by the marketplace; the operator invoices outside the platform) | P2 | 8 | VEN.2, VEN.4, VEN.15, ARC.9 | A vendor whose plan lacks a feature cannot use it and keeps its data; a plan with a monthly fee gets exactly one `plan_fee` entry per period even when the job runs twice; the statement PDF states that it is not a tax invoice and matches the ledger entry; no fiscal document or e-invoice is produced |

## 4.4 Mandatory regression cases

1. Example: 20% commission on €100 net goods, €122 gross: commission 20.00; payment fee 122 × 4% + 2 = 6.88; vendor net 95.12.
2. Case FEE-NETTO: the payment fee is computed on the gross (9.3424, rounded), not on the already-reduced net.
3. No VAT on the commission and no VAT on the payment fee (platform's current tax regime).
4. Missing rule → blocking error, never a silent 0 commission (case REGOLA-ZERO).
5. The percentage frozen in the order is the one valid on the order date (case PERC-CONGELATA).
6. Payment without split is not possible: a single collection flow (case NO-SPLIT).
7. A change of the platform's regime (e.g. VAT on commissions after a relocation) is
   a `PlatformOperator` configuration, not a code change.
