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
   (DAC7 data, Stripe Express account via onboarding link, acceptance of the versioned contract,
   tax regime) → `active`. Every step is recorded in `AuditLog`.
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
| Invoicing bank details | `iban`, `bic`, `bank_name` | IBAN mod-97 and length per country, BIC format. The IBAN is encrypted at rest by SEC.3 and masked in the audit log. Payout accounts are VEN.6 |

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
the list of missing or invalid fields. A vendor that passes it has the complete DAC7 dataset
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

### Vendor dashboard (bare minimum)

Own orders and order lines, entry of shipping quotes, products (editing of texts, price and stock
if the vendor is not fed by a pipeline), balance and payouts, documents (contract, commission invoices),
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
| VEN.7 | Payouts: eligibility, block if DAC7 incomplete, history | P2 | 8 | VEN.4, PAY.3 | Tests on every blocking condition |
| VEN.8 | Nightly ledger ↔ Stripe reconciliation with anomaly report | P1 | 10 | VEN.4, PAY.3 | Simulated discrepancy → alert |
| VEN.9 | Vendor dashboard: balance, transactions, payouts, documents | P1 | 10 | CORE.9, VEN.4 | Vendor sees only their own transactions |
| VEN.10 | Public vendor storefront per language (bio, policies, products, store categories) | P1 | 8 | CAT.8 | Category sidebar correct in IT too (WCFM bug solved by design) |
| VEN.11 | Tax regime change request by vendor with staff review and email | P2 | 6 | TAX.2 | Parity with VTO 2.4 |
| VEN.12 | Vendor legal and e-invoicing identity (EU + IT): legal form, structured address, VAT with VIES status, codice fiscale, fiscal regime RF01-RF19, REA, SDI recipient code, PEC, EORI, fiscal representative, permanent establishment, invoicing bank details; required fields per country, region (EU or non-EU) and legal form, including the DAC7 dataset for every vendor, defined in a staff-editable table, enforced when the vendor requests a payout | P1 | 8 | VEN.1 | A vendor's data fills a valid FatturaPA party block; a vendor missing a required field for its country cannot request a payout, but can open and edit its profile |

## 4.4 Mandatory regression cases

1. Example: 20% commission on €100 net goods, €122 gross: commission 20.00; payment fee 122 × 4% + 2 = 6.88; vendor net 95.12.
2. Case FEE-NETTO: the payment fee is computed on the gross (9.3424, rounded), not on the already-reduced net.
3. No VAT on the commission and no VAT on the payment fee (platform's current tax regime).
4. Missing rule → blocking error, never a silent 0 commission (case REGOLA-ZERO).
5. The percentage frozen in the order is the one valid on the order date (case PERC-CONGELATA).
6. Payment without split is not possible: a single collection flow (case NO-SPLIT).
7. A change of the platform's regime (e.g. VAT on commissions after a relocation) is
   a `PlatformOperator` configuration, not a code change.
