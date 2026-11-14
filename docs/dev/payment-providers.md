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
- A key must never be reused for a request with different parameters (Stripe answers with an
  idempotency error). Our keys are derived from what identifies the request: the order-derived
  PaymentIntent key, `<key>:transfer:<n>`, `<key>:reverse:<transfer>` and, in the PAY.3
  settlement sweep (`apps/ledger/settlement.py`), `transfer:<order>:<vendor>`. If the amount or
  the destination of a payment can change, the key has to change with it.

How the code behaves: the sweep (`transfer_payment`, `settle_pending`) retries a vendor transfer
by calling `StripeProvider.transfer`, which first lists the transfers of the order's transfer
group and returns an existing one for the same payment and destination (an amount mismatch is an
error). That lookup is what makes the retry safe after the 24 hours, and after a transfer that was
created but not recorded. It does not help after a failed create: no transfer exists, so the
sweep sends the create again with the same key.

Known limitation: if Stripe stored an error for `transfer:<order>:<vendor>` (for example a
temporary balance or account problem that was later fixed), every sweep inside the next 24 hours
gets the same stored error back and the transfer stays pending; the sweep audits the failure once
and retries on its next run, and the first run after the window expires sends a fresh request.
There is no key rotation on error. Errors that Stripe does not store (validation failures, a
concurrent duplicate) are not affected.
