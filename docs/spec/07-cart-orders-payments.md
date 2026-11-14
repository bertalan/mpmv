# 07 — Cart, checkout, orders, payments and returns

## 7.1 How it works today

- **A single WooCommerce `shop_order`** per checkout, with one row per vendor in the
  `wcfm_marketplace_orders` table (commissions) and shipping lines with `vendor_id`. There are no real
  vendor sub-orders: the vendor view is reconstructed after the fact from the commissions table.
- Custom states: `quote-pending`, `quote-ready`, `quote-converted` (legacy).
- Settings: net prices, VAT based on the billing country, prices shown VAT-inclusive in the shop
  and VAT-exclusive in the cart; geolocation at checkout disabled.
- Gateways: `stripe_split` (destination charges) **and** `stripe_cc` without split, both active.
- Emails: WCFM "New Store Order" (suppressed in the quote states) + custom quote emails.
- **Refunds**: no procedure and no code; only per-vendor text policies.
- Protections: rate limiting on accounts and registrations, Turnstile, GET `?add-to-cart` requests rejected.

## 7.2 How it will work

### Models

```python
class Cart(Model):
    session_key / user; language; country_hint; created_at; updated_at
class CartLine(Model):
    cart; product; variant; quantity; added_price_net   # to warn if the price changes

class StockReservation(Model):                 # oversell prevention
    variant_or_product; cart; quantity; expires_at      # 15 minutes, renewed at checkout

class Order(Model):
    number (sequential with configurable prefix, e.g. "2027-000123"); public_id; customer (null = guest); email; language
    # the number is an order reference, never an invoice number: the vendor invoices (spec 00)
    billing_address; shipping_address (structured JSON + ISO country)
    customer_tax_status (b2c | b2b) ; vat_validation snapshot
    state = FSMField(...)                      # see diagram
    totals: goods_net, shipping_net, tax_total, grand_total (snapshot)
    payment_intent_ref; transfer_group
    legacy_wp_order_id

class VendorOrder(Model):                      # explicit vendor sub-order
    order; vendor; state = FSMField(...)
    goods_net; shipping_net; tax_total; gross_total
    shipping_choice (snapshot: carrier, zone, rate or quote)
    commission_snapshot (JSON: rule, percentage, fee)  # from compute_commission
    tax_profile_snapshot (JSON)
    tracking_number; carrier; shipped_at; delivered_at

class OrderLine(Model):                        # immutable snapshot
    vendor_order; product (null if deleted); sku; title; variant_label   # sku: display snapshot of the vendor label, never a reference
    unit_price_net; quantity; discount_net
    tax_rate; tax_reason; tax_amount; legal_note_key

class OrderEvent(Model):                       # readable timeline for customer, vendor, staff
    order; vendor_order (null); kind; payload; actor; created_at

class ReturnRequest(Model):                    # withdrawal / return
    order; vendor_order; lines (M2M + quantity); reason; kind ("withdrawal" | "warranty" | "other")
    state (requested → authorized → received → refunded | rejected); refund_amount
```

