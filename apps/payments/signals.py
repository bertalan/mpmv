"""`on_payment_succeeded` (PAY.2): sent once per confirmed payment, in the webhook transaction.

Receivers run in the same database transaction as the `Payment` row and the idempotency row, so a
receiver that raises rolls both back and the provider's retry runs everything again. Receivers
must therefore be idempotent themselves. The ledger posting (PAY.3, VEN.4) and the order state
change (ORD) connect here; this app imports neither.
"""

from django.dispatch import Signal

# sender: the `Payment` class; kwargs: `payment` (the saved row).
on_payment_succeeded = Signal()
