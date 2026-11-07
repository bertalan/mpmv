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

**Historical bugs** (they become tests, see §4.4): `tax_enable` read with `isset()`; transaction charge
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
    max_products (null = unlimited); features (JSON: featured, api_sync, custom_tax…)
    monthly_fee (MoneyField, null)             # plan fee billing: phase 2 (D-04)

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
                              "commission_reversal", "adjustment", "payout", "transfer"])
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

### Legal and e-invoicing identity (VEN.12)

The vendor's legal identity lives on `Vendor` itself, not in a separate profile row: spec 04
already lists these fields on the vendor, DAC7 (CMP.1) reads them from it, and a 1:1 table would
only add a "profile row missing" state.

| Group | Fields | Rules |
|---|---|---|
| Legal form | `legal_form` (sole proprietorship, partnership, S.r.l., S.p.A., cooperative, other or foreign); `first_name`, `last_name` for sole proprietors | FatturaPA `Anagrafica`: `Nome` + `Cognome` for a sole proprietor, `Denominazione` (the legal name) otherwise |
| Registered office | `registered_street`, `registered_street_number`, `registered_postal_code`, `registered_city`, `registered_province`, `registered_country` | Italy: 5-digit postal code and official 2-letter province code. Replaces the former JSON address (data-preserving migration) |
| Tax identity | `vat_number` with `vies_status` (unchecked, valid, invalid, unavailable) and `vies_checked_at`; `tax_code` (codice fiscale in Italy, the national TIN elsewhere); `tax_residence_country` (DAC7); `fiscal_regime` (RF01–RF19 with the official SDI labels; there is no RF03; RF19 = forfettario); `ateco_code`; `eori` | Italian VAT checksum; codice fiscale check character (omocodia included) or the 11-digit numeric form. VIES is reached only through the `VatNumberValidator` interface; changing the VAT number resets the status to unchecked |
| Company register (REA) | `rea_office` (province), `rea_number`, `share_capital`, `sole_shareholder` (SU/SM), `liquidation_status` (LS/LN) | FatturaPA `IscrizioneREA` |
| E-invoicing | `sdi_recipient_code`, `pec` | 7 characters; `0000000` when invoices go to the PEC; `XXXXXXX` for foreign vendors only; 6-character codes belong to public administration offices and are refused |
| Non-resident vendors | fiscal representative (`fiscal_representative_name`, `fiscal_representative_vat_number`), permanent establishment address (`establishment_*`) | FatturaPA `RappresentanteFiscale`, `StabileOrganizzazione` |
| Invoicing bank details | `iban`, `bic`, `bank_name` | IBAN mod-97 and length per country, BIC format. The IBAN (like `tax_code`) is encrypted at rest by SEC.3 and masked in the audit log. Payout accounts are VEN.6 |

`fiscal_regime` is what the vendor declares on its invoices; the VAT computation regime stays in
`VendorTaxProfile` ([05](05-tax.md)).

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

`engine.fatturapa_party()` turns the identity into a FatturaPA 1.2.x party block
(`CedentePrestatore` or `CessionarioCommittente`: `IdFiscaleIVA`, `CodiceFiscale`,
`Anagrafica`, `RegimeFiscale`, `Sede`, `StabileOrganizzazione`, `IscrizioneREA`, `Contatti`);
companion functions give the transmission recipient (`CodiceDestinatario`, `PECDestinatario`)
and the fiscal representative. A foreign `CessionarioCommittente` without a VAT number is
identified by its national TIN in `IdFiscaleIVA`. Writing the XML is CMP.2.

### Vendor legal documents and signature (VEN.5, VEN.13 to VEN.17)