### Order privacy fields (owner decisions D-29, D-30)
- `Order.tax_retention_until`: 31 December of the order year plus 10, computed at order build in
  the business time zone (D-31). `Order.privacy_status`: `active`, `restricted_tax_only`,
  `anonymized`, changed only by the scrub service (see [11](11-compliance.md), "Order data
  retention and archive").
- The VIES log in the snapshot keeps the consultation time, the consultation number and the name
  returned by VIES.
- `Order.user` is a foreign key with `SET NULL`. The snapshot never joins live user or address
  tables: names, addresses and email are copied at order build.

### State machines

```mermaid
stateDiagram-v2
  direction LR
  [*] --> checkout
  checkout --> awaiting_payment
  checkout --> quote_flow: at least one package NeedsQuote
  quote_flow --> awaiting_payment: valid quote link
  awaiting_payment --> paid: webhook payment_intent.succeeded
  awaiting_payment --> cancelled: expiry / cancellation
  paid --> in_fulfillment
  in_fulfillment --> fulfilled: all VendorOrders shipped or delivered
  fulfilled --> closed: end of return window
  paid --> partially_refunded
  in_fulfillment --> partially_refunded: one vendor order refunded while others are fulfilled
  fulfilled --> partially_refunded
  partially_refunded --> refunded
```

`VendorOrder`: `pending` → `accepted` → `shipped` (tracking) → `delivered` → `completed`;
or `cancelled` / `refunded`. The `Order` state is derived from the states of its vendor sub-orders.

### Checkout (service)

```python
def build_checkout(cart, address, customer_tax_input) -> CheckoutPreview:
    """For each vendor: lines → resolve_goods_tax → quote_shipping → resolve_shipping_tax.
    Returns per-vendor and overall totals, plus any NeedsQuote/Blocked outcomes."""


def place_order(preview, payment_method) -> Order:
    """Transaction: verifies prices and stock, confirms the StockReservation, creates Order, VendorOrder and lines
    with snapshots; creates the PaymentIntent (or the quote request)."""


def on_payment_succeeded(event) -> None:  # idempotent per event.id
    """Order → paid; decrements stock; compute_commission per VendorOrder; ledger; transfer per vendor;
    customer and vendor emails."""
```

### Payments: abstraction and Stripe Connect

```python
class PaymentGateway(Protocol):
    def create_payment(self, order: Order) -> PaymentRef
    def transfer_to_vendor(self, vendor_order: VendorOrder, amount: Decimal) -> TransferRef
    def refund(self, order: Order, amount: Decimal, lines) -> RefundRef
    def reverse_transfer(self, transfer: TransferRef, amount: Decimal) -> ReversalRef
    def parse_webhook(self, request) -> GatewayEvent    # signature verification mandatory
```

- First implementation: **Stripe Connect, Express accounts, "separate charges and transfers"**.
  A single PaymentIntent on the platform with `transfer_group = order.number`, then one transfer per
  vendor for the `vendor_net` amount. This is the model suited to **multi-vendor** carts: destination
  charges allow only one destination account per payment.
- A single payment collection flow: no "non-split" gateway leaving orphaned vendor credits.
- Vendor not connected (currently Vendor E) → products not purchasable (`on_request`) until the account is active.
- Connected accounts are in EU countries at go-live, since only EU-established vendors are
  admitted (D-21, [11](11-compliance.md) CMP.13). Vendor countries outside the EU and cross-border
  payouts are CMP.22. UK consumer consignments of £135 or less create no charge at all (CMP.14).
- Customer funds do not sit in the platform's accounts (D-08, decided: adviser opinion 2026-11-11): no PSD2
  licence is needed while every customer payment goes through Stripe Connect. Each transfer to a
  connected account is created automatically and tied to the charge (`source_transaction`), so
  there is no free balance of third-party money; refunds use `reverse_transfer`. Customer money is
  never collected on the operator's bank account. The customer terms state that payment through
  the platform discharges the debt towards the vendor (Italian civil code art. 1188); the vendor
  terms carry the Stripe Connected Account Agreement and the set-off clause (art. 1252) (CMP.5).
- Launch scope (owner decision D-09, 2026-11-11): **Stripe Connect only**, split at source, so the
  platform fee and the vendor payout are separated by the provider. Other payment methods come
  later behind a documented hook, the `PaymentProvider` interface and the dev page "how to add a
  payment method" (PAY.5): a provider must support a split or a deferred collection of the platform
  fee, refunds, and payout reconciliation. For a method that cannot split at source (proposed
  follow-up PAY.6, spec only): the platform fee becomes a ledger receivable netted from the
  vendor's next Stripe payout, with a vendor SEPA debit mandate as the fallback when the balance
  does not cover it.
- Idempotent webhooks (`ProcessedEvent` table), verified signature, retries handled.

### Refunds, withdrawal and returns

- Full or partial refund by line; VAT reversed using the rates in the snapshot.
- Ledger: proportional `refund` and `commission_reversal`; `reverse_transfer` towards the vendor.
- **Fees on a refund (owner decision D-05, 2026-11-11).** The platform gives back everything it
  took on the refunded order (percentage commission, collection cost, order-handling fee) and
  charges the vendor a **refund fee** instead, a new ledger line type. Refund fee = the real
  provider cost of that refund or chargeback, read from the provider's balance transaction (never a
  fixed table), plus a surcharge (default 2 EUR), the total **rounded up to the whole euro** (the one ceil; every
  other line stays `ROUND_HALF_UP`); every refund event is charged cost plus surcharge. Examples:
  provider cost 3.15 EUR + 2 EUR = 5.15 EUR, charged 6 EUR; a second partial refund with cost
  1.20 EUR + 2 EUR = 3.20 EUR, charged 4 EUR. Surcharge and ceil are configurable per plan on the
  versioned CommissionRule (VEN.2, VEN.33). A partial refund gives the fees back pro rata and
  charges one refund fee per refund event; a chargeback uses the dispute cost as the provider
  cost. The refund fee is charged to the vendor on every refund, withdrawal included; there is
  no split by cause, and the vendor may **not** pass it on to consumers in its return terms (owner's legal review 2026-11-13: Codice del consumo artt. 56-57 forbid charging the consumer refund handling costs).
  The one exception (L-10, owner, 2026-11-11, adviser opinion 2026-11-11) is a refund caused by a platform
  malfunction or error: the refund carries an exclusion flag with a reason, audited, and no refund
  fee is charged. The contract states the exact fixed part (EUR 2.00 at signature); a change of it
  is notified on a durable medium at least 15 days before and applies only to orders created from
  its effective date (a versioned plan parameter, VEN.33). The refund fee is a platform service
  with the commission's VAT treatment and a line of its own on the periodic statement (T-8, CMP.2). The clause that lets the platform charge it sits in the vendor terms (CMP.5, VEN.33).
- **Online withdrawal function** in the customer area (button labelled "withdraw from contract
  here", in Italian "recedere dal contratto qui", then a confirmation labelled "confirm withdrawal",
  in Italian "conferma recesso", and an acknowledgement on a durable medium with the date and time):
  mandatory from 2026-06-19 (directive (EU) 2023/2673; Italy: article 54-bis of the Codice del
  Consumo, added by legislative decree 209/2025), see [11](11-compliance.md).
- Return: request per line → vendor authorization (or automatic within 14 days for withdrawal)
  → receipt → refund.

### Customer area

Orders with timeline and tracking, quotes to confirm, withdrawal and returns, addresses, data export and
deletion (anonymization, see GDPR), preferred language. Guest checkout allowed.

**Preferences and request history (ORD.11, P2, scope to be defined).** The owner wants one section
of the customer account where the customer sees all their product information and quote requests
and sets other preferences, including following vendors and event notifications (owner decision
Q-74; event notifications are EVT.16, [15](15-events.md)). The owner has still to review its
content, so it is not designed here: ORD.11 is a placeholder with a small estimate, re-estimated
and detailed in this section once the owner's answers are in.

### Free gifts (P2, ORD.12, owner decision MQ-9)

A price is never 0.00, neither on a product nor on a variant ([03](03-catalog.md), Q-31), so a gift
is not a zero-priced product. It is a promotion, triggered by a coupon code or by a cart condition
(for example a minimum amount or a given product in the cart). The gift enters the order as its
own line at full price plus a discount of 100% of that line (`discount_net`), so VAT and
commission are computed per line as for any other line. Who funds the discount follows the coupon rules of [18](18-promotions-coupons.md) §18.3: the
gift's issuer (the vendor or the platform) is its only funder, with no co-funding in v1, and a
platform-funded gift reduces the commission (Q-90). The split funding of bundles (CAT.16 to
CAT.18) stays a bundle rule.
The promotion tracks the gift's stock and a per-customer limit. VAT treatment (T-7, adviser opinion 2026-11-11):
a gift is allowed only as an in-kind discount on a paid order, with the gift's value at most the
value of the paid goods; never a standalone free supply.
Proposal D-39 ([18](18-promotions-coupons.md)) would build the gift as the `gift` kind of a general
coupon engine (PRM.1 to PRM.5), funded by whoever sets the promotion up (decided).

### Transactional emails (in the recipient's language)

The platform sends order confirmations and order summaries, never invoices or receipts. Each
order confirmation is per vendor, carries the vendor's identity, and is labelled "Documento non
fiscale / Non-fiscal document" in the recipient's language; the vendor issues the invoice or
receipt with its own tools ([00](00-vision-and-review.md)).

Customer: order received, payment confirmed, shipped (tracking), quote received/ready,
refund, withdrawal registered. Vendor: new order (only after payment), quote to be entered,
fulfillment reminder, return requested, payout. Staff: anomalies (payment without transfer, failed
webhooks, quotes about to expire).

### Proof of intra-EU dispatch (ORD.13, owner decision D-28, needs human review)

A reverse-charge intra-EU supply (spec [05](05-tax.md), nature code `N3.2`) is exempt only if the
goods really left for another member state. For every vendor order taxed that way the platform
collects at least **two non-contradictory proofs issued by independent parties** (Implementing
Regulation (EU) 282/2011 art. 45a): a signed CMR, the carrier's invoice, a transport insurance
document, or the carrier's tracking record. Two proofs from the same party count as one, and two
proofs that disagree on destination or date count as none.

When the **buyer arranges the transport**, the buyer also sends a written receipt declaration
(goods received, date, destination member state), due by the **10th day of the month following the
dispatch**. The platform sends reminders before and after the deadline. The vendor sees a status
per vendor order (`proof_missing`, `proof_partial`, `proof_complete`, `declaration_pending`,
`declaration_overdue`); a vendor order without complete proof is flagged for the vendor and for
staff. The proofs are stored as private documents on the vendor order, with the party that issued
each of them.

### Checkout legal step (ORD.17, owner decision D-40)

The last step before payment, in the core checkout and in any checkout module, follows the counsel opinion of
2026-11-13 (questions L-1 to L-6, D-40). Each seller's sales terms must be readable **before** the button: sending them
only after payment would leave them out of the contract (art. 1341(1) c.c.; artt. 49 and 51 Codice del consumo), and the
right of withdrawal does not cure missing pre-contractual information (art. 53 extends withdrawal by 12 months; art. 57
moves the return costs to the seller; the platform answers for its interface under art. 31 DSA).

**Per seller, in that seller's block of the recap** (in clear or in an accordion that opens on the same page):

- the goods, quantities, prices, VAT and that seller's shipping cost;
- identity: full legal name, "professional seller" status (or not, art. 49-bis), geographic address, VAT or registration
  number, contact email;
- the split of obligations: the sales contract is concluded directly with the seller, who is responsible for shipping,
  conformity, invoicing, returns and withdrawals; the platform provides the marketplace service and collects the payment;
- the delivery time the seller commits to (a date or a range);
- withdrawal for consumers: 14 days from delivery, also through the online withdrawal button; who pays the direct cost
  of returning the goods; for goods that cannot normally be returned by post (pallet, bulky), a reasonable estimate of
  the maximum return cost (art. 49(1)(i)); if it is missing, the seller pays the return;
- the legal guarantee reminder (24 months for consumers) and, when the buyer used the garage, the selected item with
  "Compatibility declared by the seller" (D-38);
- a link "Sales terms of <seller> (version <n> of <date>, download PDF)" that opens a printable page or downloads the
  version in force (art. 12(3) D.Lgs. 70/2003, mandatory for business buyers too).

**Right above the button, with no long text in between** (art. 51(2); CJEU C-249/21):

- the short list of goods and the final total with VAT, shipping and every other charge, with the share of each seller;
- **box 1**, mandatory for every buyer, unticked: the buyer declares to have read and to accept the platform's terms of
  use **and the sales terms of each seller named in the recap** (one link per seller), and to have read the privacy
  notice (an acknowledgement, not a consent: the order runs on the contract basis; any marketing consent is a separate,
  optional, unticked box);
- **box 2**, only between businesses: a **business buyer** (one who chose to buy as a business and gave a VAT number;
  the VIES result drives the tax treatment, not box 2: an unverified number still shows box 2, because the buyer
  declared to act as a business) **and** an Italian party, that
  is the operator established in Italy (`current_operator()`, never a literal) and/or at least one seller in the cart
  established in Italy (owner, 2026-11-13). It is the specific approval under artt. 1341 and 1342 c.c. of the onerous
  clauses, each named by **article number and title** (a generic "I approve all onerous clauses" is not valid): the
  platform terms' list when the operator is Italian (declared per version of the customer terms of use in the same
  structured form as VEN.35, kept with the CMP.5 versioned terms; the vendor-contract clauses of CMP.5 are not part of it), then the list each seller in the cart declared for the version
  in force (VEN.35) when that seller is established in Italy **or its terms choose Italian law**. Any other foreign
  seller's B2B terms follow its own law (Reg. (EC) 593/2008) and its court clause is valid through the pre-contract
  link and box 1 (art. 25 Reg. (EU) 1215/2012), so its clauses are not listed (owner chose this variant A, 2026-11-13).
  The owner chose counsel's option A (2026-11-13): box 2 is never shown to a consumer, because asking a consumer to
  approve clauses that are void for them (artt. 33-36 Codice del consumo) is a misleading practice;
