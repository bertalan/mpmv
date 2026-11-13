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
    dac7_complete: bool (derived)              # blocks payouts if False, never activation (Q-56)
    legacy_wp_user_id

class CrossVendorConsent(Model):               # versioned, append-only, audited; default: no row = opted out
    vendor; in_suggestions: bool               # products may appear in other vendors' suggestions (Q-32, CAT.14)
    in_bundles: bool                           # products may be part of others' bundles (CAT.16, P2)
    valid_from; set_by

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
                              "plan_fee", "bundle_discount_vendor_funded",
                              "bundle_discount_platform_funded"])
                              # plan_fee: VEN.19 (P2); bundle discounts: CAT.17 (P2, spec 03)
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
   with the signature flow below, tax regime) → `active`. Activation is refused when neither the
   registered office nor a permanent establishment is in the allowed countries of the
   `PlatformOperator`, the EU-27 at go-live (CMP.13, D-21). Every step is recorded in `AuditLog`.
   The public "Sell with us" page creates the applicant's account and the application (VEN.22).
   Implementation (VEN.22): `/vendor/apply/` asks for the name, email, business name and country
   of establishment (among the allowed countries), behind the per-IP registration limit,
   Turnstile and a body-size cap. It asks for no password: the account is created inactive with
   an unusable password, an `applicant` vendor it owns and a pending marker
   (`VendorApplication`). Only the emailed single-use link (CORE.15, email-bound) leads to the
   confirm page, where whoever opens it chooses the password; that activates the account, clears
   the marker and never signs anyone in. Only an account with the marker can get a link, so a
   blocked or migrated applicant is never reactivated by a form submission. A new application
   for a pending address sends a new link and kills the old one. An address that already has an
   account gets the same answer and a notice by email with no link, so the form never reveals
   accounts. MFA is enrolled at first login (CORE.4, vendor members). Owner decision D-24: the
   confirmation mail and one reminder at 48 h (hourly job) each carry a cancel link ("this was
   not me", POST with CSRF on a button page, idempotent, neutral page) that deletes the account
   and the applicant vendor at once; an hourly job deletes unconfirmed applications 3 days after
   the request (`VENDOR_APPLICATION_RETENTION_DAYS`). A GET of either link changes nothing and
   keeps the token valid (CORE.15 already works this way).
   Missing DAC7 or payout data never block activation: they block payouts only (owner decision
   Q-56, confirming the current `onboarding_checklist` in `apps/vendors/engine.py` and
   `assert_payout_allowed`, VEN.12).
   **Claiming an imported vendor** (owner decision MQ-1, VEN.24). A vendor migrated or created by
   staff without an account holder is claimed through a single-use, expiring secure link
   (CORE.15) that goes only to the vendor's legal email of record or, for a vendor with one on
   file, its registered PEC (certified email, Italian vendors), never to an address typed in a
   request. The claimant sets a password and enrols their own MFA (CORE.4), then confirms the
   VAT number or TIN already stored on the vendor; a mismatch is refused and audited. The claim
   then waits for staff approval, and until staff approve it the claimant cannot change fiscal
   data (legal identity, tax profile) or payout data. Links expire, so staff have a "resend /
   regenerate" action: it issues a new link and invalidates the old one, and both steps are in
   the AuditLog.
   Implementation (VEN.24): the legal email of record is `Vendor.contact_email`; a vendor that
   already has a member, or has neither a VAT number nor a TIN, cannot be claimed. The claim
   page asks for the VAT number or TIN first (spaces, dots, dashes and the country prefix are
   ignored); each wrong answer is audited without the typed value, claim-page POSTs are rate
   limited per IP, and the claim locks after `VENDOR_CLAIM_MAX_FAILURES` (5) wrong answers
   until staff regenerate the link. The password and the authenticator app (TOTP, the key kept
   in the session until its first code checks out) come next; the account, its second factor,
   the spent link and the submitted claim are written in one transaction. An existing account
   with the legal email is never taken over or changed: when one exists (a claimant whose claim
   was rejected and who got a new link, or who already claimed another vendor with the same
   legal email), the claim page asks for that account's own password (the login lockout and
   captcha apply) and a code from its own authenticator app instead, then submits the claim for
   it. The link alone never signs anyone in. The claimant gets no membership until staff approve
   the claim (`VendorClaim`, admin actions on the vendor list), so before approval they reach no
   vendor data at all. Sending or regenerating a link, approving and rejecting need a fresh
   second factor (step-up), and approval is refused when the vendor meanwhile got a member or
   the claimant's email is no longer the address the link went to.
   Destination (decision by lane A, delegated by the owner, on PR #141, confirmed as part of
   MQ-1 at coherence check 7): when the vendor has a PEC address on file, the staff send /
   regenerate dialog may pick it instead of the legal email; no other address can be chosen. The link is then bound to the PEC, the claimant's
   account uses it, and the AuditLog entry names the channel and the masked address.
   After a reject: rejecting a claim invalidates every open claim link of the vendor (audited
   `vendor.claim_link_invalidated`, reason `rejected`). Only staff start a new claim afterwards,
   through the regenerate action behind step-up; its audit entry references the reject (claim,
   time and audit entry). The claimant has no self-service resend.
2. **Paid order**: for each `VendorOrder` the rule snapshot is frozen, `compute_commission` is
   computed, and the ledger rows are written (`sale`, `shipping`, `commission`, `payment_fee`).
3. **Transfer**: with *separate charges and transfers* (see [07](07-cart-orders-payments.md)) a Stripe
   transfer is created per vendor for the `vendor_net` amount, recorded as `transfer`.
4. **Refund**: proportional `refund` and `commission_reversal` rows; current business rule
   owner decision D-05 (2026-11-11): the platform gives back the commission, the collection cost and
   the order-handling fee of the refunded order and charges the vendor a **refund fee** (real provider
   cost from the balance transaction plus a surcharge, default 2 EUR on every refund event, total rounded up to the whole euro,
   per plan; PAY.4, VEN.33). No refund fee on a refund caused by a platform malfunction or error
   (L-10, adviser opinion 2026-11-11); the surcharge is versioned with an effective date and a change needs 15 days' notice.
   Commissions, collection costs, amounts refunded to customers and refund fees are set off
   against transfers by contract (D-08, art. 1252 of the Italian civil code; CMP.5 clause (e)),
   with SEPA debit under the vendor's mandate for a negative balance.
