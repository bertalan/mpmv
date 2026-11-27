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
from django.db.models.functions import Round
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product
from apps.core.fields import CountryField, LanguageField, MoneyField
from apps.core.i18n import primary_language
from apps.core.models import BaseModel
from apps.vendors.models import Vendor
from apps.vendors.scoping import VendorScopedManager

from . import engine
from .tz import business_tz


class OrderState(models.TextChoices):
    CHECKOUT = "checkout", _("Checkout")
    QUOTE_FLOW = "quote_flow", _("Quote in progress")
    AWAITING_PAYMENT = "awaiting_payment", _("Waiting for payment")
    PAID = "paid", _("Paid")
    IN_FULFILLMENT = "in_fulfillment", _("Being prepared")
    FULFILLED = "fulfilled", _("Delivered")
    CLOSED = "closed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")
    PARTIALLY_REFUNDED = "partially_refunded", _("Partly refunded")
    REFUNDED = "refunded", _("Refunded")


class VendorOrderState(models.TextChoices):
    PENDING = "pending", _("Pending")
    ACCEPTED = "accepted", _("Accepted")
    SHIPPED = "shipped", _("Shipped")
    DELIVERED = "delivered", _("Delivered")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")
    REFUNDED = "refunded", _("Refunded")


class PrivacyStatus(models.TextChoices):
    ACTIVE = "active"
    RESTRICTED_TAX_ONLY = "restricted_tax_only"
    ANONYMIZED = "anonymized"


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
    _initial_states: ClassVar[frozenset[str]]
    _stored_state: str | None = None

    class Meta:
        abstract = True

    @classmethod
    def from_db(cls, db: str | None, field_names: Any, values: Any, **kwargs: Any) -> Any:
        instance = super().from_db(db, field_names, values, **kwargs)
        instance._stored_state = instance.__dict__.get("state")  # None if deferred
        return instance

    def _is_legacy(self) -> bool:
        return getattr(self, "legacy_wp_order_id", None) is not None

    def refresh_from_db(self, *args: Any, **kwargs: Any) -> None:
        super().refresh_from_db(*args, **kwargs)
        self._stored_state = self.__dict__.get("state")

    def save(self, *args: Any, **kwargs: Any) -> None:
        update_fields = kwargs.get("update_fields")
        state: str = self.state  # type: ignore[attr-defined]
        if (
            self._state.adding
            and state not in self._initial_states
            and not self._is_legacy()  # MIG.4: a historical order keeps its final state
        ):
            raise engine.IllegalTransition(f"{state!r} is not an initial state")
        if (
            (update_fields is None or "state" in update_fields)
            and self._stored_state is not None
            and state != self._stored_state
        ):
            type(self)._machine(self._stored_state, state)
        super().save(*args, **kwargs)
        self._stored_state = state


class TermsAcceptanceLocked(Exception):
    """A save tried to change `Order.terms_acceptance` after it was set (ORD.17b)."""


