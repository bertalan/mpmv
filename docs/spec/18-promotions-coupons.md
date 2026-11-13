# 18 — Coupons, abandoned-cart recovery and return incentive

Proposal for decision D-39 (owner ideas of 2026-11-10): a coupon engine, a reminder for abandoned
carts, and an incentive that appears when the customer comes back. Status: **proposal, under
review**. One rule is **decided**: **whoever sets up a coupon pays for it** (the vendor, and in
some cases the platform). Everything else is a proposal; the rows in §18.11 are P2 and P3 and none
blocks go-live. The design was written independently of any other notes, so §18.1 also says what it
would not build.

## 18.1 What exists today and what this design would not build

- The spec has **no coupon engine**. The only related text is ORD.12 (free gift, owner decision
  MQ-9, [07](07-cart-orders-payments.md)): a promotion "triggered by a coupon code or a cart
  condition" that adds a gift line at full price plus a 100% `discount_net`. It leaves the code
  mechanics undefined. Bundle discounts (CAT.16 to CAT.18, [03](03-catalog.md)) have their own
  funding split and ledger kinds and stay as they are.
- `OrderLine.discount_net` exists; `Cart` has no discount, no code and no owner consent flag.
  Nothing detects abandoned carts. There is no marketing consent model: CORE.19 says
  newsletters "need consent and unsubscribe, a separate module", and ARC.16 sends no tracking
  pixel. The legacy shop has no coupon or recovery logic worth porting.
- Rules already in force: a price is never 0.00 (Q-31); the platform is not the seller and issues no
  invoices (D-21, [00](00-vision-and-review.md)); VAT is computed per line with `ROUND_HALF_UP`;
  commission is computed on the taxable goods amount ([04](04-vendors-commissions-ledger.md),
  VEN.3); no tracking pixel in marketing emails without consent (Garante provision 284 of
  2026-04-17, [11](11-compliance.md) CMP.4); order data lives under D-29 and D-30, carts are
  non-fiscal data; one business time zone (D-31).

**What this design would not build** (each is a deliberate cut, not an oversight):

1. **Stored value**: gift cards, credit notes as balance, wallet credit. They are vouchers under
   VAT directive articles 30a to 30b and may be e-money; a coupon code that only gives a discount is
   not (§18.4). Nothing here issues a balance.
2. **Co-funding of one coupon** by vendor and platform. One coupon, one funder. Shared funding
   stays where it already exists (bundles, CAT.16).