- the button, whose label names the obligation to pay: "Confirm order and pay now – EUR <total>" (IT "Conferma ordine e
  paga ora – EUR <totale>"); never "Complete order" or "Send";
- after a redirect back from the provider (3-D Secure) and before the webhook: a "payment received, confirming" state
  with a short HTMX poll; only the webhook marks the order paid.

**Competent court and applicable law** (counsel confirmed the rule):

- consumer: the court of the consumer's residence, never moved by any term (art. 66-bis Codice del consumo; artt. 17-19
  Reg. (EU) 1215/2012), and the mandatory law of the consumer's habitual residence is kept (art. 6(2) Reg. (EC)
  593/2008);
- business: for disputes with the operator, the court of the operator's seat; for the sales contract, the court in the
  seller's terms, or, when the seller names none, the court of the seller's registered office (the fallback clause in
  the platform terms). In box 2 the court clause is one of the listed onerous clauses;
- the terms never link the retired EU ODR platform (Reg. (EU) 2024/3228 repealed Reg. 524/2013 from 2025-07-20).

The confirmation email carries, on a durable medium (the text or a PDF, not only a link), the accepted platform terms and
each seller's sales terms in the version of the order (art. 51(7)): this is the confirmation duty, never a substitute for
the information before the button. The ready-made texts of box 1, box 2 and the court clauses are in counsel's opinion
(kept private); the legal pages and the translations use them.