class Order(_StateGuarded):
    _machine = staticmethod(engine.check_order_transition)
    _initial_states = engine.ORDER_INITIAL_STATES

    # The retention clock (D-29) and the numbering year count from this date. It is the time of
    # insert, except for a legacy order (MIG.4), which keeps its original date; the database
    # trigger enforces the same rule.
    created_at = models.DateTimeField(default=timezone.now, editable=False)

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
    # D-29: the fiscal snapshot. These columns (with the addresses, countries and
    # `vat_validation`) are immutable in the database; only the audited scrub services in
    # `apps.orders.services` may rewrite them, after the legal retention periods.
    customer_name = models.CharField(
        max_length=200, blank=True, help_text="Name or company, as on the order."
    )
    customer_vat_number = models.CharField(max_length=32, blank=True)
    customer_tax_id = models.CharField(max_length=32, blank=True)
    email = models.EmailField()
    language = LanguageField(default=primary_language)
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
    vehicle_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text=("FIT.17: the selected vehicle (make, model, year labels)."),
    )
    terms_acceptance = models.JSONField(
        default=dict,
        blank=True,
        editable=False,
        help_text=(
            "ORD.17: what the buyer accepted at checkout: the customer terms version (id, number, "
            "hash), one terms version per seller (id, number, hash), box 1, and box 2 with its "
            "text when it was shown."
        ),
    )
    terms_accepted_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text="ORD.17: when the buyer accepted the terms at checkout.",
    )
    state = models.CharField(
        max_length=24, choices=OrderState.choices, default=OrderState.CHECKOUT, db_index=True
    )
    goods_net = MoneyField()
    shipping_net = MoneyField()
    tax_total = MoneyField()
    grand_total = MoneyField()
    privacy_status = models.CharField(
        max_length=24,
        choices=PrivacyStatus.choices,
        default=PrivacyStatus.ACTIVE,
        editable=False,
        db_index=True,
        help_text="Forward only (D-30): changed by the scrub services, enforced by a trigger.",
    )
    tax_retention_until = models.DateField(
        editable=False,
        help_text="31 December of the order year + 10, in the business time zone; immutable.",
    )
    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text="Set by the trigger when the order first becomes paid; immutable. A legacy "
        "loader supplies it on insert. Decides whether an order was ever paid.",
    )
    restricted_at = models.DateTimeField(
        null=True, blank=True, editable=False, help_text="When it became restricted_tax_only."
    )
    anonymised_at = models.DateTimeField(
        null=True, blank=True, editable=False, help_text="When the personal fields were replaced."
    )
    payment_intent_ref = models.CharField(max_length=255, blank=True)
    # Not stored: set by `place_order` for the one request that confirms the payment in the browser.
    payment_client_secret: str = ""
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
                condition=models.Q(privacy_status__in=PrivacyStatus.values),
                name="orders_order_privacy_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    grand_total=models.F("goods_net")
                    + models.F("shipping_net")
                    + models.F("tax_total")
                ),
                name="orders_order_grand_total_adds_up",
            ),
            models.CheckConstraint(
                condition=models.Q(goods_net__gte=0), name="orders_order_goods_net_not_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(shipping_net__gte=0),
                name="orders_order_shipping_net_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(tax_total__gte=0), name="orders_order_tax_total_not_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(grand_total__gte=0), name="orders_order_grand_total_not_negative"
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        adding = self._state.adding
        fresh = adding and self.legacy_wp_order_id is None
        if fresh:
            self.created_at = timezone.now()
        update_fields = kwargs.get("update_fields")
        if not adding and (update_fields is None or "terms_acceptance" in update_fields):
            stored = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("terms_acceptance", flat=True)
                .first()
            )
            if stored and stored != self.terms_acceptance:
                raise TermsAcceptanceLocked(
                    "ORD.17: what the buyer accepted at checkout is never changed afterwards."
                )
        if adding:
            self.tax_retention_until = engine.tax_retention_until(
                self.created_at, business_tz(self.created_at)
            )
        super().save(*args, **kwargs)
        if fresh:  # the trigger stamped the database's own times: read them back
            self.created_at, self.tax_retention_until = Order.objects.values_list(
                "created_at", "tax_retention_until"
            ).get(pk=self.pk)

    def __str__(self) -> str:
        return f"Order {self.number}"