3. **Platform money behind a vendor's coupon**, or a vendor's money behind a platform coupon.
4. **A coupon that appears for any returning visitor** (exit-intent pop-up, profiling-based "welcome
   back"): it teaches people to abandon, needs profiling, and is trivially farmed. The incentive reaches
   the person who left a cart (a signed-in customer or an opted-in guest) through the recovery email
   and, decided by the owner (Q-P7), through a non-modal box on the site for a signed-in visitor
   (§18.7); never a pop-up to anyone else.
5. **SMS, push, WhatsApp** (owner: not now).
6. **Open-tracking pixels**, countdown timers, "only 2 left" unless true, rising-value coupon ladders,
   machine-learned discount sizing, price personalisation of the list price.
7. **Giving vendors customer lists**: a vendor sets the rules and the budget and sees aggregate
   results; it never gets recipients' addresses.
8. **Clawing back a discount** when a customer withdraws from part of an order (§18.5).
9. **A zero-total order path**: a coupon cannot take the payable total below the minimum charge (§18.3).

## 18.2 Coupon model

A new app `promotions` (prefix PRM). It depends on `catalog` and `vendors`. `orders` calls
`promotions.engine` with a cart snapshot and never the reverse; `tax` and `shipping` never import
`promotions` (spec 01 §1.3).

```python
class Promotion(Model):                  # versioned: a row with redemptions is never edited, a new version is added
    issuer_kind = CharField(["vendor", "platform"])
    issuer_vendor = FK(Vendor, null=True)        # set iff issuer_kind == "vendor" (check constraint)
    # NO separate funder field: the funder IS the issuer. This is the decided rule, kept in the schema.
    name (internal); customer_label (translated)
    kind = CharField(["percent", "fixed", "free_shipping", "gift"])    # gift = ORD.12 on this engine
    value = Decimal                                # percent 0.01..100, or an amount in the order currency
    max_discount = MoneyField(null=True)           # cap for percent kinds
    scope_kind = CharField(["order", "vendor", "products", "categories"])
    scope_products / scope_categories              # M2M; a vendor promotion is always limited to its own catalogue
    exclude_discounted = bool                      # default true: lines with a public reduction or a bundle price
    activation = CharField(["code", "automatic", "personal"])
    valid_from; valid_to                           # business time zone (D-31), stored UTC
    state = CharField(["draft", "active", "paused", "ended"])
    # limits
    max_redemptions_total; max_per_customer; min_order_net; min_items; first_order_only: bool
    budget_cap = MoneyField(null=True)             # total the funder is willing to spend; mandatory for platform promotions
    combinable = bool                              # default false (see stacking)
    created_by; version

class Coupon(Model):                             # a code that activates a promotion
    promotion; code (normalised: upper case, no spaces, unique per platform); kind = ["public", "personal"]
    customer = FK(User, null=True)                 # personal codes: bound to one account
    expires_at; source = ["manual", "recovery_campaign", "import"]; created_at

class Redemption(Model):                         # one row per use; the unit of every limit and report
    promotion_version; coupon (null for automatic); customer (null = guest); order (null while reserved)
    state = ["reserved", "redeemed", "released"]; reserved_until; amount_net (snapshot)
```

**Types.** `percent` (of the eligible line price, optional `max_discount`), `fixed` (an amount off
the eligible lines, shared as in §18.3), `free_shipping` (zeroes the shipping of the vendor or vendors
in scope), `gift` (adds a line at full price plus a 100% discount line, ORD.12; T-7: only as an in-kind
discount on a paid order, the gift's value at most the value of the paid goods).

**Scope.** A vendor promotion covers that vendor's catalogue only: the whole vendor, a product set or a
category set. A platform promotion may span vendors, narrowed by categories. A platform promotion
reaches a vendor only if that vendor opted in (§18.4). No country scope by default: limiting a promotion
by the customer's country can fall under the geo-blocking rules (⚖️ L-8).

**Activation.** `code` (public code typed by the customer), `automatic` (applies when the conditions
hold, for example "free shipping over 80 euro"), `personal` (a single-use code bound to one account,
created by a recovery campaign or by hand). Public codes may be chosen by the vendor; personal codes
are generated with 60 bits of randomness, case-insensitive, no ambiguous characters.

**Limits.** Per customer (counted over `reserved` and `redeemed`), total uses, minimum net order,
validity window, optional first-order-only. Guests: a limit per customer is applied to the
normalised email (case folded, plus-tags removed) and is best effort; personal codes need an account.

**Stacking.** Default rules, all in the pure engine:
1. A line receives **at most one price discount**. If several qualify, the one giving the customer the
   largest amount on that line wins, ties by lowest promotion id. Percent discounts never multiply.
2. One `code` slot per cart per funder context: one platform code, plus one code per vendor on that
   vendor's lines, plus automatic promotions. Entering a second code in the same slot replaces the first
   with a visible message.
3. `free_shipping` and a price discount stack. A `gift` stacks with both.
4. Bundle lines (CAT.16) and lines with a public price reduction are excluded unless the promotion
   sets `exclude_discounted = false` (vendor choice, shown in the preview).
5. A promotion with `combinable = false` is never combined with another price discount on the same line.

## 18.3 Funding and recording

### The rule, stated once

**Decided (owner, 2026-11-10, Q-P2; platform coupons on the Q-90 model, owner and adviser
opinion 2026-11-11, T-1 and T-1 bis):** the promotion's issuer is its funder and the only payer. The
customer-visible price reduction is identical in both cases, and so is the VAT base: **the discounted
price** (T-1 bis), because the vendor's own invoice to the customer always shows the coupon as an
unconditional discount. What changes is who bears it:

- **Vendor coupon:** the vendor bears the discount. The commission is computed on the pre-coupon total
  and does not depend on the coupon.
- **Platform coupon (Q-90 model):** the vendor still shows the discount on its invoice, and the
  platform **reduces its commission** on that vendor order by the same amount. If the coupon is larger
  than the order's commission, the excess is netted against the same vendor's commissions of the month
  on the periodic statement (CMP.2); the total of a period never goes below zero, and an excess larger
  than the whole month's commissions carries over to the next month as a vendor credit against future
  commissions. **No cash is ever paid out** for a coupon, and there is no "third-party payment" path:
  the platform's share is never treated as consideration paid by a third party (T-1).

Each funder has its own ledger kinds (below), never a hidden line discount.

| | Vendor promotion | Platform promotion |
|---|---|---|
| Customer pays | list price − discount | list price − discount |
| Vendor's invoice | list price with an unconditional discount | list price with an unconditional discount |
| Vendor's VAT base | list − discount (T-1 bis) | list − discount (T-1 bis) |
| Commission | on the list price | on the list price, reduced by the discount (Q-90) |
| Vendor receives | list − commission − discount | list − commission (as without the coupon) |
| Platform bears | nothing | the discount, as commission it does not earn (marketing cost) |

### Commission base (decided)

**Decided (Q-P2): commission is computed on the pre-coupon total (the list price), for both funders.**
Worked example (owner): item 100, vendor coupon 20, commission 10%: the customer pays 80, the 20 is debited
from the vendor's credit, commission is 10 on the pre-coupon total, the vendor nets 100 − 10 − 20 = 70. With
a platform coupon of 20 the platform's commission of 10 on that order drops to 0, the other 10 is netted
against the vendor's other commissions of the month (or carried over), and the vendor nets 90.

Example: line 100, commission 15%, coupon 10. Vendor coupon: customer pays 90, commission 15, vendor
nets 75, so the vendor paid exactly 10 more than without the coupon. If commission were computed on the
discounted price (13.50), the vendor would net 76.50: the platform would silently pay 1.50 of a
coupon the vendor set up, which contradicts the decided rule. For a platform coupon the vendor gets
85 as if there were no coupon, and the platform gives up 10 of its commission (Q-90).

Why list: (1) it is the only base that makes "whoever sets it up pays" literally true; (2) one rule,
independent of the funder, so a vendor cannot reduce its commission by discounting and a platform
coupon never changes a vendor's net; (3) it matches the bundle precedent (a vendor-created bundle:
vendor net = list − normal commission − own share, [03](03-catalog.md)). Costs: a vendor pays
commission on money its customer did not pay (a 50% coupon on a 15% rule is an effective 30% on the
price paid), the contract and the statement must say so (VEN.30), and VEN.3's current wording
("taxable amount of the vendor's goods") changes. The fallback is a versioned
`CommissionRule.coupon_base` = `list` (the decided default) or `paid`; the rule snapshot on the order
records which one applied, so a later policy change never touches old orders.

**Guard rails.** A vendor-funded discount on a line may not exceed that line's list price minus its
commission and its share of the payment fee, so the vendor's net per line never goes negative:
percent promotions are validated at creation (percent ≤ 100 − commission% − fee%), fixed ones are
clamped at pricing with a visible note. The payment fee stays computed on the gross actually paid, as
today (case FEE-NETTO).

### Allocation across lines and vendors

Pure function `apply_promotions(cart: CartSnapshot, promotions) -> PricedCart` in
`promotions/engine.py`:

1. Select eligible lines per promotion (scope, exclusions, stacking rules above).
2. `percent`: per line, `round(line_net × percent)`, `ROUND_HALF_UP`, capped by `max_discount`.
3. `fixed`: the amount is shared across the eligible lines in proportion to their net, each share rounded
   `ROUND_HALF_UP`; the rounding remainder goes to the largest line (as in CAT.16). A fixed amount
   larger than the eligible total is cut to that total.
4. `free_shipping`: the vendor's `shipping_net` is reduced to zero on the vendors in scope; the VAT
   portions follow from `resolve_shipping_tax` on the reduced amount (shipping VAT follows the goods,
   D-03), so the discount is shared over the rates like the shipping itself.
5. Tax is resolved first (the rate does not depend on the price) and applied to the discounted net:
   `tax_amount = round(rate × (line_net − discount))` per line. The tax engine is unchanged.
6. **Minimum payable.** If the order total after all discounts would fall below the platform's minimum
   charge (0.50 in the order currency, a setting), the promotion is not applied to that cart and the
   customer is told why. No zero-total order path exists.
7. A vendor promotion in a mixed cart touches only that vendor's lines; a platform promotion's discount
   is shared across vendors over the eligible lines as in step 3.

### Snapshots on the order

Orders store snapshots, never live references (spec 01). `OrderLine.discount_net` keeps its meaning (the
reduction the customer sees). New immutable rows:

```python
class OrderLineDiscount(Model):      # append-only; one row per promotion per line
    order_line; promotion_id (SET_NULL) + promotion_version + code snapshot + label snapshot
    kind; funder ("vendor" | "platform"); funder_vendor (null for platform)
    amount_net   # always reduces the VAT base (T-1 bis, §18.4); no VAT-mode field
    rule_snapshot (JSON: percent or fixed value, cap, scope, commission_base mode)
# VendorOrder: shipping_discount_net, shipping_discount_funder
# OrderLine: list_unit_price_net (the existing unit_price_net), tax_base_net (what VAT was computed on)
```

The non-fiscal order confirmation and the CMP.23 export carry `list_net`, `discount_vendor_net`,
`discount_platform_net` and `tax_base_net` per line, because the vendor invoices with these figures.
Redemption is **reserved** at `place_order` (with the stock reservation, same 15-minute expiry, unique
constraint for single-use codes under `select_for_update`), **redeemed** in `on_payment_succeeded`
(idempotent per event), **released** on cancel or expiry.

### Ledger

`LedgerEntry.kind` gains, P2, next to the bundle kinds: `coupon_vendor_funded` (the vendor's
discount, a debit of the vendor's balance), `coupon_platform_discount` (the discount the vendor shows
on its invoice for a platform coupon), `commission_coupon_reduction` (the platform's commission given
up for it, at most the order's commission) and `commission_coupon_excess` (the rest, netted on the
periodic statement). `sale` is the list goods amount in every case:

| Case (line 100, rule 15%, coupon 10) | Rows for the vendor | Vendor net | Platform |
|---|---|---|---|
| Vendor-funded | `sale` +100, `commission` −15, `coupon_vendor_funded` −10 | 75 | +15 |
| Platform-funded (Q-90) | `sale` +100, `coupon_platform_discount` −10, `commission` −15, `commission_coupon_reduction` +10 | 85 | +15 − 10 = +5 |
| Platform-funded, coupon 20 | `sale` +100, `coupon_platform_discount` −20, `commission` −15, `commission_coupon_reduction` +15, `commission_coupon_excess` +5 (netted on the statement) | 80 on this order, +5 against the month's other commissions | 0 on this order, −5 on the month |

The sum of `sale` and the discount row always equals what the customer paid for goods, which is what
the nightly Stripe reconciliation checks. The transfer for an order never exceeds what its customer
paid: the platform never tops up a transfer from its own funds. A `commission_coupon_excess` is
consumed by the vendor's later commissions, month by month, and is never paid out. The platform's
marketing cost is the sum of `commission_coupon_reduction` and consumed `commission_coupon_excess`
rows, reported to staff (Finance and payments role) per promotion and budget. The ledger is still not
accounting (spec 00, 2026-11-07). The commission statement (CMP.2) shows the reduced commission and
the netted excess. DAC7 (CMP.1, T-6): `Consideration` is what is paid or credited to the vendor net of
the platform's fees and taxes, and the fees go in `Fees`; on the case above a vendor coupon gives
Consideration 75 and Fees 15, a platform coupon Consideration 85 and Fees 5.

### Refunds, withdrawal and returns

- **A refund returns what the customer paid for the line**: list − allocated discount, VAT from the
  snapshot (`tax_base_net`), proportional for a partial quantity. The refund row is that amount.
- Vendor-funded, line above refunded in full: `refund` −90, `commission_reversal` +15, `coupon_vendor_funded`
  stays: 100 − 15 − 10 − 90 + 15 = 0. Platform-funded: `refund` −90, `commission_reversal` of the
  commission actually charged (+5, that is 15 less the reduction of 10), and the reduction is not
  given back twice: 100 − 10 − 15 + 10 − 90 + 5 = 0. An excess already netted on an earlier statement
  is reversed on the next one (CMP.2, T-8). D-05 applies unchanged (full: commission and fee
  returned; partial: proportional).
- **No clawback.** Returning part of an order never removes a discount on the lines kept: a minimum
  order or the loss of a gift trigger is checked at order time only. Reason: a penalty for using the
  withdrawal right clashes with directive 2011/83/EU, art. 14 (⚖️ L-9). The cost is bounded by
  one-per-customer limits and the abuse rules.
- **A coupon is not given back** after payment, except when the order is cancelled before dispatch (the
  redemption is released and the coupon works again until it expires). A refund after delivery does not
  restore it (Q-P11).
- A gift line (ORD.12) is not recalled when the triggering item is returned; the vendor may add the
  gift to a return request as a normal line.

## 18.4 VAT treatment (⚖️ tax advisor)

- **A code that only gives a discount is not a voucher.** VAT directive article 30a(1) excludes
  instruments that only entitle to a price reduction; articles 30a to 30b (single-purpose and
  multi-purpose vouchers) apply to instruments redeemable for goods or services, which is why the design
  issues no stored value (§18.1).
- **Vendor-funded: decided (adviser opinion 2026-11-11, T-1 bis).** The vendor's VAT base is the
  discounted price (80 in the owner's example), with the discount on the vendor's own invoice. The
  order stores both figures (`tax_base_net` and the list net) for the statements; there is no mode
  setting. Per-line `ROUND_HALF_UP` and the 0.50 floor are confirmed (T-2), and a vendor in an
  exemption or special VAT regime follows the same rule (T-4).
- **Platform-funded: decided (owner and adviser opinion 2026-11-11, T-1, Q-90).** Customer pays 90.
  The vendor's invoice shows 100 with an unconditional discount of 10, so its VAT base is 90, and the
  platform reduces its commission by 10. The vendor receives 85, as without the coupon. There is no
  "third-party consideration" reading and no VAT-mode setting. This is the same model as the bundle
  design (CAT.16, Q-90), so the two features do not diverge.
- **Shipping (T-5).** Platform-paid free shipping follows T-1: the vendor's invoice shows shipping 0,
  or a 100% discount on it, and the platform reduces its commission by the same amount; the VAT split
  follows `resolve_shipping_tax` on the reduced amount.
- Under Q-90 the platform's cost is commission it does not earn, shown on the CMP.2 statement; how
  the operator records it is for the operator's accountant (T-3) and blocks no row.

## 18.5 Abandoned-cart detection and recovery

### When a cart is abandoned

A cart is **abandoned** when all of these hold: it has at least one line; its owner is a signed-in
customer or a guest who ticked the checkbox (§18.6); it has had no activity (`updated_at`) for
`CART_IDLE_MINUTES` (default 120, range 60 to 4320); the cart total is at least `RECOVERY_MIN_CART_NET`
(default 15.00); no order was placed since the last activity; and at least one line is still
purchasable (`sales_mode=buy`, vendor active, stock available). A price change since `added_price_net`
and a stock change are shown in the email, never hidden. Detection is a job every 15 minutes in the
business time zone; it only reads carts, it writes a `RecoveryDispatch` row per decision (sent,
skipped with a reason, holdout).

### Two different messages, kept apart

1. **Payment-incomplete notice (a service message, PRM.11).** The customer started a checkout that has
   an `Order` in `awaiting_payment` (card failed, page closed). One message within 24 hours, no coupon, no
   promotion, no consent needed because it concerns an order the customer placed. It is a normal ARC.16
   transactional email.
2. **Cart reminder (marketing, PRM.10).** Anything about a cart with no order. Treated as marketing
   in every case, even when its wording is neutral: an unsolicited reminder with a commercial aim is a
   commercial communication under article 13 of directive 2002/58/EC (ePrivacy) and, in Italy, article
   130 of the Codice della privacy, and the Garante has treated reminders about carts and offers
   that way (⚖️ L-1 confirms). It needs consent.

### Consent and the soft opt-in

- **Explicit opt-in consent** for cart reminders, purpose-specific (`MarketingConsent.purpose =
  cart_reminders`, separate from any future newsletter), unticked checkbox at account creation, in
  checkout and in the account preferences (ORD.11), with a versioned text, timestamp and method;
  append-only; withdrawal is as easy as the grant. Double opt-in is not required by law but is
  recommended for guests (§18.6).
- **Soft opt-in** (article 13(2) ePrivacy; Italy: article 130(4) Codice della privacy) lets a seller email
  its own customers about its own similar products when it got the address in a sale. **Not used at
  launch.** The platform is not the seller (vendors sell in their own name), the platform's role in the
  customer relationship is for counsel to qualify (controller, joint controller), and the Garante reads the
  exception narrowly. It is simpler, and safer, to ask. Counsel can open the exception later for a
  vendor's own customers (L-2).
- **Send-time checks.** Consent and the suppression list are checked again when the email is built,
  not only when it is queued, so a withdrawal after queuing wins. Unsubscribed or erased
  addresses are kept as a salted hash on the suppression list only, to honour the refusal (legitimate
  interest, minimal data).
- **Controller roles.** The platform sends the reminder and is its controller. A vendor that funds a
  campaign (§18.7) never receives the address or any individual's identity; it gets aggregates.

### Channels, frequency, timing

- **Email only**, through the ARC.16 outbox (idempotent per `(cart, step)`), in the recipient's
  language, as a `marketing` template class: no tracking pixel at all, CSP-safe HTML plus text,
  sender identity and postal address in the footer, no urgency timers, no scarcity claims that are
  not true, no price shown as reduced unless §18.9 allows it. No SMS, no push, no WhatsApp.
- **Caps (settings, defaults):** at most **2 messages per cart** (§18.7 ladder); at most **one cart
  sequence per customer per 14 days**; at most 4 marketing emails per customer per 30 days across all
  marketing purposes, newsletters included when they exist.
- **Quiet hours:** messages are sent between 09:00 and 20:00 in the business time zone (D-31,
  `business_tz()`); a message due outside the window is moved to 09:00 the same or next day. No
  per-recipient time zone (not collected).
- **Unsubscribe:** the `List-Unsubscribe` header with `List-Unsubscribe-Post: List-Unsubscribe=One-Click`
  (RFC 8058), plus a footer link to a page that withdraws the purpose with a POST; the token is a CORE.15
  link bound to recipient and purpose, a GET changes nothing. The effect is immediate and audited.
- **The link** in the email is a CORE.15 secure action link, purpose `cart_restore`, bound to the cart and
  the recipient, valid 7 days. It opens the cart; it does not sign anyone in (the customer logs in as
  usual), and it never contains the coupon code in clear text in the URL.

### Retention (D-29, D-30, D-31)

Carts are non-fiscal data: deleted `CART_RETENTION_DAYS` (30) after the last activity, immediately on
account erasure (D-30). `RecoveryDispatch` rows keep a pseudonymous user id, cart id, step, outcome and
the experiment group for 13 months, then drop the user id (the aggregates stay). `MarketingConsent`
evidence is kept while the consent is valid and for a configurable evidence period after withdrawal
(default 24 months, ⚖️ L-3). Personal coupons lose their customer binding when the account is erased; the
order's discount snapshot is part of the order and follows its retention. Jobs run in the business
time zone with a dry-run report and an `AuditLog` entry per run (the same pattern as the order
retention jobs).

## 18.6 Guests

**Decided (owner, 2026-11-10, Q-P6): guests are included.** A first-party cookie recognises a returning
guest's own cart on the site (a functional cookie: it is the cart they built, no profiling). E-mail
reminders go to a guest **only with the unticked checkbox** at the email step ("Email me about this cart"),
never to an address typed and not submitted: a guest's email exists to deliver the order and is not a basis
for marketing. The checkbox is stored as `MarketingConsent` with double opt-in (the first message asks for
confirmation and carries nothing else), the same caps, and the same retention. The soft opt-in does not
apply to a person who never bought.

## 18.7 Return incentive

The owner's idea: when the customer comes back, an incentive and a coupon appear. This design gives
the incentive **through the recovery email and on the site** (decided, Q-P7), details configurable per
campaign. By email it is applied when the customer opens the restored cart, so the coupon "appears" in the
cart as a visible discount line. On the site, a signed-in visitor with a qualifying abandoned cart sees a
non-modal box with the cart and the coupon, under the same cooldown, minimum, validity, holdout and the
"created automatically from your cart" notice; it is not shown to anyone who simply visits.

### Campaign model

```python
class RecoveryCampaign(Model):
    owner_kind = ["vendor", "platform"]; vendor (null iff platform)
    state = ["draft", "active", "paused", "ended"]; valid_from; valid_to
    step1_delay_minutes (reminder, default 120); step2_delay_hours (incentive, default 24, null = no incentive)
    incentive = {kind: percent | fixed | free_shipping; value; max_discount; validity_days (default 5)}
    min_cart_net; cooldown_days (default 60); lifetime_cap_per_customer (default 3)
    budget_cap (mandatory, the owner of the campaign pays: §18.3); holdout_percent (default 10)
```

The funder of the incentive is the campaign's owner, so it is a `personal` promotion issued by the owner
(`issuer_kind` = `owner_kind`). Enabling a vendor campaign is a financial commitment: it needs the
operational mode of D-32 (fresh passkey) in the vendor's Manager shop space, shows the vendor "you pay at
most X per redeemed coupon, Y in total" and the worst case on one order, and is audited.

### The ladder (recommended)

1. **Step 1 (about 2 hours): a plain reminder** with the cart and no incentive. Many customers return
   after this one, and an incentive sent too early is pure cannibalisation.
2. **Step 2 (about 24 hours, only if the cart is not recovered and a campaign is active): the same
   reminder plus a personal single-use coupon**, valid a few days, applied automatically when the cart is
   restored through the link. Optionally the owner sets step 2 only (immediate incentive, Q-P5).

### Which campaign wins in a multi-vendor cart

One email, one incentive. In order: (1) an active platform campaign the cart is eligible for; (2) otherwise the
active vendor campaign of the vendor with the **largest share of the cart's net value**, ties by the earliest
campaign activation; the coupon covers that vendor's lines only. Never several vendor coupons, never
by budget size: a vendor cannot buy the slot with a larger budget. The rule is published in the vendor
terms (P2B regulation 2019/1150, article 7, differentiated treatment; VEN.30).

### Abuse controls (people abandoning on purpose)

- The cooldown counts from the **last coupon sent**, not the last redeemed, so repeating the trick does not
  help; one incentive per customer per `cooldown_days`, a lifetime cap, and a first-incentive rule can
  require an earlier completed order (campaign setting).
- A minimum cart value and a `max_discount` cap keep the value small. The email never says why the
  coupon was sent in a way that teaches the pattern, and the site never mentions it.
- Account eligibility: verified email and an account at least 7 days old; exclusion of accounts with
  refund-abuse flags; no incentive within 24 hours of a completed order.
- A personal coupon works only for its bound account and only on the owner's lines; forwarding the
  email does not help.
- Detection of farms: the redemptions per shipping-address hash, per payment-method fingerprint
  (Stripe) and per device-independent signals are counted over the cooldown. Over the limit, the redemption
  is not blocked silently: the order is flagged for staff review (the discount is kept pending) and the pattern
  is shown in a platform report. No automated penalty on the customer.
- Vendors cannot target or exclude named customers.

### Measurement

Cohort: carts that met the abandonment rules and whose owner is consented. A fixed `holdout_percent`
(default 10) is chosen by a stable hash of `(cart_id, campaign_id)`; holdout carts get nothing and are
logged. Window: 7 days from the send (or from the would-have-been send time).

- **Recovery rate** = recovered carts ÷ cohort, treated and holdout. "Recovered" = an order placed by the
  same account, within the window, with at least one of the cart's products.
- **Incremental recovery** = treated rate − holdout rate; **cannibalised** = holdout rate × treated count
  (orders that would have happened anyway, each carrying a discount); **cost per incremental order** =
  total discounts ÷ incremental orders; **incremental margin** = incremental net sales − commission effect
  − discounts. A step-1-only arm can be compared with the step-1-plus-step-2 arm when volume allows.
- There is **no open rate**: the emails carry no pixel (Garante provision 284). Attribution uses the
  restore link and the order, not opens. The per-send token inside the link is the only individual
  identifier (⚖️ L-7).
- Reporting: the vendor sees its own campaign in aggregate and nothing below 10 users per cell; the platform
  sees all of it. Holdout without sending is not marketing and needs no consent.

## 18.8 Security and abuse

- **Code guessing.** Public codes are guessable by nature (a vendor picks `SUMMER10`), so the defence is
  throttling: apply attempts per account (10 per hour), per IP (30 per hour) and per session, the Turnstile
  captcha after 5 failures, a 15-minute lockout, and one uniform answer ("this code cannot be used")
  that does not tell unknown from expired or used. Personal codes (60 bits) are not guessable. Codes are
  compared in constant time and never written to logs in clear text. The apply endpoint goes on the SEC.13
  anonymous and public form register (and the sweep test) when that task is merged.
- **Concurrency.** Single-use and total limits are enforced by a unique constraint and `select_for_update`
  on the reservation; a test fires two simultaneous checkouts on one code.
- **Stacking and rounding.** One discount per line, clamps, the minimum payable total, never a negative line; a
  property-style test over random carts checks that the sum of the discounts never exceeds the eligible
  total and that vendor net per line is never negative.
- **Price gaming with platform money.** A vendor could raise a price and let a platform coupon pay the
  difference. Platform promotions apply only to prices unchanged or lowered in the last 30 days (the
  same price history as the Omnibus rule, CMP.6), carry a per-vendor and per-order cap inside the budget, and
  alert staff when one vendor takes an unusual share of a budget.
- **Self-dealing.** Orders by accounts linked to the vendor (member accounts, same shipping address or tax
  id, operator-owned shop VEN.26) cannot redeem platform coupons on that vendor's products.
- **Refund abuse.** See §18.3: refunds return what was paid; the no-clawback rule is bounded by the limits
  and the review queue. A coupon is not restored after dispatch.
- **Audit.** Every promotion and campaign change is an `AuditLog` entry with before and after, and a
  vendor's promotions never show another vendor's data (tenant isolation in the services, CORE.3).
  Platform promotions need the Finance and payments role (budget) with Configuration and tax for the VAT
  mode, step-up on activation, and a second approver above a budget threshold (Q-P4).

## 18.9 Omnibus and price display

- **A coupon is not a reduction of the displayed price.** Listings, product pages and search show the
  vendor's price. A coupon price is never shown as a "now" price next to a struck-through "was" price, and
  a coupon appears as its own line in the cart and at checkout (list, coupon, total).
- **Public promotions with a "was" price.** If a vendor shows a reduction on the listing itself (a
  price cut, not a coupon), directive 98/6/EC article 6a applies (Omnibus; Italy: article 17-bis Codice
  del Consumo, ⚖️ L-5 to check): the prior price is the **lowest price of the last 30 days**, from the
  price history of CMP.6. A public code announced in marketing ("10% off with SUMMER10") is treated in the
  design as an announced reduction: the promotion page shows the code, the condition and the end date,
  and any "was" price follows the 30-day rule. The CMP.6 strikethrough test applies.
- **Personal coupons.** A single-use code sent to one customer is not an announcement of a price reduction
  to the public, so article 6a is not triggered by it (commission guidance on the Price Indication Directive,
  2021, to be confirmed by counsel); the design makes the question mostly moot by never displaying a "was"
  price for it. Because the offer is generated from the customer's own cart behaviour, the email and the
  cart line say so in plain words ("an offer created automatically from your cart"), covering the
  personalised-price information duty of article 6(1)(ea) of directive 2011/83/EU (⚖️ L-4). No decision
  with legal or similar effect is taken (GDPR article 22 is not engaged), no sensitive or device data is
  used, and the list price itself is never personalised.
- **UCPD.** No false urgency (a countdown only if the coupon really expires then), no "limited stock" unless
  the stock is limited, no bait pricing, clear conditions (minimum, exclusions, validity), no code that
  silently does not work.
- **Vendor duty.** A vendor must not raise a price to fund a discount: added to the CMP.10 vendor
  checklist and the vendor terms (VEN.30).

## 18.10 Mandatory regression cases

Names are stable test names. Amounts are line net before VAT, rule 15%.

| Case | Setup | Expected |
|---|---|---|
| CPN-VENDOR | Vendor coupon 10% on line 100 | Customer 90, commission 15 on the pre-coupon 100, vendor net 75, platform +15, rows `sale` 100, `commission` −15, `coupon_vendor_funded` −10; VAT base 90 (T-1 bis) |
| CPN-PLATFORM | Platform coupon 10 on line 100 | Customer 90, vendor invoice 100 with an unconditional discount 10 and VAT base 90, commission 15 reduced by 10 to 5, vendor net 85, platform +5; rows `sale` 100, `coupon_platform_discount` −10, `commission` −15, `commission_coupon_reduction` +10; no cash row from the platform |
| CPN-PLATFORM-EXCESS | Platform coupon 20 on line 100; (a) the vendor's other commissions of the month are 30; (b) it has none | The order's commission drops to 0 and the excess 5 is a `commission_coupon_excess` row; (a) the month's statement total is 25; (b) the month's total is 0, a vendor credit of 5 carries over and reduces the next month's total; no payout or cash line, no negative period total |
| CPN-FIXED-SPLIT | Fixed 10 on lines 60 and 30 and 10 | Shares 6, 3, 1 (HALF_UP, remainder to largest), sum 10 |
| CPN-MULTI | Platform fixed 20 across vendors A (100) and B (50) | Shares 13.33 and 6.67, each vendor's commission is reduced by its share and each vendor still nets list − commission, platform cost 20 as commission not earned |
| CPN-MINPAY | Coupon makes total 0.10 | Promotion not applied, customer told, no zero-total order |
| CPN-GIFT-T7 | Gift worth 30 on a cart with paid goods 20; the same gift with paid goods 50; a cart with the gift only | No gift on the first cart, gift line at full price plus a 100% discount on the second, no gift and no order with no paid goods line on the third (T-7) |
| CPN-STACK | Two qualifying percent coupons on one line | Only the larger applies, never multiplied |
| CPN-REFUND-VENDOR | Full refund of CPN-VENDOR | Ledger nets to 0, refund = 90 |
| CPN-REFUND-PARTIAL | Return one of two lines under a fixed coupon | Refund = that line's paid price, no clawback |
| CPN-RACE | Two checkouts, single-use code | One wins, one gets the uniform refusal |
| CPN-BRUTE | 6 wrong codes in a minute | Captcha then lockout, uniform message, no enumeration |
| CPN-GAMING | Vendor raises price 10 days ago, platform coupon | Line excluded from platform coupon |
| RCV-GUEST | Guest cart | Never in the cohort without a ticked guest checkbox and a confirmed double opt-in; a typed but unsubmitted address is never used; the on-site cookie only restores the guest's own cart |
| RCV-CONSENT | Consent withdrawn after queueing | Not sent; suppression holds |
| RCV-QUIET | Due 23:30 business time | Sent 09:00 next day |
| RCV-CAP | Second abandonment 5 days after a sequence | No second sequence within 14 days |
| RCV-COOLDOWN | Coupon sent, never redeemed, new abandon within 60 days | No second incentive |
| RCV-WINNER | Cart A 70, B 30, both vendors have campaigns | A's campaign, coupon only on A's lines |
| RCV-HOLDOUT | 1000 consented carts, holdout 10% | About 100 held out, stable assignment, report shows incremental and cannibalised |
| RCV-PIXEL | Any recovery email | No image tracker, link only |

## 18.11 Tasks

All rows are P2 or P3 (phase F6). Estimates are first guesses, to be re-estimated when the owner
decides. Open owner questions are in §18.12, tax advisor (T-) and counsel (L-) questions in §18.13.

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| PRM.1 | New app `promotions`: `Promotion` (versioned, issuer is funder with a check constraint, no separate funder field), `Coupon`, `Redemption`, scope, limits, activation modes, audited services with `dry_run`; no order or tax import | P2 | 10 | CAT.1, VEN.2, ORD.2 | A promotion with redemptions cannot be edited in place; a vendor promotion cannot scope another vendor's product; a platform promotion needs a budget cap; migrations and import contract green |
| PRM.2 | Pure `apply_promotions` engine: types percent, fixed, free shipping, gift; one discount per line; fixed-amount sharing with remainder to the largest line; caps and vendor-net clamp; minimum payable total; stacking rules; per-line `ROUND_HALF_UP`; gift rule T-7 (in-kind discount on a paid order only, gift value at most the paid goods) | P2 | 12 | PRM.1, TAX.4 | CPN-VENDOR, CPN-GIFT-T7, CPN-FIXED-SPLIT, CPN-MULTI, CPN-MINPAY, CPN-STACK green; a random-cart property test never exceeds the eligible total or makes a negative vendor net |
| PRM.3 | Cart and checkout integration: apply a code (HTMX, one code slot per funder context), automatic promotions, redemption reserve at `place_order`, confirm in `on_payment_succeeded`, release on cancel and expiry, coupon line in cart and confirmation | P2 | 12 | PRM.2, ORD.4 | CPN-RACE green on PostgreSQL; a cancelled order frees a single-use code; duplicate payment event redeems once |
| PRM.4 | Order snapshots: `OrderLineDiscount`, `shipping_discount_net`, `tax_base_net`, `list_unit_price_net`; non-fiscal confirmation and CMP.23 export fields; VAT base is the discounted price (T-1 bis), list and discounted figures both stored | P2 | 8 | PRM.3, CMP.23 | A coupon order's confirmation and export show list, vendor-funded and platform-funded discount and the discounted VAT base per line; a deleted promotion leaves the snapshot intact |
| PRM.5 | Commission and ledger: `compute_commission` on the list base, `CommissionRule.coupon_base` (`list` default, `paid`), ledger kinds `coupon_vendor_funded`, `coupon_platform_discount`, `commission_coupon_reduction` and `commission_coupon_excess` (Q-90: a platform coupon reduces the commission, the excess is netted monthly and carried over, never paid in cash), statement lines, refund and partial refund rows, DAC7 aggregate adjustment, nightly reconciliation | P2 | 12 | PRM.4, VEN.3, PAY.4, CMP.1 | CPN-VENDOR, CPN-PLATFORM, CPN-PLATFORM-EXCESS, CPN-REFUND-VENDOR and CPN-REFUND-PARTIAL green; statement shows both rows; reconciliation passes; ledger needs human review |
| PRM.6 | Vendor promotions in the Manager shop space: create, pause, end, net-effect preview ("you pay X per use"), usage report; creation validation against commission and fee; activation behind the D-32 level and an audit entry | P2 | 10 | PRM.3, CORE.18 | A vendor creates and ends a coupon; the preview matches the ledger; a percent above 100 − commission − fee is refused |
| PRM.7 | Platform promotions in the platform space: budget caps and alerts, vendor opt-in setting (`CrossVendorConsent.in_platform_coupons`, no row = opted out), 30-day price-history gate, self-dealing exclusion; VAT and funding per the Q-90 model (T-1, answered 2026-11-11) | P2 | 12 | PRM.5, PRM.6 | CPN-GAMING green; a vendor that did not opt in is untouched; the feature flag is off by default; a platform coupon order gives the CPN-PLATFORM rows |
| PRM.8 | Abuse and security of the apply endpoint: rate limits per account, IP and session, Turnstile after 5 failures, lockout, uniform answer, constant-time compare, no codes in logs, registration in the SEC.13 register when merged | P2 | 8 | PRM.3 | CPN-BRUTE green; an endpoint sweep finds the form protected; logs contain no code |
| PRM.9 | Marketing consent and suppression: `MarketingConsent` (purpose, text version, method, timestamp, append-only), checkboxes at signup and checkout, preference toggle (ORD.11), suppression list with salted hashes, one-click unsubscribe (RFC 8058) through CORE.15 links, evidence retention; legal texts through CMP.24 | P2 | 10 | CORE.15, ARC.16, ORD.11, CMP.24 | Consent and withdrawal are recorded; unsubscribe by POST works from the header and the footer; a withdrawn purpose is never sent; texts carry counsel approval |
| PRM.10 | Abandoned-cart detection and reminder (step 1): 15-minute job, eligibility, quiet hours in the business time zone, caps, `RecoveryDispatch`, CORE.15 `cart_restore` link, marketing email class in five languages with no pixel, consent re-check at send, personalised-offer notice | P2 | 12 | PRM.9, ORD.1, ORD.9 | RCV-GUEST, RCV-CONSENT, RCV-QUIET, RCV-CAP, RCV-PIXEL green; snapshot tests of the email in EN, IT, DE, FR and ES; needs human review |
| PRM.11 | Payment-incomplete service message: one transactional email within 24 hours for an `awaiting_payment` order, no coupon, no consent needed | P2 | 4 | ORD.9 | One message per order, none after payment, none for guests without an order |
| PRM.12 | Return incentive: `RecoveryCampaign` for vendor and platform owners, step-2 personal coupon issued at send, auto-apply at restore, campaign choice in a multi-vendor cart, budget stop, D-32 level and cost summary at activation | P2 | 14 | PRM.10, PRM.6 | RCV-WINNER green; a campaign stops at its budget; a coupon works only for its bound account and for the owner's lines; the vendor gets no recipient data |
| PRM.13 | Incentive abuse controls: cooldown from last send, lifetime cap, account age and verified email, post-order exclusion, redemption flags by address hash and payment fingerprint to a review queue, platform report | P2 | 8 | PRM.12 | RCV-COOLDOWN green; a farm pattern is flagged, not blocked silently |
| PRM.14 | Measurement: stable holdout, dispatch log, report with recovery rate, incremental, cannibalised, cost per incremental order and incremental margin; vendor aggregate view with a minimum cell size | P3 | 10 | PRM.12 | RCV-HOLDOUT green; a vendor never sees a cell under 10 users or any individual |
| PRM.15 | Price display and wording gates: coupon never shown as a "was/now" price, public promotion "was" price from the 30-day history, personalised-offer notice, no fake urgency checks in the email and cart templates; tests | P2 | 6 | PRM.3, CMP.6 | A listing with an active coupon shows the vendor price only; a public price cut shows the lowest price of 30 days; templates contain no countdown unless the coupon really expires |
| PRM.16 | Retention: cart deletion after 30 days, `RecoveryDispatch` pseudonymisation at 13 months, consent evidence period, personal-coupon unbinding on erasure, integration with the order scrub jobs (D-29, D-30, D-31) | P2 | 5 | PRM.10, PRM.9 | A dry run lists what a run would delete; account erasure removes the cart and the binding but never a concluded order's snapshot |
| VEN.30 | Vendor terms and checklist for promotions: contract clause on funding, commission on the list price, platform promotions and campaign selection, price-honesty duty (no raising a price to fund a discount), 30 days' notice for changes (P2B), CMP.10 checklist row | P2 | 4 | VEN.14, CMP.24 | New contract version carries the clause as a specific-approval item; checklist row visible; wording approved |
| CMP.32 | Counsel review of the cart-reminder legal basis (L-1 to L-9) and the Omnibus and personalised-price wording; outcome recorded as ADR | P2 | 4 | CMP.24 | Written answers to L-1 to L-9 in an ADR; any change of wording goes through CMP.24 |

## 18.12 Owner questions: decisions and what is left

Decided 2026-11-10 (owner for Q-P2, Q-P6, Q-P7, Q-P9; lane-a technical defaults for the rest):

- **Q-P1.** Decided: no co-funding in v1; a "platform matches the vendor" mode is a later row.
- **Q-P2.** Decided (owner): funding is a debit on the issuer's credit balance, with a ledger kind per
  funder; commission on the pre-coupon total (§18.3). VAT base: the discounted price (T-1 bis); platform
  coupons follow the Q-90 model (T-1, 2026-11-11).
- **Q-P3.** Decided: platform promotions are opt-in by the vendor, default off; a vendor can exclude single products.
- **Q-P4.** Decided as proposed: Finance and payments role, monthly ceiling set by the owner, second approver
  above a threshold, every one audited.
- **Q-P5.** Decided: plain reminder first, incentive 24 hours later, holdout 10%.
- **Q-P6.** Decided (owner): guests included, cookie for the on-site cart, e-mail only with the unticked checkbox (§18.6).
- **Q-P7.** Decided (owner): e-mail and on site, details configurable per campaign (§18.7).
- **Q-P8.** Decided: the proposed defaults, all operator settings.
- **Q-P9.** Decided (owner): only some plans (`VendorPlan.features`); the operator sets the plan matrix.
- **Q-P10.** Decided: yes, the operator-owned shop follows the same rules.
- **Q-P11.** Decided: agreed (no clawback, no coupon restored after dispatch).
- **Q-P12.** Decided: yes, reserved words and a platform-wide unique namespace.

Answered 2026-11-11 (adviser opinion and owner): T-1 to T-8 and counsel L-1 to L-9, recorded in
[92](92-open-decisions.md), where a row wins over this text if they differ. Nothing in this spec is blocked by them any more; T-3 (how the operator
books its own cost) is for the operator's accountant and blocks no row.

## 18.13 Questions for the tax advisor and counsel (answered)

The answers are in [92](92-open-decisions.md); this list keeps the questions as they were asked.

Tax advisor (⚖️):

- **T-1.** Platform-funded discount on a vendor's supply. *Answered (owner and adviser, 2026-11-11):*
  the Q-90 model, the vendor discounts its invoice and the platform reduces its commission; excess
  netted monthly and carried over, no cash, no third-party consideration path.
- **T-1 bis.** VAT base of a vendor coupon. *Answered:* the discounted price.
- **T-2.** Voucher rules, rounding, minimum. *Answered:* a discount-only code is not a voucher (art. 30a
  and 30b); per-line `ROUND_HALF_UP`; minimum 0.50.
- **T-3.** The platform's own marketing cost. *For the operator's accountant;* under Q-90 it is
  commission not earned, shown on the CMP.2 statement.
- **T-4.** Vendor in a flat-rate or special regime. *Answered:* same rule as the others.
- **T-5.** Free shipping funded by the platform. *Answered:* as T-1, shipping 0 or a 100% discount on
  the vendor's invoice, commission reduced by the same amount.
- **T-6.** DAC7 consideration. *Answered:* net of the platform's fees and taxes, fees in `Fees` (CMP.1).
- **T-7.** Gift lines (ORD.12). *Answered:* only as an in-kind discount on a paid order, the gift's
  value at most the paid goods, never a standalone free supply (PRM.2, CPN-GIFT-T7).

Counsel (L-), answered as L-1 to L-9 in [92](92-open-decisions.md); CMP.32 records them in an ADR:

- **L-1.** Is a cart reminder always a commercial communication needing consent (article 13 ePrivacy; article 130 Codice della
  privacy, Garante practice)? Any case where neutral wording is a service message?
- **L-2.** Soft opt-in: could the platform, or a vendor for its own customers, ever rely on it? Who is the controller?
- **L-3.** Consent evidence retention after withdrawal; salted hash on the suppression list.
- **L-4.** Is a coupon generated from cart behaviour a "personalised price" under article 6(1)(ea) of directive 2011/83/EU, and is our notice enough?
- **L-5.** Omnibus: personal coupon outside article 6a; public code announced in marketing inside it; the Italian provision to cite.
- **L-6.** P2B: campaign choice by cart share (article 7 differentiated treatment) and platform coupons across vendors.
- **L-7.** Does the per-send token in the restore link count as tracking under the Garante's provision 284?
- **L-8.** Geo-blocking: may a platform promotion be limited by destination country?
- **L-9.** No clawback on partial withdrawal (directive 2011/83/EU article 14); coupon not restored after dispatch.
