"""`on_payment_succeeded` (PAY.2): sent once per confirmed payment, in the webhook transaction.

Receivers run in the same database transaction as the `Payment` row and the idempotency row, so a
receiver that raises rolls both back and the provider's retry runs everything again. Receivers
must therefore be idempotent themselves. The ledger posting (PAY.3, VEN.4) and the order state
change (ORD) connect here; this app imports neither.
"""

from django.dispatch import Signal

# sender: the `Payment` class; kwargs: `payment` (the saved row).
on_payment_succeeded = Signal()

# `on_order_paid` (PAY.3): sent, in the same transaction, right after the payment moved its order
# to `paid` (not when the order was already paid, or the payment did not match it). The ledger
# posts the vendor lines and queues the transfers from here. sender: the `Payment` class; kwargs:
# `payment`. Receivers must be idempotent.
on_order_paid = Signal()