class VendorOrder(_StateGuarded):
    """One vendor's part of an order.

    Privacy: `order` leads to the buyer's e-mail and addresses. Anything that shows a
    vendor-scoped sub-order to a vendor (views, API serialisers, exports, MCP) must pick its
    fields explicitly and never serialise `order` as a whole.
    """

    vendor_scope: ClassVar[str] = "vendor"
    objects: ClassVar[VendorScopedManager["VendorOrder"]] = VendorScopedManager()
    _machine = staticmethod(engine.check_vendor_order_transition)
    _initial_states = engine.VENDOR_ORDER_INITIAL_STATES

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
    # ORD.29: NULL rate and reason = the shipping is split across several VAT rates (it follows
    # the goods, TAX.17); the portions in `shipping_choice["tax_portions"]` and in v1 are then the
    # only source of truth, and `shipping_tax_amount` is their sum. Never group VAT by this rate.
    shipping_tax_rate = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=0,
        null=True,
        blank=True,
        help_text="Snapshot: VAT rate on the shipping; empty when split across rates (portions).",
    )
    shipping_tax_amount = MoneyField(
        default=0, help_text="Snapshot: VAT on the shipping (the sum of its portions)."
    )
    shipping_tax_reason = models.CharField(  # noqa: DJ001 (NULL = mixed, spec 07 ORD.29)
        max_length=64, blank=True, null=True
    )
    shipping_vat_nature_code = models.CharField(
        max_length=16, blank=True, help_text="N-code when the shipping is not taxed (TAX.3)."
    )
    vendor_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="The vendor's name, VAT number and country when ordered.",
    )
    shipping_choice = models.JSONField(
        default=dict, blank=True, help_text="Snapshot: carrier, zone, rate or quote."
    )
    commission_snapshot = models.JSONField(
        default=dict, blank=True, help_text="Snapshot of compute_commission: rule, percentage, fee."
    )
    tax_profile_snapshot = models.JSONField(default=dict, blank=True)
    # Non-fiscal: dropped by `restrict_order` when the order becomes restricted_tax_only (D-30).
    carrier_contact_phone = models.CharField(max_length=32, blank=True)
    support_notes = models.TextField(blank=True)
    tracking_number = models.CharField(max_length=100, blank=True)
    carrier = models.CharField(max_length=100, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    current_version = models.OneToOneField(
        "VendorOrderVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="ORD.20: the last applied version (v1 is the placed order).",
    )
    # ORD.20 (spec 07, minor change 2): after a partial unavailability the customer may cancel
    # the rest of the package free of charge before shipping, and the package cannot be shipped
    # until the hold ends, unless the customer answered earlier.
    cancel_rest_offered_at = models.DateTimeField(
        null=True, blank=True, help_text="When the cancel-the-rest offer was made."
    )
    ship_hold_until = models.DateTimeField(
        null=True, blank=True, help_text="Shipping is refused until then (cancel-the-rest hold)."
    )
    # ORD.20 part 3b (spec 07, Invoicing): the vendor may record that it already issued its
    # invoice or receipt; every applied change then shows the invoice notice prominently. It never
    # blocks a change: correcting the fiscal document is the vendor's job.
    invoice_issued_at = models.DateTimeField(
        null=True, blank=True, help_text="When the vendor says it issued its invoice or receipt."
    )

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
            models.CheckConstraint(
                condition=models.Q(goods_net__gte=0),
                name="orders_vendororder_goods_net_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(shipping_net__gte=0),
                name="orders_vendororder_shipping_net_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(tax_total__gte=0),
                name="orders_vendororder_tax_total_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(gross_total__gte=0),
                name="orders_vendororder_gross_total_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(shipping_tax_amount__gte=0),
                name="orders_vendororder_shipping_tax_amount_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(shipping_tax_rate__gte=0),
                name="orders_vendororder_shipping_tax_rate_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(shipping_tax_rate__isnull=True)
                | models.Q(
                    shipping_tax_amount=Round(
                        models.F("shipping_net") * models.F("shipping_tax_rate") / 100, 2
                    )
                ),
                name="orders_vendororder_shipping_tax_consistent",
            ),
            models.CheckConstraint(
                condition=models.Q(tax_total__gte=models.F("shipping_tax_amount")),
                name="orders_vendororder_tax_includes_shipping_tax",
            ),
        ]

    def _is_legacy(self) -> bool:
        return self.order.legacy_wp_order_id is not None

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
    # ORD.20 part 2: the variant sold, read when an amendment restocks the line. No database
    # constraint and no cascade: the line is an immutable snapshot (a deleted variant leaves
    # the id, and nothing is restocked for it).
    variant = models.ForeignKey(
        "catalog.Variant",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="+",
    )
    unit_price_net = MoneyField()
    quantity = models.PositiveIntegerField()
    discount_net = MoneyField(default=0)
    net_total = MoneyField(help_text="Unit price x quantity - discount, rounded to cents.")
    tax_rate = models.DecimalField(max_digits=6, decimal_places=3)
    tax_reason = models.CharField(max_length=64, blank=True)
    tax_amount = MoneyField()
    vat_nature_code = models.CharField(
        max_length=16, blank=True, help_text="N-code when no VAT is charged (TAX.3); else blank."
    )
    legal_note_key = models.CharField(max_length=64, blank=True)
    fiscal_category = models.CharField(
        max_length=32,
        blank=True,
        help_text="ORD.20 snapshot: the product's fiscal category when ordered (blank: legacy).",
    )
    weight_kg = models.DecimalField(
        max_digits=9,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="ORD.20 snapshot: one unit's weight in kg (variant override included).",
    )

    class Meta:
        ordering = ["vendor_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1), name="orders_orderline_quantity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price_net__gt=0), name="orders_orderline_price_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(net_total__gte=0, tax_amount__gte=0),
                name="orders_orderline_totals_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    net_total=models.F("unit_price_net") * models.F("quantity")
                    - models.F("discount_net")
                ),
                name="orders_orderline_net_total_adds_up",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    tax_amount=Round(models.F("net_total") * models.F("tax_rate") / 100, 2)
                ),
                name="orders_orderline_tax_amount_rounded",
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

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("An order line is an immutable snapshot")