5. **Nightly reconciliation**: ledger ↔ Stripe transfers and balance; discrepancies → alert and report.

### Vendor statuses (owner decision Q-17)

| From | To | Who and when |
|---|---|---|
| `applicant` | `onboarding`, `closed` | Staff approve the application, or reject it (a rejected application is `closed`) |
| `onboarding` | `active`, `closed` | Activation after the checklist and the activation gates (CMP.13); closure |
| `active` | `suspended`, `closed` | Staff suspend (for example expired documents, VEN.16 consequences) or close |
| `suspended` | `active`, `closed` | Reinstatement once the cause is solved; closure |
| `closed` | `onboarding` | Staff reopen a closed vendor, with a mandatory reason recorded in the `AuditLog` (VEN.21) |

A reopened vendor goes back to `onboarding`, never straight to `active`. Time has passed since the
closure: the contract in force may be a newer version than the one the vendor accepted, the DAC7
and payout data may be out of date, and the activation gates (establishment, CMP.13, and the other
platform duties of [11](11-compliance.md)) may have changed. Going through `onboarding` re-runs the checklist (contract
re-acceptance, DAC7 data, payout data) and the activation gates with the code that already
exists, instead of a second activation path. The data kept after closure (owner, ledger,
contracts) are reused; a vendor whose owner asked for erasure (Q-16, [02](02-core-identity-permissions.md))
is reopened only with a new owner. In the code (VEN.21) `closed → onboarding` is the only
transition out of `closed` in `TRANSITIONS` (`apps/vendors/engine.py`) and needs a reason, so
only `reopen_vendor` in `apps/vendors/services.py` makes it: staff only, with dry-run, audited as
`vendor.reopened` with the reason. The vendor's `onboarding_round` goes up and contract
acceptances count for the current round only, so the owner accepts the contract in force again
even when its version has not changed. No API key comes back with a reopen: closing a vendor
revokes its keys (CORE.14), the reopen revokes any key a closure from before that rule left
active, and an owner issues new keys once the vendor is active again.

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
| Tax identity | `vat_number` with `vies_status` (unchecked, valid, invalid, unavailable) and `vies_checked_at`; `tax_code` (codice fiscale in Italy, the national TIN elsewhere); `tax_residence_country` (DAC7); `fiscal_regime` (RF01–RF20 with the official SDI labels; there is no RF03; RF19 = forfettario; RF20 = cross-border VAT franchise for small enterprises, Directive (EU) 2020/285, added by the FatturaPA technical specification 1.9 from 2025-04-01, VEN.18; only for vendors
established in an EU member state and with their "EX" participation number, owner decision MQ-5,
VEN.25; the registered office must be in that member state, a permanent establishment alone does not qualify, owner decision MQ-17); `ateco_code`; `eori` | Italian VAT checksum; codice fiscale check character (omocodia included) or the 11-digit numeric form. VIES is reached only through the `VatNumberValidator` interface; changing the VAT number resets the status to unchecked |
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
present must be valid), and completeness never blocks saving or onboarding. Activation is the
exception: the platform duties of [11](11-compliance.md) (DSA article 30 trader data and
self-certification, CMP.25; the establishment gate, CMP.13) block it. What is
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
| Every vendor | registered office street, postal code, city and country; country of tax residence; TIN issuing country (VEN.23) |
| Sole proprietors | date of birth (DAC7, VEN.23) |
| Every other legal form | business registration number (DAC7, VEN.23) |
| Italy | PEC, codice fiscale, fiscal regime, delivery channel, registered office province |
| Italian companies (not sole proprietors) | REA office, REA number, liquidation status |
| EU member states, Italy included | VAT number, IBAN |
| Outside the EU | IBAN (the TIN, or a place of birth where none is issued, comes from the DAC7 check, VEN.23) |

The "Outside the EU" rows stay at go-live: a vendor with its registered office outside the EU
and a permanent establishment in the EU can be activated (CMP.13), and the rows are ready for the
later opening to non-EU vendors (CMP.17 to CMP.22).

