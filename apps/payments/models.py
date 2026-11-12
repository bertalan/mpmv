"""Payments storage (PAY.2): the webhook events already handled and the payments they confirm.

Neither table is vendor-owned: a payment belongs to an order (kept as a reference string, the
payments app never imports `orders`) and its vendor shares are the ledger's business (PAY.3).
"""

from typing import ClassVar

from django.db import models

from apps.core.fields import MoneyField
from apps.core.models import BaseModel
from apps.vendors.models import Vendor
from apps.vendors.scoping import VendorScopedManager

from . import stripe_engine


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


class ConnectedAccount(BaseModel):
    """A vendor's connected payment account (VEN.6): its id at the provider and where onboarding
    stands. Owned by the vendor (tenant scoped); the id never leaves the payments services and the
    vendor's own Manager page. The status is a mirror of what the provider reported."""

    vendor_scope: ClassVar[str] = "vendor"
    objects: ClassVar[VendorScopedManager["ConnectedAccount"]] = VendorScopedManager()

    vendor = models.OneToOneField(
        Vendor, on_delete=models.PROTECT, related_name="connected_account"
    )
    provider = models.CharField(max_length=32)
    account_ref = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16,
        choices=[
            (stripe_engine.PENDING, "Pending"),
            (stripe_engine.RESTRICTED, "Restricted"),
            (stripe_engine.ACTIVE, "Active"),
        ],
        default=stripe_engine.PENDING,
    )
    details_submitted = models.BooleanField(default=False)
    charges_enabled = models.BooleanField(default=False)
    payouts_enabled = models.BooleanField(default=False)
    requirements_due = models.JSONField(default=list, blank=True)
    disabled_reason = models.CharField(max_length=128, blank=True)
    # True while the vendor sells on request only because it has no active account (D-19): the
    # automatic switch back to sale applies only then, never over a vendor's own choice.
    on_request_forced = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "account_ref"], name="payments_connectedaccount_once"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["pending", "restricted", "active"]),
                name="payments_connectedaccount_status_valid",
            ),
        ]

    @property
    def is_active(self) -> bool:
        return self.status == stripe_engine.ACTIVE

    def __str__(self) -> str:
        return f"{self.provider}:{self.account_ref}"
