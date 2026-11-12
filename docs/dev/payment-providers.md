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
refuses an empty one on LIVE and STAGE). Webhook: `POST /webhooks/payments/`, CSRF-exempt, signature
and 300 s timestamp tolerance checked first, event ids stored in `ProcessedEvent` (a replay does
nothing), then the `on_payment_succeeded` signal (`apps/payments/signals.py`) inside the same
transaction. Ledger posting (PAY.3, needs VEN.4) and the order state change connect to that signal.