**DAC7 dataset (owner decision MQ-6, VEN.23).** The fields follow Directive (EU) 2021/514,
Annex V, section III.B, for individual and entity sellers alike, and a test pins the list. The TIN
is always asked, together with the country that issued it. The VAT number is asked only when the
vendor has one, and it is checked with VIES (TAX.5). A "same as the VAT number" option covers
companies whose TIN is their VAT number; it is offered only in the Member States where the VAT
number without its prefix is the TIN (`engine.SAME_AS_VAT_COUNTRIES`), and the TIN follows a
definite VIES answer (an unavailable VIES keeps the TIN already derived). Formats are checked
per issuing country. When the issuing country does not issue TINs, an individual gives the place
of birth instead; an entity always gives a TIN (or the identifier its country uses as one).
Staff can correct or complete the dataset from the admin, and create a vendor with DAC7 data,
behind an MFA step-up and with an AuditLog entry for each change (`vendor.dac7_corrected`, or
`vendor.created` for a new vendor). Migration 0016 fills the TIN issuing country of existing
vendors (where the stored TIN is valid for the vendor's country) and an Italian entity's
business registration number from its REA data; any other DAC7 field an existing vendor lacks
blocks its payouts until staff complete it. The DAC7 check (`engine.dac7_check`) is not a requirement row: the payout gate
applies it on top of the active rows, so deactivating a row never lets a vendor with an
incomplete DAC7 dataset through. The rows mirror the dataset for staff.

**DAC7 follow-up (owner decision MQ-16, 2026-11-09).** The list of jurisdictions that issue no
TIN becomes data that staff edit, each change through an audited service with dry-run and an
AuditLog entry, instead of a code constant (VEN.27: table `NoTinCountry`, seeded by migration
0028 with the six countries; the service passes the list to `engine.dac7_check`). The list has
history: a row is valid over `[valid_from, valid_to)` in business-time-zone days, a removal sets
`valid_to` (never deletes), an addition starts a new row from today, rows of one country never
overlap (database constraint), and `engine.no_tin_countries_on(windows, day)` answers the list of
any day. DAC7 report generation (CMP.1) takes the list of the reporting period from it and
snapshots the list and each vendor's completeness at generation time. A change needs an
a passkey (once or an open window; CORE.25 action `no_tin_country`, sensitive). A removal asks staff for a
reason in the admin and shows and records how many vendors it re-blocks; notifying those
vendors is not built yet. The payout task must re-check completeness in the transaction that
creates the payout (time of check to time of use). For an Italian
company the REA number is accepted as the business registration number (Annex V, section
III.B), as migration 0019 already derives it. Still open, not decided: the accountant confirms
the initial no-TIN list and the TIN formats per issuing country; until then the shipped list and
format table stay as they are.

So every vendor that passes the gate has a VAT number or a TIN, except an individual whose TIN
country issues no TIN and who has no VAT number (identified by the place of birth, MQ-6); the
legal name is always present. Payout code (VEN.6, VEN.7) calls `assert_payout_allowed(vendor)`, which refuses with
the list of missing or invalid fields (and, from VEN.16, with an overdue contract acceptance). A vendor that passes it has the complete DAC7 dataset
(`engine.dac7_missing()` is empty; a permanent establishment is reported only when there is one)
and, with that one exception, enough data for a FatturaPA `CessionarioCommittente` block;
tests pin both. Saving a
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

### Operator-owned shop (VEN.26)

Owner note 2026-11-09: the platform operator may also sell on its own marketplace, as a vendor
whose legal identity is the operator's. This is allowed, without conflicts:

- **Detection proposes, the platform owner confirms.** A vendor whose VAT number, or TIN with its
  country, matches the current `PlatformOperator` gets an `operator_owned_proposed` mark when its
  VEN.12 identity is saved or the operator changes. The mark changes nothing by itself: anyone
  can type the operator's VAT number into an application. The platform owner confirms
  `is_operator_owned` in the platform space, with an MFA step-up, a reason and an `AuditLog`
  entry, and only for a vendor whose identity is verified (VEN.12 checks passed, VIES valid,
  claim completed where VEN.24 applies). Revoking the flag works the same way. The 0% rule and
  the operator shown as the seller apply only from that confirmation; until then the vendor stays
  on its plan's commission and sells under its own identity. The VAT number is compared in clear; `tax_code` is encrypted at rest (SEC.3), so the
  TIN match decrypts it in the service for the one vendor being checked and never adds a
  searchable copy. No uniqueness rule refuses a vendor whose identity equals the operator's or
  another vendor's.
- **Commission.** After confirmation, no commission: the confirmation writes an explicit 0%
  commission rule with the reason "operator-owned" (VEN.2 forbids only an implicit zero), under
  the same step-up as any rate change (MQ-2), and no commission statement (CMP.2), since the
  platform does not charge itself.
- **Ledger and payouts** stay separate for accounting: the shop has its own ledger, balance and
  payout account like any vendor.
- **Access.** The people who run it use their separate shop accounts (vendor members); a
  platform-role holder enters its shop space only through such an account or a support
  impersonation session (CORE.10), as for any vendor.
- **Storefront.** The seller shown on offers and in the order documents is the operator, with
  the operator's legal notices (DSA trader data, policies).
- **DAC7 and invoicing (D-22, decided by the owner 2026-11-09):** the operator's own sales are
  not reported in DAC7 (CMP.1 leaves operator-owned vendors out of the reportable sellers), and
  the operator invoices them as the seller, outside the platform, like any vendor.

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

### Holiday and closure schedule (VEN.20)

A vendor's holidays and shop closures are a **vendor setting**, not an event (owner decision
Q-73; the events module of [15](15-events.md) has no closure type). The buyer can still order
during a closure; a neutral notice and the checkout say when the package will leave.

```python
# apps/vendors/models.py
class VendorClosure(Model):                     # vendor-scoped (CORE.3)
    vendor = FK(Vendor)
    start_date, end_date: date                  # inclusive; end_date ≥ start_date
    repeats_yearly: bool                        # same dates every year; may cross 31 December
    notice_days = PositiveSmallInt(default=14)  # 0–60: how early the notice appears
```

- **No free text, no moderation.** The notice is a fixed interface string with the dates only,
  translated into all five languages (I18N.7), so nothing the vendor writes reaches visitors. A
  change takes effect at once.
- Validation: a yearly range lasts less than one year and never starts or ends on 29 February.
  Ranges may overlap; the calculation merges them. A range that has ended stays as history and
  cannot be edited.
- Dates are calendar dates in the platform's local time zone (spec 01); "today" is the current
  date in that zone.
- **Services** (`apps/vendors/services.py`): `add_closure`, `update_closure` and `delete_closure`
  accept `dry_run=True`, return a `ChangeSet` and write an `AuditLog` entry (actor, before and
  after, source: vendor area, admin or API).
- **Vendor area** (CORE.9, `/manager/shop/settings/closures/`): list of current and upcoming ranges,
  add, edit and delete forms, a preview of the notice. Staff use a Wagtail admin ViewSet. **API**:
  `/api/v1/vendors/{id}/closures` (list, create, update, delete, `?dry_run=true`, RFC 9457 errors);
  a vendor-scoped key (CORE.13) reaches only its own vendor, anything else is a 404.

**Dispatch date through a shipping-side interface.** `shipping` may import `vendors` (spec 01
§1.3), never the other way round, and the shipping engine stays pure:

```python
# apps/shipping/engine.py (pure, no ORM, knows nothing about vendors or orders)
@dataclass(frozen=True)
class ClosureRule:
    start: date; end: date; repeats_yearly: bool
@dataclass(frozen=True)
class ClosedPeriod:
    start: date; end: date                      # inclusive

def expand_closures(rules: Sequence[ClosureRule], window_start: date, window_end: date) -> tuple[ClosedPeriod, ...]
def earliest_dispatch_date(ordered_on: date, closed: Sequence[ClosedPeriod]) -> date:
    """Merge overlapping and adjacent periods (next.start <= current.end + 1 day); if ordered_on
    falls inside a merged period, return its end + 1 day, else ordered_on."""
def delivery_window(dispatch_on: date, min_days: int, max_days: int) -> tuple[date, date]

# apps/shipping/services.py
def vendor_closed_periods(vendor_ids, start: date, end: date) -> dict[int, tuple[ClosedPeriod, ...]]
def closure_notice(vendor_id, today: date) -> ClosureNotice | None   # merged period and dispatch date
```