class StockReservation(BaseModel):
    """Units held back from sale so two buyers cannot take the last one (ORD.4).

    A hold belongs to a cart while the customer is browsing, and to an order once `place_order`
    confirms it; it never has both. It counts against the available stock until `expires_at`.
    Stock itself is decremented only when the payment succeeds.
    """

    vendor_scope: ClassVar[str] = "product__vendor"
    objects: ClassVar[VendorScopedManager["StockReservation"]] = VendorScopedManager()

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reservations")
    variant = models.ForeignKey(
        "catalog.Variant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reservations",
    )
    cart = models.ForeignKey(
        "cart.Cart", on_delete=models.CASCADE, null=True, blank=True, related_name="reservations"
    )
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, null=True, blank=True, related_name="reservations"
    )
    quantity = models.PositiveIntegerField()
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ["expires_at", "id"]
        indexes = [models.Index(fields=["product", "variant", "expires_at"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="orders_stockreservation_quantity_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(cart__isnull=True) | models.Q(order__isnull=True),
                name="orders_stockreservation_cart_or_order",
                violation_error_message="A hold belongs to a cart or an order, not both.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.variant or self.product} until {self.expires_at:%H:%M}"


# --- order amendments (ORD.20, owner decision D-47) ------------------------------------------


class VendorOrderVersion(BaseModel):
    """One version of a vendor order (spec 07 §Order amendments). Append-only: a database trigger
    refuses updates and deletes, as for the order snapshot.

    v1 is the placed order (`amendment` empty); each applied amendment writes the next number.
    `snapshot` is `amendment_engine.VersionSnapshot.to_json()`: lines with their tax portions,
    the shipping choice and its portions, the frozen commission rule and the delivery country.
    The delivery address is kept beside it, in `shipping_address`: empty for v1 (the order's own
    address), set from v2 on, so an address change of one package never touches the others. It is
    the one column the anonymisation scrub may overwrite (D-29).
    """

    vendor_scope: ClassVar[str] = "vendor_order__vendor"
    objects: ClassVar[VendorScopedManager["VendorOrderVersion"]] = VendorScopedManager()

    vendor_order = models.ForeignKey(VendorOrder, on_delete=models.PROTECT, related_name="versions")
    number = models.PositiveIntegerField()
    amendment = models.OneToOneField(
        "OrderAmendment",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="applied_version",
        help_text="The amendment that wrote this version; empty for v1, the placed order.",
    )
    snapshot = models.JSONField()
    goods_net = MoneyField()
    shipping_net = MoneyField()
    tax_total = MoneyField()
    gross_total = MoneyField()
    shipping_address = models.JSONField(
        null=True, blank=True, help_text="Empty for v1: the order's own delivery address."
    )

    class Meta:
        ordering = ["vendor_order", "number"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor_order", "number"], name="orders_vendororderversion_unique_number"
            ),
            models.CheckConstraint(
                condition=models.Q(number__gte=1), name="orders_vendororderversion_number_positive"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(number=1, amendment__isnull=True)
                    | models.Q(number__gt=1, amendment__isnull=False)
                ),
                name="orders_vendororderversion_v1_is_the_placed_order",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    gross_total=models.F("goods_net")
                    + models.F("shipping_net")
                    + models.F("tax_total")
                ),
                name="orders_vendororderversion_gross_adds_up",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    goods_net__gte=0, shipping_net__gte=0, tax_total__gte=0, gross_total__gte=0
                ),
                name="orders_vendororderversion_totals_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vendor_order} v{self.number}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise PermissionError("A vendor order version is append-only")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("A vendor order version is append-only")


class AmendmentState(models.TextChoices):
    DRAFT = "draft", _("Draft")
    AWAITING_QUOTE = "awaiting_quote", _("Waiting for a quote")
    PROPOSED = "proposed", _("Proposed")
    ACCEPTED = "accepted", _("Accepted")
    AWAITING_PAYMENT = "awaiting_payment", _("Waiting for payment")
    APPLIED = "applied", _("Applied")
    DECLINED = "declined", _("Declined")
    EXPIRED = "expired", _("Expired")
    WITHDRAWN = "withdrawn", _("Withdrawn")
    SUPERSEDED = "superseded", _("Superseded")
    DISCARDED = "discarded", _("Discarded")


OPEN_AMENDMENT_STATES: frozenset[str] = frozenset(
    {"draft", "awaiting_quote", "proposed", "accepted", "awaiting_payment"}
)


class AmendmentKind(models.TextChoices):
    MINOR = "minor"
    SUBSTANTIAL = "substantial"


class AmendmentInitiator(models.TextChoices):
    VENDOR_OWNER = "vendor_owner"
    VENDOR_MEMBER = "vendor_member"
    PLATFORM_OWNER = "platform_owner"
    SUPPORT = "support"


class AmendmentFallback(models.TextChoices):
    KEEP = "keep"
    REMOVE_UNAVAILABLE_LINES = "remove_unavailable_lines"
    CANCEL_PACKAGE = "cancel_package"


class OrderAmendment(BaseModel):
    """A typed change to one vendor order (spec 07 §Order amendments, ORD.20 to ORD.23).

    `kind` is set by `amendment_engine.classify_amendment`, never by the actor. At most one
    amendment per vendor order is open (`draft`, `awaiting_quote`, `proposed`, `accepted`,
    `awaiting_payment`): a partial unique index enforces it. `from_version` is the version the
    draft was computed on; an apply on a newer current version marks it `superseded`.
    The quote link of ORD.23 and the `AmendmentLink` of ORD.21 come with those tasks.
    """

    vendor_scope: ClassVar[str] = "vendor_order__vendor"
    objects: ClassVar[VendorScopedManager["OrderAmendment"]] = VendorScopedManager()

    vendor_order = models.ForeignKey(
        VendorOrder, on_delete=models.PROTECT, related_name="amendments"
    )
    from_version = models.ForeignKey(
        VendorOrderVersion, on_delete=models.PROTECT, related_name="amendments_from"
    )
    changes = models.JSONField(
        default=list, blank=True, help_text="Typed changes (amendment_engine)."
    )
    kind = models.CharField(max_length=16, choices=AmendmentKind.choices)
    reasons = models.JSONField(
        default=list, blank=True, help_text="Why the change is substantial, in plain words."
    )
    initiator = models.CharField(max_length=16, choices=AmendmentInitiator.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reason = models.TextField(help_text="Why the change is made (required).")
    staff_note = models.TextField(blank=True, help_text="Required for platform staff.")
    customer_request = models.JSONField(
        default=dict,
        blank=True,
        help_text="Channel, date and the customer's words, for an address change they asked for.",
    )
    state = models.CharField(
        max_length=16,
        choices=AmendmentState.choices,
        default=AmendmentState.DRAFT,
        db_index=True,
    )
    preview = models.JSONField(
        default=dict, blank=True, help_text="The recompute: before, after and money."
    )
    fallback = models.CharField(
        max_length=32, choices=AmendmentFallback.choices, default=AmendmentFallback.KEEP
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    reminded_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_channel = models.CharField(max_length=16, blank=True)
    payment_ref = models.CharField(max_length=255, blank=True)
    refund_ref = models.CharField(max_length=255, blank=True)
    # Each edit of a draft bumps it: a passkey grant is bound to the revision it was asked for,
    # so an edit voids it (CORE.25).
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["vendor_order", "created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor_order"],
                condition=models.Q(state__in=sorted(OPEN_AMENDMENT_STATES)),
                name="orders_orderamendment_one_open_per_vendor_order",
                violation_error_message="This package already has an open amendment.",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=AmendmentState.values),
                name="orders_orderamendment_state_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=AmendmentKind.values),
                name="orders_orderamendment_kind_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(initiator__in=AmendmentInitiator.values),
                name="orders_orderamendment_initiator_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(reason=""), name="orders_orderamendment_reason_required"
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(initiator__in=["platform_owner", "support"])
                    | ~models.Q(staff_note="")
                ),
                name="orders_orderamendment_staff_note_required",
            ),
        ]

    def __str__(self) -> str:
        return f"amendment {self.public_id} ({self.state})"

    @property
    def is_open(self) -> bool:
        return self.state in OPEN_AMENDMENT_STATES

    def save(self, *args: Any, **kwargs: Any) -> None:
        """A closed amendment (applied, declined, expired, withdrawn, superseded, discarded) is a
        record of what happened: saving it again is refused. Only the anonymisation scrub rewrites
        its personal fields, through a queryset update (D-29)."""
        if not self._state.adding:
            stored = type(self).objects.filter(pk=self.pk).values_list("state", flat=True).first()
            if stored is not None and stored not in OPEN_AMENDMENT_STATES:
                raise PermissionError(f"An amendment that is {stored} cannot be changed")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("An amendment is kept as a record; discard a draft instead")


