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
    country; vat_number; tax_code; registered_address (structured JSON)
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

## 4.4 Mandatory regression cases

1. Example: 20% commission on €100 net goods, €122 gross: commission 20.00; payment fee 122 × 4% + 2 = 6.88; vendor net 95.12.
2. Case FEE-NETTO: the payment fee is computed on the gross (9.3424, rounded), not on the already-reduced net.
3. No VAT on the commission and no VAT on the payment fee (platform's current tax regime).
4. Missing rule → blocking error, never a silent 0 commission (case REGOLA-ZERO).
5. The percentage frozen in the order is the one valid on the order date (case PERC-CONGELATA).
6. Payment without split is not possible: a single collection flow (case NO-SPLIT).
7. A change of the platform's regime (e.g. VAT on commissions after a relocation) is
   a `PlatformOperator` configuration, not a code change.