- `vendor_closed_periods` reads `VendorClosure` rows and maps them to `ClosureRule`; the engine
  only receives dataclasses.
- **Notice.** From `notice_days` before a closure until its end, the vendor storefront (VEN.10),
  each product page of the vendor (CAT.7, next to the add-to-cart button) and the cart (per vendor
  package) show: "This shop is closed from 1 to 31 August 2027. Orders placed now will be
  dispatched from 1 September 2027." Overlapping or adjacent ranges show as one merged period. The
  templates use the `shipping` template tag library (`{% load dispatch %}`) inside theme
  templates, which adds no Python import to `vendors` or `catalog`. The notice is a `role="region"`
  landmark with a heading (not an alert: it does not interrupt) and does not rely on colour.
- **Checkout.** `cart.build_checkout` (ORD.3) asks `vendor_closed_periods` for the vendors in the
  cart (today to 180 days ahead) and passes each package's periods to `earliest_dispatch_date`.
  The delivery estimate counts from the dispatch date (the method's indicative delivery time of
  SHP.12 and any extra days of a special area of SHP.14 on top). Ordering stays possible, with no
  extra confirmation step.
- The order snapshot (ORD.2) stores the expected dispatch date per `VendorOrder`, so a later
  change of the schedule does not rewrite what the buyer was told; vendor and customer order
  emails (ORD.9) show it.
- The anonymous page cache (SEO.6) is invalidated for the vendor's storefront and product pages
  when the schedule changes, and a page with a notice is never cached past the notice's start or
  end.

### Vendor dashboard (bare minimum)

Own orders and order lines, entry of shipping quotes, products (editing of texts, price and stock
if the vendor is not fed by a pipeline), balance and payouts, documents (accepted contracts and their
evidence from VEN.15, non-fiscal commission statements from CMP.2, obligations checklist from CMP.10, plan fee statements from VEN.19),
tax regime change request (approved by staff, as today), store policies per language, holiday
and closure schedule (VEN.20).