## 7.3 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| ORD.1 | Cart, CartLine, guest/user session, merge on login | P1 | 8 | CAT.1 | Cart merge test |
| ORD.2 | Order, VendorOrder, OrderLine with snapshots, numbering, FSM | P1 | 12 | ORD.1 | Illegal transitions impossible (test) |
| ORD.3 | `build_checkout` (tax + shipping per vendor), preview and blocking messages | P1 | 12 | TAX.4, SHP.4 | Mixed 3-vendor cart correct |
| ORD.4 | Transactional `place_order` with StockReservation and price verification | P1 | 10 | ORD.2 | Concurrency test on stock 1 |
| ORD.5 | Cart and checkout UI (HTMX), conditional B2B fields, VIES, Turnstile | P1 | 16 | ORD.3, TAX.5 | Checkout keyboard-accessible |
| PAY.1 | `PaymentGateway` + Stripe implementation (PaymentIntent, transfer_group); owner decision D-09 (2026-11-11): Stripe Connect is the only payment provider at launch, split at source (platform fee versus vendor payout); no other method is built or offered at checkout until PAY.5 and a provider that passes its checklist exist; D-08 decided (adviser opinion 2026-11-11): no PSD2 licence while every customer payment goes through Stripe Connect, and no payment flow collects customer money on an operator bank account | P1 | 12 | ORD.4 | End-to-end test payment; checkout offers only the methods the Stripe provider reports; the platform fee and the vendor payout are separated at the provider, with no platform-side deferred collection; no configured payment flow has an operator bank account as the payee (test on the provider configuration) |
| PAY.2 | Signed, idempotent webhooks, `on_payment_succeeded` | P1 | 8 | PAY.1, VEN.4 | Duplicate event has no effect |
| PAY.3 | Transfer per vendor, reverse transfer, reconciliation with the ledger (D-08, adviser opinion 2026-11-11): every transfer is created automatically on payment and tied to the charge with `source_transaction`, so no free platform balance of third-party money exists; a refund reverses the vendor's share with `reverse_transfer` | P1 | 10 | PAY.2 | 3 vendors in one order: 3 correct transfers; every transfer in the test carries `source_transaction` equal to the order's charge; a refund test calls `reverse_transfer` and leaves no unassigned platform balance |
| PAY.4 | Full/partial refunds with VAT and fees reversed (owner decision D-05, 2026-11-11): the platform gives back the commission, the collection cost and the order-handling fee of the refunded order and charges the vendor a refund fee instead; refund fee = real provider cost of the refund or chargeback taken from the provider's balance transaction + the plan's surcharge (default 2 EUR, charged on every refund event), the total rounded up to the whole euro, read from the VEN.2 CommissionRule in force on the order snapshot (VEN.33); partial refund: fees given back pro rata, one refund fee per refund event; chargeback: the dispute cost is the provider cost; the refund fee is charged to the vendor on every refund, withdrawal included (no cause-based split; the vendor may **not** pass it on to consumers in its return terms (owner's legal review 2026-11-13: Codice del consumo artt. 56-57 forbid charging the consumer refund handling costs)), except a refund caused by a platform malfunction or error (L-10, adviser opinion 2026-11-11): an exclusion flag with a required reason on the refund, set only by platform staff, audited, charges no refund fee; the surcharge is read from the plan version whose effective date covers the order's creation (VEN.33); the refund fee is a platform service line for the CMP.2 statement (T-8); money rules (`Decimal`, `ROUND_HALF_UP` per line, the ceil to the euro the only exception); the service accepts `dry_run=True` and returns a `ChangeSet`, writes `AuditLog`; the PR is flagged for human review (ledger and payments code); channel and initiative per D-41 (2026-11-13): the platform refunds on its own only for its own error or a vendor default; withdrawal and conformity refunds are started by the vendor, who chooses through the platform (by line, quantity or amount, reverse transfer from its share) or outside it (no ledger movement, optional "refunded outside the platform" mark on the line); the choices and the reversal of commission and collection cost are `PlatformOperator` settings (ARC.7), default D-05; a second payment that succeeds on an order already paid (also while the first transfer is still pending) is a **platform-error case** (D-05/L-10), auto-flagged with its reason: audited as `orders.payment_duplicate`, staff alerted, refunded in full by the platform, no vendor share reversed, no transfer, no refund fee (#438 review); a refund that arrives **before a pending transfer** (PAY.3 settles through a deferred job) reduces or cancels that transfer, and no reverse transfer is issued for a transfer never created; a refund by line or quantity converts to `refunded_gross` before `refund_lines(RefundEvent)`; an outside refund (D-41): the platform is not liable for it, the mark requires the vendor's declared refund date and method and is audited, a buyer complaint of non-refund escalates to the vendor-default path (platform-initiated refund), and the mark does not change the DAC7 consideration (CMP.1 reports what the platform paid or credited; the vendor is told; tax advisor, 2026-11-13, D.Lgs. 32/2023 art. 2(1)(q) and art. 12; vendor clause (i), CMP.5) | P1 | 10 | PAY.3, VEN.33 | Partial refund cases green; full refund returns all three fees and writes one refund fee line; a partial refund returns the fees pro rata and each further refund event writes its own refund fee; regression case (owner, 2026-11-11): provider cost 3.15 EUR + 2 EUR surcharge = 5.15 EUR, charged 6 EUR; provider cost 1.37 EUR plus surcharge 2.00 gives 4.00 EUR and an exact 3.00 EUR total stays 3.00 EUR (ceil to the euro); a second partial refund on the same order with provider cost 1.20 EUR + 2 EUR = 3.20 EUR is charged 4 EUR (every refund event carries the surcharge); a chargeback charges the dispute cost plus surcharge; the same refund charges the vendor whether the cause is a withdrawal, a defect or a vendor error; a refund flagged as a platform malfunction or error charges no refund fee and gives the fees back as usual; setting the flag without a reason is refused and setting it writes an `AuditLog` entry; an order created before a surcharge change keeps the old surcharge; the provider cost comes from the balance transaction fixture, not a constant; the plan's surcharge and ceil are read from the snapshot rule, never live; a dry run changes nothing and returns the ChangeSet; the ledger sums to zero for the order after the refund fee; every refund writes an `AuditLog` entry; the PR carries the human-review flag; D-41 cases: a vendor refund through the platform by line, by quantity and by amount each issues one reverse transfer; an outside refund writes zero ledger lines and one audited mark with date and method; with the operator setting off an outside mark is refused, and with commission reversal off no commission or collection-cost reversal lines are written; the defaults reproduce D-05 with the #409 numbers (61.00 of 122.00, provider cost 1.20: reversals 10.00 and 3.44, fee 4.00, clawback 51.56); a duplicate PaymentIntent on a paid order writes `orders.payment_duplicate`, alerts staff, refunds in full, creates no transfer and no refund-fee line; a refund before the pending transfer leaves a reduced transfer, or none when fully refunded, and no reverse transfer |
| PAY.5 | `PaymentProvider` interface (owner decision D-09, 2026-11-11; PAY.1 is its only implementation) and the dev page `docs/dev/payment-providers.md` "how to add a payment method": what a provider must support (split at source or a deferred collection of the platform fee, refunds with the balance-transaction cost for PAY.4, payout reconciliation for PAY.3, signed idempotent webhooks for PAY.2), the contract tests a new provider must pass, and the review steps (D-08 PSD2 check, CMP.5 terms); spec and docs only for now, the code lands with PAY.1 | P2 | 4 | PAY.1 | The dev page exists and lists the four capabilities, the contract-test checklist and the review steps; the interface in PAY.1 matches it and the Stripe provider is the only registered one; a provider missing a capability is refused at registration (test, when built) |
| PAY.6 | Non-split payment methods (proposed by lane-b on the owner's request for ideas, D-09, 2026-11-11; spec only, no code now, waits for the owner to schedule it; D-08 decided, adviser opinion 2026-11-11): allowed **only when the customer pays the vendor directly**, never with customer money collected on the operator's bank account; for such a method the platform fee becomes the platform's own receivable on the vendor, netted from the vendor's next Stripe payout; when the balance does not cover it, a vendor SEPA direct-debit mandate (signed in the Manager, audited, revocable) is the fallback; unpaid receivables are listed for staff and block nothing at checkout until the owner sets a ceiling; refunds of such orders reverse the receivable | P3 | 16 | PAY.5, PAY.4, VEN.4 | Owner schedules the build (counsel's D-08 answer is in); then: a non-split order is possible only with the vendor as the payee of the customer's payment (a test refuses a configuration where the operator receives it); it creates a receivable equal to the platform fee; the next payout is reduced by it; with too little balance a debit is requested under the mandate; a refund reverses the receivable; the ledger stays append-only and sums to the order |
| ORD.6 | Vendor fulfillment: accept, ship with tracking, delivered | P1 | 8 | ORD.2, CORE.9 | Vendor fulfills from its own panel |
| ORD.7 | Customer area: orders, timeline, tracking, quotes | P1 | 10 | ORD.2 | Parity with My Account |
| ORD.8 | Online withdrawal and returns flow | P1 | 12 | PAY.4, CMP.6, ARC.16 | Withdrawal button "withdraw from contract here" (IT "recedere dal contratto qui") visible for the whole withdrawal period, confirmation "confirm withdrawal" (IT "conferma recesso"), acknowledgement email with content, date and time |
| ORD.9 | Transactional emails per language (customer, vendor, staff); per-vendor order confirmation as a non-fiscal document | P1 | 10 | ORD.2, ARC.16 | All emails in EN, IT, DE, FR and ES with snapshot tests; the order confirmation (email and PDF) is per vendor, labelled "Documento non fiscale / Non-fiscal document" in all five languages, carries the order reference and never an invoice number, and no email or document calls itself an invoice or receipt |
| ORD.10 | Checkout and payments kill-switch, alert on payment without transfer | P1 | 4 | ARC.8, PAY.3 | Kill-switch verified on STAGE |
| ORD.11 | Customer account: preferences and request history (scope to be defined with the owner): one section listing the customer's product information and quote requests and holding preferences such as followed vendors and event notifications; placeholder, hours to be re-estimated once the scope is agreed; scope decided by the owner (Q-84, 2026-11-13): the customer's information and quote requests with their history; the vendors the customer follows and their news; event notifications (EVT.16); email preferences and newsletter consent (language, frequency; every marketing consent separate, optional and unticked); wish lists and **gift lists** of products the customer would like to receive, shareable by a private link; other marketing preferences, each an explicit opt-in | P2 | 4 | ORD.7, SHP.10 | The owner has agreed the scope, this row is re-estimated and the section is detailed in §7.2 before any code is written |
| ORD.12 | Free-gift promotion (owner decision MQ-9): triggered by a coupon code or a cart condition; adds the gift product at full price plus a 100% discount line; discount funded by its issuer only (vendor, or platform on the Q-90 model; spec 18 §18.3, no co-funding in v1), gift stock reserved like any line, a per-customer limit; VAT and commission per line; gift rule (T-7, adviser opinion 2026-11-11): a gift is allowed only as an in-kind discount on a paid order, with the gift's value at most the value of the paid goods, never a standalone free supply | P2 | 10 | ORD.4, CAT.17 | A qualifying cart gets the gift line at full price and a 100% discount line; no line is priced 0.00; a vendor-funded and a platform-funded gift give the CPN-VENDOR and CPN-PLATFORM ledger rows; a customer over the limit gets no second gift; an out-of-stock gift is not added; VAT and commission are computed per line; a cart whose gift value would exceed the value of its paid goods gets no gift; an order with no paid goods line gets no gift |
| ORD.13 | Proof of intra-EU dispatch (owner decision D-28, art. 45a Reg. 282/2011): for every reverse-charge vendor order, at least two non-contradictory proofs from independent parties (signed CMR, carrier invoice, transport insurance, tracking) stored as private documents with the issuing party; when the buyer arranges transport, the buyer's written receipt declaration is due by the 10th of the following month, with reminders and a vendor-visible status | P1 | 10 | ORD.2, TAX.3, ARC.16 | A reverse-charge vendor order with one proof, or two from the same party, shows `proof_partial` to the vendor; two contradictory proofs count as none and show `proof_missing`; two independent consistent proofs give `proof_complete`; a buyer-arranged order without the declaration shows `declaration_pending`, gets reminders and is `declaration_overdue` after the 10th of the next month; a domestic or VAT-charged order needs none |
| ORD.14 | Order privacy masking, the vendor "accounting" role and the Tax/Audit permission (D-29, D-30): in `restricted_tax_only` the personal fields (name, addresses, email, phone, tax ids) are masked (for example `j***@example.com`) in every ordinary vendor, support and customer-facing UI, in the API and in exports; in clear only for the vendor owner, a new vendor role "accounting" (assigned by the owner, own orders only) and the platform Tax/Audit permission (a CORE.18 role); every in-clear view needs a reason and writes an `AuditLog` entry with actor, order, time and reason; Support impersonation never reveals the clear fields | P1 | 10 | ORD.2, CORE.18, CORE.17, ARC.9 | A `restricted_tax_only` seed order is masked for a vendor member, a Support user, the API and the CSV export, and in clear for the vendor owner, a vendor accounting member (own vendor only) and a platform Tax/Audit holder; a reasonless reveal is refused; each reveal writes an `AuditLog` entry with actor, order, time and reason; an `active` order is shown normally; the owner can assign and remove the accounting role and no other member can |
| ORD.15 | Vendor fiscal evidence package (D-30): a download per order for the vendor, while the order is `active` or `restricted_tax_only`, holding the order snapshot, the VIES log (time, number, returned name) and the transport proofs; personal fields in clear only to those allowed by ORD.14 (every download audited with a reason); no invoices, the platform issues none; refused once the order is `anonymized` | P1 | 6 | ORD.2, ORD.14, VEN.12 | A seed order's package holds the snapshot, the VIES log with the returned name and the transport proofs; a vendor member without the owner or accounting role gets the masked version; the download is in the `AuditLog`; an `anonymized` order has no package |
| ORD.16 | Email language follow-ups (coherence check 19, ORD.9 1/2 leftovers): the German, French and Spanish bodies of every ORD.9 email are translated (today they fall back to English); vendors get a mail language setting, and vendor mails use it, with the platform language as the fallback | P2 | 4 | ORD.9, I18N.7 | Each ORD.9 email renders in the five languages with no English fallback in de, fr and es (snapshot tests); a vendor with a language set receives mails in it, a vendor with none in the platform language |
| ORD.17 | Checkout legal step (owner decision D-40 and counsel opinion of 2026-11-13, §Checkout legal step): per-seller recap block (identity, professional status, address, VAT number, contact, split of obligations, delivery time, withdrawal and who pays the return, maximum return-cost estimate for goods not returnable by post, legal guarantee, garage item, link to the seller's terms version with PDF); total with each seller's share right above the button; box 1 for every buyer (platform terms plus each named seller's terms, privacy notice read); box 2 only between businesses with an Italian operator and/or a seller established in Italy or choosing Italian law, built from the Italian operator's and those sellers' declared onerous clauses by article number and title (VEN.35); button "Confirm order and pay now – EUR <total>"; accepted versions (platform terms, one per seller, box 2 text) and the time stored on the order; terms on a durable medium in the confirmation email; "payment received, confirming" state after a provider redirect (only the webhook marks paid); ⚖️ (L) counsel answered L-1 to L-6; phase note: this F2 task waits for the F3 rows VEN.35 and CMP.5, so the checkout legal step ships after them | P1 | 14 | ORD.5, ORD.9, PAY.2, CMP.5, CMP.6, VEN.35 | The order cannot be placed without box 1, nor without box 2 for a business buyer when the operator is Italian or a seller in the cart is established in Italy or chooses Italian law (server-side too); a consumer never sees box 2, also when buying from an Italian seller on an Italian operator; a business with an unverified VAT number still sees it; the operator condition reads `current_operator()` and a test with a non-Italian operator and a foreign seller shows no box 2; a business buying only from a foreign seller on a foreign operator sees no box 2; box 2 names every article by number and title for the Italian operator and each seller in the cart that is established in Italy or whose terms choose Italian law, and no other seller; each seller block shows identity, delivery time, return cost and, for a bulky item, the return-cost estimate, and links a downloadable PDF of the terms version in force; the order stores the platform version, one version per seller, the box 2 text and the time; the button label contains the total; the confirmation email attaches the terms; a redirect before the webhook shows the confirming state and the order stays unpaid until the webhook |
| ORD.18 | Single-page accordion checkout, the **first paid module** (owner decision D-40): one page with a compact order summary on top; billing, delivery ("same as billing" ticked by default), shipping per vendor package, courier notes and payment as steps; a finished step closes into an editable summary row; totals recalculate after billing (country, VIES); the Stripe Payment Element opens inline before the payment exists and the order and PaymentIntent are created on "Pay now"; progress (addresses, choices, notes, step) saved server-side with the cart and expiring with it, across devices when signed in; keyboard and phone first; Turnstile survives the steps; ORD.17's legal step is reused, not copied; UI only over the core services, which re-check everything; lives in a private module package (ARC.33); its activation option picks the mixed-cart policy (whole marketplace by default, every vendor entitled, or at least one) | P2 | 24 | ARC.33, ORD.17, PAY.3, PAY.4 | With the module on, a mixed 3-vendor cart pays on one page and a reload resumes at the same step; with it off, the core checkout runs; a tampered step (skipped terms, changed price) is refused by the core services |
