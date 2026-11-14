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
    PAYMENT_FEE_REVERSAL = engine.PAYMENT_FEE_REVERSAL, "Collection cost reversal"
    TRANSFER_REVERSAL = engine.TRANSFER_REVERSAL, "Transfer reversal"
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


class RefundChannel(models.TextChoices):
    THROUGH_PLATFORM = "through_platform", "Seller refund through the platform"
    OUTSIDE_PLATFORM = "outside_platform", "Seller refund outside the platform (mark only)"
    PLATFORM_INITIATED = "platform_initiated", "Platform-initiated refund"


class Refund(models.Model):
    """One refund event of a vendor order (PAY.4, D-05, D-41): the record the ledger lines and
    the provider refund of that event hang from.

    `through_platform` and `platform_initiated` rows moved money (provider refund, ledger lines);
    their amounts count against the vendor order's gross. `outside_platform` is only the vendor's
    mark with its declared date and method: it moves nothing, the platform is not liable for it
    and the DAC7 consideration is unchanged. Rows are never edited; a mistake is a new event.
    Written by `apps.ledger.refunds` only; the idempotency key is the caller's request id.
    """

    vendor_scope: ClassVar[str] = "vendor"
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="refunds")
    vendor_order = models.ForeignKey(VendorOrder, on_delete=models.PROTECT, related_name="refunds")
    channel = models.CharField(max_length=24, choices=RefundChannel.choices)
    cause = models.CharField(max_length=32, blank=True)  # platform-initiated: the D-41 case
    idempotency_key = models.CharField(max_length=128, unique=True)
    # What was asked: {"mode": "line" | "quantity" | "amount", "lines": [{"line": id, "qty": n}]}
    request = models.JSONField(default=dict)
    refunded_gross = MoneyField()
    commission_reversal = MoneyField(default=0)
    payment_fee_reversal = MoneyField(default=0)
    refund_fee = MoneyField(default=0)
    provider_cost = MoneyField(default=0)
    transfer_reversed = MoneyField(default=0)
    provider_refund_ref = models.CharField(max_length=128, blank=True)
    # L-10: set by platform staff only; the reason is required.
    platform_error = models.BooleanField(default=False)
    platform_error_reason = models.TextField(blank=True)
    # Outside the platform (D-41): the vendor's own statement.
    declared_date = models.DateField(null=True, blank=True)
    declared_method = models.CharField(max_length=64, blank=True)
    reason = models.TextField(blank=True)
    created_by = models.CharField(max_length=128, blank=True)  # the audit actor label

    objects: ClassVar[VendorScopedManager["Refund"]] = VendorScopedManager()

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(refunded_gross__gt=0), name="ledger_refund_gross_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(channel__in=RefundChannel.values),
                name="ledger_refund_channel_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(platform_error=True) | ~models.Q(platform_error_reason=""),
                name="ledger_refund_platform_error_needs_reason",
            ),
            models.CheckConstraint(
                condition=models.Q(channel="outside_platform")
                | (models.Q(declared_date__isnull=True) & models.Q(declared_method="")),
                name="ledger_refund_declared_only_outside",
            ),
            models.CheckConstraint(
                condition=~models.Q(channel="outside_platform")
                | (models.Q(declared_date__isnull=False) & ~models.Q(declared_method="")),
                name="ledger_refund_outside_needs_date_and_method",
            ),
        ]
        indexes = [models.Index(fields=["vendor_order", "id"], name="ledger_refund_order_idx")]

    def __str__(self) -> str:
        return f"{self.channel} {self.refunded_gross} ({self.vendor_order_id})"
