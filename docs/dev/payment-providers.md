# How to add a payment method (PAY.5)

Owner decision D-09: Stripe Connect is the only payment provider at launch (PAY.1), split at
source. Any other method comes later, behind the `PaymentProvider` interface described here.
No other method is built or offered at checkout until a provider passes the checklist below.

Code: `apps/payments/engine.py` (interface and pure types), `registry.py`, `checks.py`,
`fake.py` (in-memory provider), `tests/provider_contract.py` (contract tests).

## What a provider must support

A provider implements all four capabilities. `capability_problems` checks them, and the registry
refuses a provider that misses one, when it is registered.

1. **Split at source, or a deferred collection of the platform fee.** `fee_collection` is
   `FeeCollection.SPLIT_AT_SOURCE` (the provider separates the platform fee from each vendor's
   payout, one transfer per vendor, tied to the charge; PAY.1, PAY.3) or `DEFERRED_RECEIVABLE`
   (the fee becomes a ledger receivable netted from the vendor's next payout; PAY.6, spec only).
   `create_payment(PaymentRequest)` is idempotent on `idempotency_key`; shares plus platform fee
   equal the amount.
2. **Refunds.** `refund(RefundRequest)` handles full and partial refunds, reverses the vendors'
   shares (`reverse_vendor_shares`), is idempotent, and returns the real provider cost of the
   refund from the provider's balance transaction in `provider_cost`, which PAY.4 needs for the
   refund fee.
3. **Payout reconciliation.** `list_payouts(since)` returns `PayoutRecord` lines (vendor account,
   amount, payment reference) that the ledger reconciliation (PAY.3, VEN.8) compares with the
   ledger.
4. **Signed, idempotent webhooks.** `parse_webhook(body, signature)` verifies the signature
   against the raw body and raises `InvalidSignature` on a mismatch. The `event_id` it returns
   is stable across redeliveries, so the `ProcessedEvent` table (PAY.2) drops a duplicate.

Also required: `code` (lowercase, unique), `supported_methods()` (checkout offers only these) and
`collects_on_operator_account = False`. Customer money is never collected on an operator bank
account (D-08); a provider that does is refused at registration.

Money is `Decimal`, never a float. The provider never rounds; the caller does, `ROUND_HALF_UP`
per line.

## Steps

1. Create the adapter module in `apps/payments/` (for example `apps/payments/<name>.py`) with a
   class implementing `PaymentProvider`. No credentials in code or tests: keys come from the
   environment, settings and the secrets file, never literals.
2. Register it with `registry.register_provider(MyProvider)` from `PaymentsConfig.ready()`.
3. Select it per environment with `MPMV_PAYMENT_PROVIDER=<code>`. `manage.py check` fails with
   `payments.E001` when the code is not registered. The fake provider (`fake`) exists only where
   `PAYMENT_ALLOW_FAKE_PROVIDER` is true (the test settings) and fails `payments.E002` where
   `LIVE_DATA` is on.
4. Subclass `PaymentProviderContract` from `apps/payments/tests/provider_contract.py` in the provider's test
   module and implement `make_provider` and `signed_webhook`. Every check must pass. Run the
   provider against its own sandbox double behind the interface; mock only through our interface.

## Contract-test checklist

- Declares every capability and `collects_on_operator_account` is false.
- Fee collection is split or deferred.
- Reports its supported methods and refuses an unsupported one.
- A payment gives one transfer per vendor; the same idempotency key returns the same result.
- `transfer(TransferRequest)` (PAY.3) pays one vendor its share after the payment settled, tied to
  the charge; it is idempotent on the key and never makes a second transfer for the same payment and
  account (a retry after the provider's key window must find the first one).
- An unbalanced payment (shares plus fee differ from the amount) is refused.
- A refund reports the provider cost, reverses the shares, is idempotent, and cannot exceed the
  payment.
- Payouts are listed by date for reconciliation.
- A webhook with a bad signature or a tampered body is refused; the event id is stable on
  redelivery.

## Review steps before a provider ships

- D-08 PSD2 check: confirm with the adviser that the flow stays outside a payment-institution
  licence (customer pays the vendor directly, or funds stay at the provider with automatic
  transfers). A non-split method needs PAY.6 and its own legal sign-off.
- CMP.5: the customer and vendor terms name the provider and the discharge and set-off clauses.
- Human review of the PR (money, payments, ledger), regression tests from the spec, and the
  tenancy and audit registries updated if the adapter adds models.

## The Stripe provider (PAY.1, PAY.2)

`apps/payments/stripe_provider.py` is the only registered provider. Stripe Connect, separate
charges and transfers: one PaymentIntent per order with `transfer_group` = the order reference,
then one transfer per vendor with `source_transaction` = the order's charge. The platform fee is
what stays on the platform's Stripe balance. Everything goes through the `StripeClient` interface
(`stripe_client.py`); tests use `apps/payments/tests/fake_stripe.py` and never touch the network.
Stripe has two endpoint kinds, each with its own signing secret, both pointing at the same URL
`/webhooks/payments/`: the platform endpoint (`payment_intent.*`, secret `MPMV_STRIPE_WEBHOOK_SECRET`)
and the "Connected accounts" endpoint (`account.updated`, optional secret
`MPMV_STRIPE_CONNECT_WEBHOOK_SECRET`). An event is accepted if either secret verifies; an empty
Connect secret is ignored.
Keys: `MPMV_STRIPE_SECRET_KEY`, `MPMV_STRIPE_WEBHOOK_SECRET` (empty by default; `payments.E003`
refuses an empty one on LIVE and STAGE; `payments.E004` refuses LIVE while `PAYMENTS_TRANSFERS_WIRED` is False, which PAY.3 turns on). Webhook: `POST /webhooks/payments/`, CSRF-exempt, signature
and 300 s timestamp tolerance checked first, event ids stored in `ProcessedEvent` (a replay does
nothing), then the `on_payment_succeeded` signal (`apps/payments/signals.py`) inside the same
transaction. The order state change is connected (`order_hook.py`, `mark_order_paid`, PAY.2); ledger posting (PAY.3, needs VEN.4) connects to the same signal.

### Stripe idempotency keys and cached errors

Stripe stores the response to a request, an error response included, for an idempotency key for
24 hours (it does not store requests that fail validation before they start or that collide with a
request still in flight). Two rules follow:

- A retry with the same key after a failed request returns the same stored error, not a fresh
  attempt. It does not help to retry quickly with an unchanged request.
- A key must never be reused for a request with different parameters (Stripe answers HTTP 400
  `idempotency_error`). Every key names what identifies its request, the parameters that may
  change included:
  - PAY.3 settlement sweep (`apps/ledger/settlement.py`, built by
    `stripe_engine.transfer_idempotency_key`):
    `transfer:<order>:<vendor>:<destination>:<amount in minor units>:<CURRENCY>`. A key longer
    than Stripe's 255 characters keeps `transfer:<order>:<vendor>:` and replaces the rest with a
    32-character SHA-256 prefix.
  - Refund reversals: `<refund key>:reverse:<transfer>`, amount-free on purpose (the transfer id
    names the destination). The same refund and transfer always get the same key, so a retry with
    a changed amount fails closed with `idempotency_error` instead of becoming a second reversal.
  - The order-derived PaymentIntent key and its `<key>:transfer:<n>` shares, made once at
    checkout.

Incident (STAGE, 2026-11-14): the transfer key used to be `transfer:<order>:<vendor>`. The sweep
first sent a transfer to a demo payout account, Stripe refused it and stored the answer; after
the vendor was linked to a real connected account every retry got `idempotency_error` for 24
hours. With the destination and the amount in the key, a relinked vendor gets a fresh key at once.

How the code behaves: the sweep (`transfer_payment`, `settle_pending`) retries a vendor transfer
by calling `StripeProvider.transfer`, which first lists the transfers of the order's transfer
group:

- a transfer of the same payment to the same destination is returned, never repeated (an amount
  mismatch is a `TransferConflict`). That lookup makes the retry safe after the 24 hours, and
  after a transfer that was created but not recorded;
- a transfer of the same payment for the same vendor order (`metadata[vendor_order]`) to another
  destination is a `TransferConflict`: the vendor was relinked after the money left. Nothing new
  is created; the sweep audits `ledger.transfer_blocked` once with the existing transfer id and
  staff decide.

A demo payout account (`acct_demo_<n>`, `connect.record_demo_account`, STAGE seed only) never
reaches Stripe: the sweep skips it before any provider call and audits `ledger.transfer_blocked`
with reason `demo_account` once (the vendor balance stays the receivable), and
`StripeProvider.transfer` refuses it too before calling the client.

Remaining limitation: if Stripe stored an error for an unchanged request (for example a temporary
balance problem that was later fixed), every sweep inside the next 24 hours gets the same stored
error back; each failure is audited as `ledger.transfer_failed` and the first run after the window
sends a fresh request. There is no key rotation on error. Errors that Stripe does not store
(validation failures, a concurrent duplicate) are not affected.

Error reporting: `StripeError` keeps Stripe's `error.type`, `error.code` and `decline_code`
(plain identifiers only) and the HTTP status, and its message is built from them, e.g.
`Stripe 400 idempotency_error on /transfers`; that text is the `ledger.transfer_failed` reason.
Stripe's own message text, the request body and the idempotency key are never logged: the
message can echo request parameters.
