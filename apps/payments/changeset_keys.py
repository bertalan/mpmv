"""Audit names of the payments app (docs/dev/audit-trail.md)."""

PAYMENT_MODEL = "payments.payment"
PAYMENT_SUCCEEDED = "payments.payment.succeeded"

CONNECTED_ACCOUNT_MODEL = "payments.connectedaccount"
CONNECTED_ACCOUNT_CREATED = "payments.connected_account.created"
CONNECTED_ACCOUNT_UPDATED = "payments.connected_account.updated"
VENDOR_MODEL = "vendors.vendor"
VENDOR_SALES_MODE_CHANGED = "payments.vendor_sales_mode.changed"
