"""The append-only vendor ledger (VEN.4, spec 04)."""

import uuid
from typing import Any, ClassVar

from django.db import models

from apps.core.fields import MoneyField
from apps.core.models import PlatformOperator
from apps.orders.models import VendorOrder
from apps.vendors.models import Vendor
from apps.vendors.scoping import VendorScopedManager

from . import engine


class LedgerKind(models.TextChoices):
    SALE = engine.SALE, "Sale"
    SHIPPING = engine.SHIPPING, "Shipping"
    COMMISSION = engine.COMMISSION, "Commission"
    PAYMENT_FEE = engine.PAYMENT_FEE, "Payment fee"
    REFUND = engine.REFUND, "Refund"
    COMMISSION_REVERSAL = engine.COMMISSION_REVERSAL, "Commission reversal"
    REFUND_FEE = engine.REFUND_FEE, "Refund fee"
    ADJUSTMENT = engine.ADJUSTMENT, "Adjustment"
    PAYOUT = engine.PAYOUT, "Payout"
    TRANSFER = engine.TRANSFER, "Transfer"
    PLAN_FEE = engine.PLAN_FEE, "Plan fee"
    BUNDLE_DISCOUNT_VENDOR_FUNDED = (
        engine.BUNDLE_DISCOUNT_VENDOR_FUNDED,
        "Bundle discount funded by the vendor",
    )
    BUNDLE_DISCOUNT_PLATFORM_FUNDED = (
        engine.BUNDLE_DISCOUNT_PLATFORM_FUNDED,
        "Bundle discount funded by the platform",
    )


class LedgerEntry(models.Model):
    """One signed movement of a vendor's balance: positive credits the vendor, negative debits it.

    Append-only: the database refuses UPDATE and DELETE (trigger, migration 0002) and `save` and
    `delete` refuse them too. A mistake is corrected by a new `adjustment` entry, never an edit.
    `balance_after` is the vendor's running balance including this entry; the database computes it
    on insert under a per-vendor lock, so it is right whatever wrote the row. Written by
    `services.record_entry`. The ledger is an operational record, not accounting (spec 04).
    """

    vendor_scope: ClassVar[str] = "vendor"
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="ledger_entries")
    vendor_order = models.ForeignKey(
        VendorOrder, null=True, blank=True, on_delete=models.PROTECT, related_name="ledger_entries"
    )
    kind = models.CharField(max_length=32, choices=LedgerKind.choices)
    amount = MoneyField()
    currency = models.CharField(max_length=3, default=engine.CURRENCY)
    balance_after = MoneyField(editable=False, default=0)
    platform_entity = models.ForeignKey(
        PlatformOperator, on_delete=models.PROTECT, related_name="ledger_entries"
    )
    idempotency_key = models.CharField(max_length=128, unique=True)
    # Plain references, no foreign keys: the refund or order snapshot id the entry comes from,
    # and the provider's id (tr_, po_, re_) with the account that moved the money.
    source_ref = models.CharField(max_length=128, blank=True)
    external_ref = models.CharField(max_length=128, blank=True)
    gateway_account = models.CharField(max_length=128, blank=True)
    created_by = models.CharField(max_length=128, blank=True)  # the audit actor label
    memo = models.TextField(blank=True)

    objects = VendorScopedManager()

    class Meta:
        ordering = ["id"]
        verbose_name_plural = "ledger entries"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(amount=0), name="ledger_entry_amount_not_zero"
            ),
        ]
        indexes = [models.Index(fields=["vendor", "id"], name="ledger_entry_vendor_idx")]

    def __str__(self) -> str:
        return f"{self.kind} {self.amount} {self.currency} ({self.vendor_id})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise PermissionError("LedgerEntry is append-only")
        super().save(*args, **kwargs)
        self.refresh_from_db(fields=["balance_after"])  # set by the insert trigger

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise PermissionError("LedgerEntry is append-only")
