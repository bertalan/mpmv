"""Orders, vendor sub-orders and order lines (spec 07 §7.2, ORD.2).

An order is a set of vendor sub-orders (one per vendor), each with its own lines, totals and
state. Everything the order says is a snapshot taken when it was placed: prices, tax, the
shipping choice, the commission rule and the vendor's tax profile. Nothing here follows a live
catalogue, tax or shipping row, so a later change never rewrites history. The only links to live
rows are the product on a line (set to null if the product is deleted) and the vendor.

States change only along the arrows of `apps.orders.engine`: saving an illegal change raises
`IllegalTransition`, and the database refuses any state outside the machine. Create and change
orders through `apps.orders.services`, which number them, compute the totals and audit.

The number is an order reference (`2027-000123`), never an invoice number: the vendor invoices
(spec 00).
"""

from typing import Any, ClassVar

from django.conf import settings
from django.db import models

from apps.catalog.models import Product
from apps.core.fields import CountryField, LanguageField, MoneyField
from apps.core.models import BaseModel
from apps.vendors.models import Vendor
from apps.vendors.scoping import VendorScopedManager

from . import engine


class OrderState(models.TextChoices):
    CHECKOUT = "checkout"
    QUOTE_FLOW = "quote_flow"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    IN_FULFILLMENT = "in_fulfillment"
    FULFILLED = "fulfilled"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"


class VendorOrderState(models.TextChoices):
    PENDING = "pending"
    ACCEPTED = "accepted"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class CustomerTaxStatus(models.TextChoices):
    B2C = "b2c", "Consumer"
    B2B = "b2b", "Business"


class OrderNumberSequence(models.Model):
    """The last sequence number issued in a year. Locked while a number is taken, and the
    increment commits or rolls back with the order, so numbers have no gaps."""

    year = models.PositiveSmallIntegerField(unique=True)
    last_value = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.year}: {self.last_value}"


class _StateGuarded(BaseModel):
    """Refuses to save a state change that the machine has no arrow for."""

    _machine: ClassVar[Any]  # engine.check_*_transition
    _stored_state: str | None = None

    class Meta:
        abstract = True

    @classmethod
    def from_db(cls, db: str | None, field_names: Any, values: Any, **kwargs: Any) -> Any:
        instance = super().from_db(db, field_names, values, **kwargs)
        instance._stored_state = instance.__dict__.get("state")  # None if deferred
        return instance

    def refresh_from_db(self, *args: Any, **kwargs: Any) -> None:
        super().refresh_from_db(*args, **kwargs)
        self._stored_state = self.__dict__.get("state")

    def save(self, *args: Any, **kwargs: Any) -> None:
        update_fields = kwargs.get("update_fields")
        state: str = self.state  # type: ignore[attr-defined]
        if (
            (update_fields is None or "state" in update_fields)
            and self._stored_state is not None
            and state != self._stored_state
        ):
            type(self)._machine(self._stored_state, state)
        super().save(*args, **kwargs)
        self._stored_state = state


class Order(_StateGuarded):
    _machine = staticmethod(engine.check_order_transition)

    number = models.CharField(
        max_length=32, unique=True, editable=False, help_text="Order reference, not an invoice."
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        help_text="Empty for a guest order.",
    )
    email = models.EmailField()
    language = LanguageField(default=settings.LANGUAGE_CODE)
    billing_address = models.JSONField(default=dict)
    billing_country = CountryField()
    shipping_address = models.JSONField(default=dict)
    shipping_country = CountryField()
    customer_tax_status = models.CharField(
        max_length=3, choices=CustomerTaxStatus.choices, default=CustomerTaxStatus.B2C
    )
    vat_validation = models.JSONField(
        default=dict, blank=True, help_text="Snapshot of the VAT number check, if any."
    )
    state = models.CharField(
        max_length=24, choices=OrderState.choices, default=OrderState.CHECKOUT, db_index=True
    )
    goods_net = MoneyField()
    shipping_net = MoneyField()
    tax_total = MoneyField()
    grand_total = MoneyField()
    payment_intent_ref = models.CharField(max_length=255, blank=True)
    transfer_group = models.CharField(max_length=64, blank=True)
    legacy_wp_order_id = models.PositiveBigIntegerField(null=True, blank=True, unique=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=OrderState.values),
                name="orders_order_state_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    grand_total=models.F("goods_net")
                    + models.F("shipping_net")
                    + models.F("tax_total")
                ),
                name="orders_order_grand_total_adds_up",
            ),
        ]

    def __str__(self) -> str:
        return f"Order {self.number}"


class VendorOrder(_StateGuarded):
    vendor_scope: ClassVar[str] = "vendor"
    objects: ClassVar[VendorScopedManager["VendorOrder"]] = VendorScopedManager()
    _machine = staticmethod(engine.check_vendor_order_transition)

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="vendor_orders")
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="vendor_orders")
    state = models.CharField(
        max_length=16,
        choices=VendorOrderState.choices,
        default=VendorOrderState.PENDING,
        db_index=True,
    )
    goods_net = MoneyField()
    shipping_net = MoneyField()
    tax_total = MoneyField()
    gross_total = MoneyField()
    shipping_choice = models.JSONField(
        default=dict, blank=True, help_text="Snapshot: carrier, zone, rate or quote."
    )
    commission_snapshot = models.JSONField(
        default=dict, blank=True, help_text="Snapshot of compute_commission: rule, percentage, fee."
    )
    tax_profile_snapshot = models.JSONField(default=dict, blank=True)
    tracking_number = models.CharField(max_length=100, blank=True)
    carrier = models.CharField(max_length=100, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["order", "vendor"], name="orders_vendororder_unique"),
            models.CheckConstraint(
                condition=models.Q(state__in=VendorOrderState.values),
                name="orders_vendororder_state_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    gross_total=models.F("goods_net")
                    + models.F("shipping_net")
                    + models.F("tax_total")
                ),
                name="orders_vendororder_gross_total_adds_up",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.order.number} / vendor {self.vendor_id}"


class OrderLine(BaseModel):
    """An immutable snapshot of what was bought, at which price and tax."""

    vendor_scope: ClassVar[str] = "vendor_order__vendor"
    objects: ClassVar[VendorScopedManager["OrderLine"]] = VendorScopedManager()

    vendor_order = models.ForeignKey(VendorOrder, on_delete=models.PROTECT, related_name="lines")
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="order_lines"
    )
    sku = models.CharField(
        max_length=64, help_text="Display snapshot of the vendor's label, never a reference."
    )
    title = models.CharField(max_length=300)
    variant_label = models.CharField(max_length=300, blank=True)
    unit_price_net = MoneyField()
    quantity = models.PositiveIntegerField()
    discount_net = MoneyField(default=0)
    net_total = MoneyField(help_text="Unit price x quantity - discount, rounded to cents.")
    tax_rate = models.DecimalField(max_digits=6, decimal_places=3)
    tax_reason = models.CharField(max_length=64, blank=True)
    tax_amount = MoneyField()
    legal_note_key = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["vendor_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1), name="orders_orderline_quantity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(discount_net__gte=0),
                name="orders_orderline_discount_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(tax_rate__gte=0), name="orders_orderline_tax_rate_not_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.title}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise PermissionError("An order line is an immutable snapshot")
        super().save(*args, **kwargs)
