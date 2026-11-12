"""Payments storage (PAY.2): the webhook events already handled and the payments they confirm.

Neither table is vendor-owned: a payment belongs to an order (kept as a reference string, the
payments app never imports `orders`) and its vendor shares are the ledger's business (PAY.3).
"""

from django.db import models

from apps.core.fields import MoneyField
from apps.core.models import BaseModel


class ProcessedEvent(BaseModel):
    """A provider webhook event that was handled. A redelivery finds its row and does nothing."""

    provider = models.CharField(max_length=32)
    event_id = models.CharField(max_length=255)
    event_type = models.CharField(max_length=128)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "event_id"], name="payments_processedevent_once"
            )
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.event_id}"


class Payment(BaseModel):
    """A customer payment the provider confirmed. One row per provider payment reference."""

    provider = models.CharField(max_length=32)
    provider_ref = models.CharField(max_length=255)
    charge_ref = models.CharField(max_length=255, blank=True)
    order_ref = models.CharField(max_length=255, blank=True)
    amount = MoneyField()
    currency = models.CharField(max_length=3)
    succeeded_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_ref"], name="payments_payment_once"
            )
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.provider_ref}"