## 4.3 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| VEN.1 | Vendor models, translations and policies, statuses, slug | P1 | 8 | ARC.6 | Admin CRUD + status tests |
| VEN.2 | VendorPlan and versioned CommissionRule with constraints (no implicit zero rule); the per-vendor plan assignment with its history is not part of it (VEN.19 owns it): until then callers of `commission_rule_for` pass the plan in force at order time; rule and plan changes need a passkey, once or an open window (CORE.25 action `commission_change`, sensitive) and a role from the CORE.18 matrix, checked by role membership and never `has_perm`: commission rules the Configuration and tax role (section `platform.config`, spec 02), plans the platform owner (section `platform.vendors`); the other roles read | P1 | 6 | VEN.1 | Tests on overlapping validity periods; with no passkey window open a rule or plan change is refused and with one open it is allowed; every other role is refused, and a technical superuser without a role too |
| VEN.3 | `compute_commission` pure function + real-case suite (REGOLA-ZERO, PERC-CONGELATA, FEE-NETTO) | P1 | 8 | VEN.2 | All 4.4 cases green |
| VEN.4 | Append-only LedgerEntry (DB constraint/trigger), balance, per-vendor report | P1 | 10 | VEN.3 | UPDATE/DELETE impossible; balance = sum |
| VEN.5 | Application → onboarding → active flow, with versioned contract and DAC7 checklist | P1 | 12 | VEN.1 | Test vendor activated end-to-end |
| VEN.6 | Stripe Express onboarding (account link, status, `account.updated` webhook); a vendor without an active Stripe Connect account sells on request (owner decision D-19, 2026-11-11): `sales_mode=on_request`, catalog visible, no cart, until the account is active | P1 | 8 | PAY.1 | Account status synchronized; a vendor without an active account has `on_request` products that show in the catalog with no add-to-cart and no price in JSON-LD; when the account becomes active the vendor can switch to sale |
| VEN.7 | Payouts: eligibility, block if DAC7 incomplete (`assert_payout_allowed`), history | P2 | 8 | VEN.4, PAY.3, VEN.12 | Tests on every blocking condition |
| VEN.8 | Nightly ledger ↔ Stripe reconciliation with anomaly report | P1 | 10 | VEN.4, PAY.3 | Simulated discrepancy → alert |
| VEN.9 | Vendor dashboard: balance, transactions, payouts, documents | P1 | 10 | CORE.9, VEN.4 | Vendor sees only their own transactions |
| VEN.10 | Public vendor storefront per language (bio, policies, products, store categories) | P1 | 8 | CAT.8 | Category sidebar correct in IT too (WCFM bug solved by design) |
| VEN.11 | Tax regime change request by vendor with staff review and email | P2 | 6 | TAX.2, ARC.16 | Parity with VTO 2.4 |
| VEN.12 | Vendor legal and e-invoicing identity (EU + IT): legal form, structured address, VAT with VIES status, codice fiscale, fiscal regime RF01-RF19, REA, SDI recipient code, PEC, EORI, fiscal representative, permanent establishment, invoicing bank details; required fields per country, region (EU or non-EU) and legal form, including the DAC7 dataset for every vendor, defined in a staff-editable table, enforced when the vendor requests a payout | P1 | 8 | VEN.1 | A vendor's data fills a valid FatturaPA party block; a vendor missing a required field for its country cannot request a payout, but can open and edit its profile |
| VEN.13 | Contract templates with merge fields, vendor-specific clauses with four-eyes approval, custom uploaded contracts: per-vendor `VendorContractDocument` rendered to PDF per language through a `DocumentRenderer` interface (WeasyPrint), prevailing language, legal representative field on the VEN.12 identity; clausole vessatorie marked on templates and vendor clauses (number, title, text), two signature blocks with the art. 1341 and 1342 c.c. formula, the fixed clause on how to complete the specific approval, page footers with the short hash, one-page specific approval sheet | P1 | 18 | VEN.5, VEN.2, VEN.12, ARC.15 | A test vendor's IT and EN PDFs carry its legal identity, commission rule and approved clauses; a missing merge field blocks issuing; a clause or custom upload approved by its own author is refused; a new template version or clause issues a new document for each vendor concerned; a document with marked clauses renders two signature blocks whose formula lists exactly their numbers, and an approval sheet with their full texts and the main PDF hash |
| VEN.14 | Vendor contract e-signature in two separate acts: acceptance of the whole contract (checkbox, email OTP, MFA step-up), then the specific approval of the marked clauses on a separate screen with a second, separate email OTP; email OTP simple electronic signature, PDF of the exact version per language with SHA-256 hash and RFC 3161 timestamp per act behind a `TimestampAuthority` interface, evidence per act (user, member role, time, trusted-proxy IP, user agent, OTP id), evidence package by email, completion route choice, `SignatureProvider` interface | P1 | 22 | VEN.13, CORE.4, CORE.5, ARC.9, ARC.16 | A test vendor signs end to end; the stored PDF matches its hash and both timestamp tokens verify against their evidence manifests (fake TSA in tests, a real TSA on STAGE); the evidence email arrives in the signer's language; without a text in its language the vendor can accept only after acknowledging English; a wrong, expired or reused code records no acceptance; the specific approval needs its own screen and its own code, the acceptance code is refused for it, and no single request records both acts; the OTP fields accept paste and carry `autocomplete="one-time-code"` (WCAG 2.2 SC 3.3.8, spec 14 14.6) |
| VEN.15 | Vendor document area: download accepted contracts and evidence (PDF, approval sheet, evidence package, timestamp tokens, uploaded signed file, paper original scan) through signed URLs, kept per the retention rules | P1 | 6 | VEN.14, CORE.9 | A vendor downloads all and only its own accepted documents; another vendor's file gives 404; files stay downloadable for staff after the vendor is closed |
| VEN.16 | Contract updates and specific approval completion: re-acceptance deadlines per vendor, reminders, automatic consequences, specific approval pending requirement with its own deadline and consequence, paper original route with staff moderation (receipt date, scan, archive location, approve or reject with reason), completion routes per version and per vendor, staff pending view with route and paper status; re-acceptance emails link through CORE.15 secure action links | P1 | 20 | VEN.14, ARC.9, SEC.4, ARC.16, CORE.15 | A vendor that misses its deadline gets the configured consequence (warn, payouts blocked, sales blocked or suspended); staff can extend the deadline, change the consequence or exclude the vendor, all audited with a reason; accepting the document lifts the automatic restrictions but not a staff suspension; a vendor with both acts online but no completion gets the specific approval consequence at its deadline, and a staff-approved paper original lifts it; a rejected original stays pending and the vendor is emailed the reason |
| VEN.17 | Qualified and advanced digital signature upload and verification (PAdES/CAdES) of the whole document or of the specific approval sheet, pyHanko, EU trusted lists for qualified and staff-listed trust anchors for advanced; completes the specific approval and is stored as a stronger acceptance | P1 | 18 | VEN.14, VEN.16 | A PAdES and a `.p7m` test file signed with a certificate from a test trusted list are accepted; a file that signs a different PDF, an altered file or an untrusted certificate is refused with the reason; a signed approval sheet satisfies the vendor's specific approval requirement |
| VEN.18 | Fiscal regime RF20 (cross-border VAT franchise, Directive (EU) 2020/285, FatturaPA specification 1.9 from 2025-04-01): add the choice with its official SDI label, update the check constraint with a data migration, and record the FatturaPA specification version (1.9.1) the party block follows; the TAX.2 regime-change request constraint accepts RF20 too, and the tax profile consistency check gets an RF20 rule (⚖️ tax advisor: VAT regime and exemption reason for the cross-border franchise); who may choose RF20 (EU establishment, "EX" number) is VEN.25 (owner decision MQ-5) | P1 | 3 | VEN.12, TAX.2 | A vendor can save RF20; the vendor and tax request constraints accept RF01–RF20 except RF03; a regime-change request to RF20 is accepted; an RF20 profile with an inconsistent VAT regime is reported; `fatturapa_party()` emits `RegimeFiscale` RF20; a test pins the label list to the specification version |
| VEN.19 | Plan entitlements and plan fees: `plan_allows(vendor, feature)` service over `VendorPlan.features`, plan change with effective date and audit; monthly `plan_fee` ledger debit by a scheduled idempotent job; non-fiscal statement PDF per charge in the vendor's language (no invoice issued by the marketplace; the operator invoices outside the platform) | P2 | 8 | VEN.2, VEN.4, VEN.15, ARC.9 | A vendor whose plan lacks a feature cannot use it and keeps its data; a plan with a monthly fee gets exactly one `plan_fee` entry per period even when the job runs twice; the statement PDF states that it is not a tax invoice and matches the ledger entry; no fiscal document or e-invoice is produced |
| VEN.21 | Reopen a closed vendor (owner decision Q-17): `closed → onboarding` in `TRANSITIONS` (`apps/vendors/engine.py`), a staff-only service with a mandatory reason in the AuditLog and dry-run, admin action; the onboarding checklist and activation gates run again | P1 | 2 | VEN.5 | A closed vendor reopened by staff is `onboarding` and needs the contract in force accepted again before activation; a reopen without a reason is refused; vendor members cannot reopen; `closed → active` stays refused |
| VEN.22 | Public "Sell with us" application (owner decision Q-55): account creation and vendor application in one form (no password on the form: the person who opens the emailed link chooses it), Turnstile without a puzzle (WCAG 3.3.8, CMP.9), confirmation email through ARC.16 plus one reminder at 48 h, each with a "this was not me" cancel link, unconfirmed applications deleted after 3 days (owner decision D-24), MFA enrolment at first login (CORE.4); the application lands as `applicant` for staff review (VEN.5); the CMS.5 recruitment page links to it | P1 | 8 | VEN.5, ARC.16, CORE.4 | A visitor applies from the public page, confirms the email, enrols MFA at first login and appears as an applicant in the admin; a bot submission without a valid Turnstile token is refused; the form passes the CMP.9 checks; a GET of the confirmation or cancel link changes nothing and leaves the token valid, a POST acts once and a second POST is idempotent; an application nobody confirms gets one reminder at 48 h and is deleted after 3 days |
| VEN.20 | Holiday and closure schedule (owner decision Q-73): `VendorClosure` in the vendor settings (date ranges, optional yearly recurrence, notice lead time, no free text, no moderation), audited services with dry-run, vendor-area and admin forms, API `/vendors/{id}/closures`; `ClosureRule`, `ClosedPeriod`, `expand_closures`, `earliest_dispatch_date` and `delivery_window` pure functions in `shipping/engine.py`, `vendor_closed_periods` and `closure_notice` shipping services; neutral notice on the storefront, product pages and cart through a `shipping` template tag; `build_checkout` shifts the dispatch date and the estimate; expected dispatch date in the order snapshot; page cache invalidation | P1 | 12 | CORE.9, CORE.13, VEN.10, CAT.7, SHP.3, ORD.2, ORD.3 | Regression cases 8–12 of §4.4 green; a checkout with a closed and an open vendor shows the notice only on the closed vendor's package and still places the order; import-linter stays green |
| VEN.23 | DAC7 seller dataset (owner decision MQ-6): the Directive (EU) 2021/514 Annex V section III.B fields on the vendor for individuals and entities (TIN with its issuing country, VAT number only when the vendor has one with its VIES status, date of birth for individuals, place of birth when the issuing country issues no TIN, business registration number), a "same as the VAT number" option, TIN format rules per issuing country in a table, the seeded `LegalFieldRequirement` rows and `engine.dac7_missing()` updated; staff correct or complete the dataset behind an MFA step-up with audit | P1 | 6 | VEN.12, TAX.5, CORE.4 | A test pins the field list to Annex V III.B; a TIN without its issuing country is incomplete for a payout; "same as the VAT number" fills the TIN from a VIES-valid VAT number; a malformed TIN for its country is refused; a vendor whose country issues no TIN is complete with a place of birth; a staff correction without step-up is refused and a saved one is in the AuditLog; an Italian company's REA number is accepted as its business registration number (owner decision MQ-16); the no-TIN country list becomes staff-editable data in VEN.27 |
| VEN.24 | Claim an imported vendor (owner decision MQ-1): a single-use, expiring CORE.15 link sent only to the vendor's legal email of record or its registered PEC (certified email, Italian vendors), chosen by staff; the claimant sets a password and their own MFA and confirms the stored VAT number or TIN; staff approve the claim before fiscal or payout data can change; a staff "resend / regenerate" action invalidates the old link, with audit | P1 | 6 | CORE.15, VEN.5, CORE.4 | The link goes only to the legal email of record or the registered PEC on file, and the AuditLog names the channel; a GET does not consume it; a wrong VAT number or TIN is refused and audited; before staff approval the claimant cannot change the legal identity, the tax profile or the payout data; after regenerate the old link is refused and both the regeneration and the invalidation are in the AuditLog; an expired link is refused |
| VEN.25 | RF20 eligibility (owner decision MQ-5): fiscal regime RF20 only for vendors established in an EU member state, with a mandatory participation number issued under the cross-border SME franchise (suffix `EX`, art. 284(3) of the VAT directive as amended by Directive (EU) 2020/285), a format check per issuing member state and the issuing country matching the establishment country; the number in the FatturaPA party data and the TAX.2 regime-change check. Owner decision MQ-17 (2026-11-09): the establishment is the registered office (seat), and a permanent establishment alone does not qualify; every RF20 tax profile stays flagged for manual review (`profile_review_notes`); the `EX` number format per member state stays open until the accountant confirms it | P1 | 3 | VEN.18, CMP.13 | RF20 is refused for a vendor established outside the EU and without an `EX` number; RF20 is refused for a vendor whose seat is outside the EU even with a permanent establishment in a member state; a malformed number or one issued by another member state is refused; a valid EU vendor saves RF20; a regime-change request to RF20 without the number is refused; every RF20 profile appears in the review list |
| VEN.26 | Operator-owned shop (owner note 2026-11-09, spec 04 §Operator-owned shop): a VAT number, or TIN with country, matching the current `PlatformOperator` only proposes the shop (`operator_owned_proposed`, re-checked on identity save and on operator change; TIN compared by decrypting in the service, no searchable copy); the platform owner confirms or revokes `is_operator_owned` in the platform space with MFA step-up, reason and AuditLog, only for a vendor with verified identity (VEN.12, VEN.24); after confirmation an explicit 0% commission rule with reason (MQ-2 step-up) and no CMP.2 commission statement; own ledger and payouts; the operator shown as the seller with its legal notices; operator-owned vendors left out of the DAC7 reportable sellers (CMP.1) and their sales invoiced by the operator outside the platform (D-22, decided) | P1 | 6 | VEN.12, VEN.2, VEN.24, ARC.7, CORE.18 | An applicant with the operator's VAT number is proposed, not flagged, and its commission stays on the plan until the owner confirms; a confirmation without step-up or reason is refused, and one for a vendor with unverified identity is refused; a confirmed vendor computes 0 commission from the confirmation on, with the flag change in the AuditLog; a non-owner platform role cannot confirm (404); two vendors with the same name and tax code coexist; changing the operator's VAT number updates the proposal on the next check and never sets the flag by itself; a confirmed operator-owned vendor's sales are not in the DAC7 export |
| VEN.27 | No-TIN jurisdictions as staff-editable data (owner decision MQ-16): the list of jurisdictions that issue no TIN, today the `engine.NO_TIN_COUNTRIES` constant, moves to a table seeded with the current list; staff add or remove a country through an audited service with dry-run (AuditLog entry with the reason), behind a passkey, once or an open window (CORE.25), with effective dating (`valid_from` / `valid_to`, a removal ends the row, no overlap per country, `engine.no_tin_countries_on` answers any day) so past DAC7 periods and payout decisions are reproducible, and DAC7 report generation snapshots the list and completeness at generation time; `dac7_check` reads the table, keeping the engine pure (the service passes the list in); the accountant's confirmation of the initial list and of the TIN formats stays an open item | P1 | 3 | VEN.23 | A data migration seeds the six current countries and the DAC7 check gives the same results as before; a country staff add makes an individual with a place of birth and no TIN complete, and removing it makes the vendor incomplete again; each change is in the AuditLog with its reason, and a dry run changes nothing; vendor members cannot edit the list; the engine has no ORM import; with no passkey window open an add or end is refused and with one open it is allowed; a removal keeps the row and past days still answer yes; overlapping windows of a country are refused |
| VEN.28 | Read-only document access for closed vendors (proposed, lane-a decision on #170, not built): the CORE.17 shop space refuses members of a closed vendor with a 403 page; for the legal retention period they may still need to download their own past payout statements, invoices and DAC7 data. A dedicated read-only area for the closed vendor's former members, downloads only through signed URLs, no other shop page, every download in the AuditLog, access ending with the retention period | P2 | 4 | CORE.17, VEN.9, VEN.15, VEN.23 | A member of a closed vendor downloads that vendor's past payout statements, invoices and DAC7 data and opens no other shop page; another vendor's documents answer 404; after the retention period the area answers 403 with the closed-vendor page; each download writes an AuditLog entry |
| VEN.29 | Storefront rich text (owner decision D-34, follow-up of VEN.10 and CORE.9): the description and the three policies are Wagtail `RichTextField`s with the default editor features, edited with Wagtail's Draftail widget in the Manager and the normal field in the admin, saved through the CMS.12 hook (form, admin, API, import), rendered with `\|richtext` after the allow-list runs again; the tagline stays plain text; length caps count the stored HTML; a data migration turns plain text into `<p>` paragraphs (idempotent, translation fingerprints kept); images and documents must be in the vendor's own collection tree (`engine.vendor_collection_name`), checked on save and on render, and the Wagtail choosers list only that tree for vendor members. Open follow-up: vendor members have no Wagtail admin access, so the Manager editor leaves out the image, embed and document-link features until the Manager hosts its own choosers and upload | P1 | 3 | VEN.10, CORE.9, CMS.12 | Bold, list and link render as HTML on the storefront; `<script>`, `onclick`, `<iframe>` and `javascript:` links are stripped on save and on render; crafted HTML embedding another vendor's image or document is refused and a text whose media left the collection is hidden; the vendor's own media works; a legacy plain-text policy renders as paragraphs; the migration is idempotent; an e2e screenshot of a formatted policy |
| VEN.31 | Manager image and document chooser with upload into the vendor's own collection (owner decision D-34, follow-up of VEN.29): in the CORE.17 shop space the rich-text and image fields get a chooser limited to the vendor's own Wagtail collection (images and documents of other vendors and of the platform never listed or addressable), with upload in place into that collection; uploads go through the SEC.2 upload pipeline (type, size and pixel caps, scan, EXIF stripped) and the CMS.8 image licence fields; the Manager chooser is scoped by `current_vendor`, not by role alone; the page-scoped CSP hash for the editor styles replaces the local `editor.css` allowance; owner decision (2026-11-11): no public-by-URL vendor PDFs, so documents can be chosen and uploaded into the vendor's own collection but cannot be linked from a storefront text (the editor has no document link and a save refuses one) until ARC.15 serves them privately with signed, expiring URLs | P1 | 8 | CORE.17, CMS.2, SEC.2, ARC.15 | A vendor member picks and uploads an image into its own collection and sees no other collection; a crafted chooser or upload request naming another vendor's collection answers 404 and is audited; an oversized or disguised file is refused; the stored file is in the vendor's collection; no new inline style or script is needed on the Manager page |
| VEN.32 | Versioned vendor documents (owner requirement of 2026-11-11, extends the VEN.31 decision B: no public-by-URL vendor PDFs, so the VEN.31 document link waits for this row): vendor documents (terms, return policy, data sheets) live in the vendor's own private area of the ARC.15 private document store (local backend first, object storage later, [ADR-0009](../adr/0009-private-vendor-documents.md)), never on a public URL and never in another vendor's area (CORE.3). A `VendorDocumentVersion` is immutable and identified by the SHA-256 of its content: a new upload of the same document is a new version, never an overwrite, and the same bytes uploaded twice resolve to the existing version; the current version of a document is a pointer that changes, the versions stay. Uploads go through the SEC.2 pipeline (VEN.31) and are scanned by the SEC.15 antivirus service before acceptance (fail closed); the scan result and signature version are stored on the version. The vendor marks a document customer-visible and the platform can revoke that mark (audited). Dedup is per vendor (the same bytes in two vendors are two blobs). The signed link format, binding, TTL, optional single use and the `can_read_document` rule are those of ARC.15 and ADR-0009; a customer reads a version only if its hash is in the snapshot of one of their own orders and the vendor marked the document customer-visible. Every read is by a signed, expiring URL issued per read and logged in the `AuditLog` with the document, the version hash and the reader (staff and vendor members by user, customers pseudonymised, never the plain address or name). The order snapshot stores the hash (and version id) of the terms and return-policy document in force at checkout (ORD.2 snapshot rules), so what the customer relied on stays provable. Retention of a version follows the longest legal retention of any order that references it (CMP.29): a referenced version is never deleted or overwritten, an unreferenced one can be removed by the vendor. Once it lands, the VEN.31 document link (storefront rich text) points at the signed-URL view | P1 | 14 | VEN.31, ARC.15, SEC.2, SEC.15, OPS.13, CORE.3, ORD.2, CMP.29 | A second upload of a changed file creates version 2 and leaves version 1 readable with its hash; the same bytes twice do not create a new version; a stored version cannot be updated or deleted while an order references it (database trigger or constraint test); no public or unsigned URL serves the file, an expired or tampered signature answers 403 or 404 and a crafted request naming another vendor's document answers 404 and is audited; each read writes one `AuditLog` entry with the version hash and a customer reader pseudonymised (HMAC pseudonym, no plain name, address or email in the entry), and a denied or expired attempt is audited with its result; a customer can read a version only through a hash in their own order snapshot, never a newer one they did not rely on; the same bytes uploaded by two vendors are two separate blobs; the EICAR string uploaded as a document is refused and a clean upload stores the scan result and signature version on the version row; with the scanner down the upload is refused; a platform revoke of the customer-visible mark makes a customer read answer 404 and is audited; links for contract and certificate documents are single-use; a seed order stores the hash of the terms version in force and still shows that version after a newer one is uploaded; the retention job keeps a referenced version and may drop an unreferenced one; the VEN.31 document link renders a working signed link for the vendor's own document and is still refused for a foreign one |
| VEN.33 | Plan fee lines and the refund-fee clause (owner decision D-05, 2026-11-11, extends VEN.2): the fee lines of a vendor plan are the percentage commission, the **collection cost**, the **order-handling fee** and the **refund fee**; the versioned CommissionRule carries, per plan, the collection-cost and order-handling parameters and the refund-fee parameters (surcharge, default 2 EUR, charged on every refund event, and the rounding up to the whole euro); the rule is snapshotted on the order like the commission rule, so a later change never touches an existing order; `compute_commission` stays pure and gains the fee lines and a pure `refund_fee(provider_cost, rule)` that returns the cost plus the surcharge rounded up to the whole euro (`Decimal`, `ROUND_HALF_UP` on every other line; the ceil is the one explicit exception); changes need the same passkey (once or an open window) and role as any rule change (VEN.2). A **vendor-terms clause** (the versioned vendor contract of VEN.5, in the CMP.5 terms) states that a refund or chargeback gives back the commission and the collection cost on the refunded share and charges the refund fee to the vendor on every refund, withdrawal included (owner decision D-05 follow-up, 2026-11-11: no split by cause; the vendor may **not** pass it on to consumers in its return terms (owner's legal review 2026-11-13: Codice del consumo artt. 56-57 forbid charging the consumer refund handling costs), so the clause has no pass-on sentence), except a refund caused by a platform malfunction or error (L-10, adviser opinion 2026-11-11); that the refund fee = Stripe's actual cost of the refund + the **exact** fixed part (EUR 2.00 at signature, never "about"), rounded up to the whole euro; and that a change of the fixed part is notified on a durable medium at least 15 days before and applies only to orders created from its effective date. The surcharge is a versioned plan parameter with an effective date (a new version, never an in-place edit); accepting the version in force is required before a vendor can sell, and the accepted version is stored with the vendor | P1 | 8 | VEN.2, VEN.3, VEN.5, CMP.5 | Pure tests: cost 3.15 + surcharge 2.00 = 5.15 gives 6.00 (owner example), cost 1.37 + surcharge 2.00 gives 4.00, an exact 3.00 stays 3.00, a second partial refund with cost 1.20 + 2.00 = 3.20 gives 4.00, a plan with surcharge 0 and cost 0.00 gives 0.00, a plan override of the surcharge changes the result; the rule snapshot on an order still has the old surcharge after the plan changes; a rule or plan change with no passkey window open or by a wrong role is refused (VEN.2 constraint); the vendor terms version carries the refund-fee clause and the CMP.5 terms link it; a vendor that has not accepted the version in force cannot activate; the engine has no ORM import; a surcharge version whose effective date is less than 15 days after its notice date is refused; an order created before the effective date keeps the old surcharge in its snapshot; the clause text states EUR 2.00 exactly, names the platform-error exception and has no sentence letting the vendor pass the fee on to consumers |
| VEN.34 | VAT on the commission and the plan fees when `PlatformOperator.commission_vat_mode` is not `none` (coherence of PAY.3, 2026-11-13): settlement reads the mode; until this task is built, PAY.3 refuses to settle an order under any mode other than `none` (blocked and audited, no ledger line, no transfer); then the VAT lines on the commission, collection cost, order-handling and refund fees, their ledger rows and the commission statement (CMP.2) follow the mode; ⚖️ (T) the rates and the reverse-charge wording are confirmed by the tax advisor before a mode other than `none` is used | P2 | 8 | PAY.3, VEN.33, CMP.2 | With mode `none` nothing changes; with `standard` a settlement adds VAT on each fee line at the operator's rate and the ledger nets to zero; before this task, mode `standard` blocks settlement with an audit entry |

## 4.4 Mandatory regression cases

1. Example: 20% commission on €100 net goods, €122 gross: commission 20.00; payment fee 122 × 4% + 2 = 6.88; vendor net 95.12.
2. Case FEE-NETTO: the payment fee is computed on the gross (9.3424, rounded), not on the already-reduced net.
3. No VAT on the commission and no VAT on the payment fee (platform's current tax regime).
4. Missing rule → blocking error, never a silent 0 commission (case REGOLA-ZERO).
5. The percentage frozen in the order is the one valid on the order date (case PERC-CONGELATA).
6. Payment without split is not possible: a single collection flow (case NO-SPLIT).
7. A change of the platform's regime (e.g. VAT on commissions after a relocation) is
   a `PlatformOperator` configuration, not a code change.
8. **Closure overlap moves the dispatch date** (VEN.20): closures 1–15 August and 10–31 August
   2027, order on 5 August → dispatch on 1 September; adjacent closures 1–15 and 16–31 August →
   1 September; order on 31 July → dispatch on 31 July; order on 1 September → 1 September.
9. A yearly closure 24 December–6 January: order on 2 January 2028 → dispatch on 7 January 2028;
   order on 20 December 2027 → 20 December; a yearly range starting or ending on 29 February is
   refused.
10. A closure starting in 10 days with `notice_days` 14 shows the notice with the dispatch date on
    the storefront, the product pages and the cart in all five languages; one starting in 20 days
    shows nothing yet. Saving it needs no review, takes effect at once and writes one `AuditLog`
    entry; `dry_run` writes nothing.
11. The order snapshot keeps the expected dispatch date shown at checkout after the vendor edits
    or deletes the closure.
12. The shipping engine tests use no `vendors` code, `apps.vendors` never imports
    `apps.shipping` (import-linter), and a vendor's API key cannot read or change another
    vendor's closures (404).