class OrderEventAudience(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    VENDOR = "vendor", "Vendor"


class OrderEvent(BaseModel):
    """A notice sent about an order, kept on a durable medium (art. 51(7) Codice del consumo;
    spec 07, Durable medium; ORD.20 part 3b).

    `subject` and `text` are the exact text of the email, in `language`, rendered when the email
    was queued with the same context. `wording_version` is the CMP.24 version of the wording: the
    SHA-256 of the template rendered in that language with fixed placeholders, so a changed text
    in any language changes it. `terms` names the customer terms version in force when it was
    sent. Append-only: a database trigger refuses updates and deletes, except the anonymisation
    scrub (D-29) replacing the text, which may quote the vendor's free-text reason.
    """

    vendor_scope: ClassVar[str] = "vendor_order__vendor"
    objects: ClassVar[VendorScopedManager["OrderEvent"]] = VendorScopedManager()

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="events")
    vendor_order = models.ForeignKey(
        VendorOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="events"
    )
    kind = models.CharField(max_length=64, help_text="The email template key.")
    audience = models.CharField(max_length=16, choices=OrderEventAudience.choices)
    language = LanguageField()
    subject = models.CharField(max_length=300)
    text = models.TextField()
    wording_version = models.CharField(max_length=71, help_text="sha256:<hex> (CMP.24).")
    terms = models.JSONField(
        default=dict, blank=True, help_text="The customer terms version in force when sent."
    )
    payload = models.JSONField(default=dict, blank=True, help_text="Amounts and references.")

    class Meta:
        ordering = ["order", "created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(audience__in=OrderEventAudience.values),
                name="orders_orderevent_audience_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} ({self.audience}) on {self.order_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise PermissionError("An order event is append-only")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("An order event is append-only")


class StaffDraftState(models.TextChoices):
    OPEN = "open", _("Open: staff may change it and send the link")
    OPENED = "opened", _("Opened: the customer put it in their cart")


class StaffDraft(BaseModel):
    """ORD.32: a cart that Support or the platform owner prepared from a past order, for the same
    customer, waiting for the customer to open its one-time link.

    It is not an order and binds nobody: it holds what was bought (product, variant, quantity)
    and never a price, reserves no stock and writes no ledger line. The customer opens the link,
    the lines go into their own cart at today's prices, and the normal checkout follows. The
    customer is the source order's account or, for a guest order, its email: neither is copied
    here, both are read from the order when the link is sent.

    The link token is never stored: only its SHA-256 digest, with the draft version it was issued
    for, its expiry and the send times (at most 3 a day). A change of the draft bumps `version`
    and clears the digest, so an older link stops working.
    """

    source_order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="staff_drafts")
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="staff_drafts",
        help_text="The source order's account; empty for a guest order.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The staff member who prepared the draft.",
    )
    state = models.CharField(
        max_length=16, choices=StaffDraftState.choices, default=StaffDraftState.OPEN
    )
    version = models.PositiveIntegerField(default=1)
    skipped = models.JSONField(
        default=list,
        blank=True,
        help_text="Items of the order left out when the draft was made, with the reason.",
    )
    link_digest = models.CharField(
        max_length=64, blank=True, db_index=True, help_text="SHA-256 of the link token."
    )
    link_version = models.PositiveIntegerField(null=True, blank=True)
    link_expires_at = models.DateTimeField(null=True, blank=True)
    send_times = models.JSONField(
        default=list, blank=True, help_text="When the link was sent (ISO times, most recent last)."
    )
    opened_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=StaffDraftState.values),
                name="orders_staffdraft_state_valid",
            ),
            models.UniqueConstraint(
                fields=["source_order"],
                condition=models.Q(state="open"),
                name="orders_staffdraft_one_open_per_order",
                violation_error_message="This order already has an open draft.",
            ),
        ]

    def __str__(self) -> str:
        return f"Staff draft {self.public_id} from order {self.source_order_id}"


class StaffDraftLine(BaseModel):
    """One item of a staff draft: what and how many, never a price (ORD.32). Platform staff data,
    not vendor-scoped: no shop sees or lists drafts (`tests.tenancy_registry.NOT_VENDOR_OWNED`)."""

    draft = models.ForeignKey(StaffDraft, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="+")
    variant = models.ForeignKey(
        "catalog.Variant", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    title = models.CharField(max_length=300, help_text="The item's name on the source order.")
    variant_label = models.CharField(max_length=300, blank=True)
    quantity = models.PositiveIntegerField()

    class Meta:
        ordering = ["draft", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["draft", "product", "variant"],
                nulls_distinct=False,
                name="orders_staffdraftline_one_per_item",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="orders_staffdraftline_quantity_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.title}"
