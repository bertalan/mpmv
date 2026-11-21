"""Audit names of the payments app (docs/dev/audit-trail.md)."""

PAYMENT_MODEL = "payments.payment"
PAYMENT_SUCCEEDED = "payments.payment.succeeded"

CONNECTED_ACCOUNT_MODEL = "payments.connectedaccount"
CONNECTED_ACCOUNT_CREATED = "payments.connected_account.created"
CONNECTED_ACCOUNT_UPDATED = "payments.connected_account.updated"

# PAY.4 (#438 review): a payment that succeeded on an order already paid is refunded in full.
PAYMENT_DUPLICATE_FLAGGED = "payments.payment.duplicate_flagged"
PAYMENT_DUPLICATE_REFUNDED = "payments.payment.duplicate_refunded"
PAYMENT_DUPLICATE_REFUND_FAILED = "payments.payment.duplicate_refund_failed"

# PAY.4 (owner decision 2026-11-21): a capture on a cancelled order, or of another amount or
# currency, is not refunded automatically; staff are alerted and support refunds it by hand.
PAYMENT_STRAY_FLAGGED = "payments.payment.stray_flagged"