Owner decisions of 2026-11-07. The vendor contract and the other legal documents a vendor
accepts (for example a data processing agreement) are not visitor content. They are versioned
documents with one text per language, handled apart from the CMS pages of
[09](09-multilingual.md) and [10](10-content-seo-ai.md): CMS content stays English only until
after go-live, while vendor legal documents exist in every vendor language from the start (IT,
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

Data model (VEN.5 has the first three models on its branch; the other tasks add the rest):

```python
class VendorContract(Model):            # VEN.5: a template version, locked once accepted
    version (UNIQUE, auto); valid_from; note
    kind = CharField(choices=["vendor_agreement", "data_processing", ...])  # VEN.13
    prevailing_language = LanguageField()                 # VEN.13: wins if the texts differ
    reacceptance_deadline_days = PositiveIntegerField()   # VEN.16: default 30 (owner question)
    default_consequence = CharField(choices=CONSEQUENCES) # VEN.16

class VendorContractTranslation(TranslationModel):   # VEN.5: one template text per language
    contract; language; title; body                   # body holds merge fields from VEN.13

class VendorContractClause(Model):      # VEN.13: negotiated terms for one vendor
    vendor; title and body per language (VendorContractClauseTranslation)
    status = CharField(choices=["draft", "approved", "withdrawn"])
    created_by; approved_by (never created_by); approved_at   # four-eyes, audited
    # an approved clause is never edited: a change is a new clause that supersedes it

class VendorContractDocument(Model):    # VEN.13: what one vendor accepts, numbered per vendor
    vendor; number (UNIQUE per vendor)
    contract = FK(VendorContract, null=True)          # null for a custom upload
    clauses = M2M(VendorContractClause)               # approved clauses included
    source = CharField(choices=["template", "custom_upload"])
    prevailing_language
    reacceptance_deadline_days; default_consequence   # copied from the version, staff may change
    created_by; approved_by                           # four-eyes for clauses and custom uploads
    # VendorContractDocumentFile, one per language: pdf (private storage), pdf_sha256,
    # merge_data (JSON snapshot of the merge fields), rendered_at

class ContractAcceptance(Model):        # VEN.5: append-only, one per vendor and document
    vendor; contract; user; language; text_hash; accepted_at; ip
    # VEN.14 adds, still written once at acceptance:
    document = FK(VendorContractDocument); pdf_sha256
    method = CharField(choices=["email_otp", "qualified_signature"])
    member_role                         # the signer's VendorMember role at that moment
    english_fallback_acknowledged: bool
    user_agent; otp = FK(SignatureOtp, null=True); mfa_step_up_at

class SignatureOtp(Model):              # VEN.14: one-time code sent by email
    user; vendor; document; language; pdf_sha256; code_hash; sent_to
    created_at; expires_at; attempts; verified_at

class AcceptanceEvidence(Model):        # VEN.14: append-only, one per acceptance
    acceptance (1:1); manifest (JSON); manifest_sha256
    tsa; timestamp_token (RFC 3161 response, DER); timestamped_at
    package (private storage); package_sha256

class QualifiedSignature(Model):        # VEN.17 (P2): append-only, a stronger acceptance
    acceptance; signed_file (private storage); file_sha256
    format = CharField(choices=["pades", "cades_p7m"])
    signer_name; certificate (DER); issuer; trusted_list_status; verified_at; report (JSON)

class VendorContractRequirement(Model): # VEN.16: a vendor must (re-)accept a document
    vendor; document; deadline; consequence (null = the document's default)
    status = CharField(choices=["pending", "accepted", "overdue", "excluded"])
    reminders_sent_at (JSON); consequence_applied_at; lifted_at; reason
```

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
   wrong, expired or reused code writes nothing but the failed attempt.
5. A job (ARC.9) builds the evidence manifest (JSON with the data above), requests an RFC 3161
   timestamp on its SHA-256 through our own `TimestampAuthority` interface, and builds the
   evidence package: a ZIP with the PDF, the manifest, the timestamp token and a readable summary
   PDF. If the TSA is down the job retries and alerts staff after the last retry; the acceptance
   stands and the timestamp follows. The package is emailed to the signer and the vendor's contact
   address and stays in the vendor's document area (VEN.15).

The email one-time code flow sits behind a `SignatureProvider` interface with only the built-in
implementation, so a third-party e-signature provider (for example Yousign, Namirial or InfoCert)
can be added later without changing the vendor pages. No provider is planned for go-live.

Qualified digital signature (VEN.17, P2). A vendor with a qualified signature can download the
PDF, sign it (PAdES, or CAdES as `.p7m`) and upload it. The upload is accepted only if it signs
the exact stored PDF (same hash for CAdES, unchanged signed revision for PAdES) and the signature
and certificate verify (pyHanko, with the EU trusted lists for the qualified status). It is stored
as a stronger acceptance: next to an email one-time code acceptance, or as the acceptance itself
with method `qualified_signature`.

Vendor document area (VEN.15). Every vendor can download all its accepted documents: the PDF,
the evidence package, the timestamp token and any uploaded signed file, through short-lived
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
decided by staff. ⚖️ P2B (regulation 2019/1150) requires at least 15 days' notice of changes to
the terms, so a new version's `valid_from` is at least 15 days after it is issued unless a legal
obligation requires otherwise.

### Vendor dashboard (bare minimum)

Own orders and order lines, entry of shipping quotes, products (editing of texts, price and stock
if the vendor is not fed by a pipeline), balance and payouts, documents (accepted contracts and their
evidence from VEN.15, commission invoices),
tax regime change request (approved by staff, as today), store policies per language.

## 4.3 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| VEN.1 | Vendor models, translations and policies, statuses, slug | P1 | 8 | ARC.6 | Admin CRUD + status tests |
| VEN.2 | VendorPlan and versioned CommissionRule with constraints (no implicit zero rule) | P1 | 6 | VEN.1 | Tests on overlapping validity periods |
| VEN.3 | `compute_commission` pure function + real-case suite (REGOLA-ZERO, PERC-CONGELATA, FEE-NETTO) | P1 | 8 | VEN.2 | All §4.4 cases green |
| VEN.4 | Append-only LedgerEntry (DB constraint/trigger), balance, per-vendor report | P1 | 10 | VEN.3 | UPDATE/DELETE impossible; balance = sum |
| VEN.5 | Application → onboarding → active flow, with versioned contract and DAC7 checklist | P1 | 12 | VEN.1 | Test vendor activated end-to-end |
| VEN.6 | Stripe Express onboarding (account link, status, `account.updated` webhook) | P1 | 8 | PAY.1 | Account status synchronized |
| VEN.7 | Payouts: eligibility, block if DAC7 incomplete (`assert_payout_allowed`), history | P2 | 8 | VEN.4, PAY.3, VEN.12 | Tests on every blocking condition |
| VEN.8 | Nightly ledger ↔ Stripe reconciliation with anomaly report | P1 | 10 | VEN.4, PAY.3 | Simulated discrepancy → alert |
| VEN.9 | Vendor dashboard: balance, transactions, payouts, documents | P1 | 10 | CORE.9, VEN.4 | Vendor sees only their own transactions |
| VEN.10 | Public vendor storefront per language (bio, policies, products, store categories) | P1 | 8 | CAT.8 | Category sidebar correct in IT too (WCFM bug solved by design) |
| VEN.11 | Tax regime change request by vendor with staff review and email | P2 | 6 | TAX.2 | Parity with VTO 2.4 |
| VEN.12 | Vendor legal and e-invoicing identity (EU + IT): legal form, structured address, VAT with VIES status, codice fiscale, fiscal regime RF01-RF19, REA, SDI recipient code, PEC, EORI, fiscal representative, permanent establishment, invoicing bank details; required fields per country, region (EU or non-EU) and legal form, including the DAC7 dataset for every vendor, defined in a staff-editable table, enforced when the vendor requests a payout | P1 | 8 | VEN.1 | A vendor's data fills a valid FatturaPA party block; a vendor missing a required field for its country cannot request a payout, but can open and edit its profile |
| VEN.13 | Contract templates with merge fields, vendor-specific clauses with four-eyes approval, custom uploaded contracts: per-vendor `VendorContractDocument` rendered to PDF per language through a `DocumentRenderer` interface (WeasyPrint), prevailing language, legal representative field on the VEN.12 identity | P1 | 16 | VEN.5, VEN.2, VEN.12, ARC.15 | A test vendor's IT and EN PDFs carry its legal identity, commission rule and approved clauses; a missing merge field blocks issuing; a clause or custom upload approved by its own author is refused; a new template version or clause issues a new document for each vendor concerned |
| VEN.14 | Vendor contract e-signature: email OTP simple electronic signature with MFA step-up, PDF of the exact version per language with SHA-256 hash and RFC 3161 timestamp behind a `TimestampAuthority` interface, evidence (user, member role, time, trusted-proxy IP, user agent, OTP id), evidence package by email, `SignatureProvider` interface | P1 | 18 | VEN.13, CORE.4, CORE.5, ARC.9 | A test vendor signs end to end; the stored PDF matches its hash and the timestamp token verifies against the evidence manifest (fake TSA in tests, a real TSA on STAGE); the evidence email arrives in the signer's language; without a text in its language the vendor can accept only after acknowledging English; a wrong, expired or reused code records no acceptance |
| VEN.15 | Vendor document area: download accepted contracts and evidence (PDF, evidence package, timestamp token, uploaded signed file) through signed URLs, kept per the retention rules | P1 | 6 | VEN.14, CORE.9 | A vendor downloads all and only its own accepted documents; another vendor's file gives 404; files stay downloadable for staff after the vendor is closed |
| VEN.16 | Contract updates: re-acceptance deadlines per vendor, reminders, automatic consequences, staff pending view | P1 | 14 | VEN.14, ARC.9, SEC.4 | A vendor that misses its deadline gets the configured consequence (warn, payouts blocked, sales blocked or suspended); staff can extend the deadline, change the consequence or exclude the vendor, all audited with a reason; accepting the document lifts the automatic restrictions but not a staff suspension |
| VEN.17 | Qualified digital signature upload and verification (PAdES/CAdES), pyHanko, EU trusted lists, stored as a stronger acceptance | P2 | 14 | VEN.14 | A PAdES and a `.p7m` test file signed with a certificate from a test trusted list are accepted; a file that signs a different PDF, an altered file or an untrusted certificate is refused with the reason |

## 4.4 Mandatory regression cases

1. Example: 20% commission on €100 net goods, €122 gross: commission 20.00; payment fee 122 × 4% + 2 = 6.88; vendor net 95.12.
2. Case FEE-NETTO: the payment fee is computed on the gross (9.3424, rounded), not on the already-reduced net.
3. No VAT on the commission and no VAT on the payment fee (platform's current tax regime).
4. Missing rule → blocking error, never a silent 0 commission (case REGOLA-ZERO).
5. The percentage frozen in the order is the one valid on the order date (case PERC-CONGELATA).
6. Payment without split is not possible: a single collection flow (case NO-SPLIT).
7. A change of the platform's regime (e.g. VAT on commissions after a relocation) is
   a `PlatformOperator` configuration, not a code change.
