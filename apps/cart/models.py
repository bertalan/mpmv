"""Carts and cart lines (spec 07 §7.2, ORD.1).

A cart belongs either to a signed-in user (one cart per user) or to a guest session (one cart
per session key), never both: the database enforces it. Lines point at products and variants by
their internal identity, never by the vendor SKU (CAT.13), and hold display prices only: the
order snapshots its prices when it is placed (ORD.2, ORD.4). Change carts through
`apps.cart.services`, which check stock and purchasability (`engine.py`).

Vendor scoping (CORE.3): a line is reachable from its product's vendor (`vendor_scope`), so the
lines of one vendor's products can be listed without seeing other vendors'. A cart as a whole
is the customer's and spans vendors; `services.vendor_packages` groups its lines by vendor.
"""

from typing import ClassVar

from django.conf import settings
from django.db import models

from apps.catalog.models import Product, Variant
from apps.core.fields import CountryField, LanguageField, MoneyField
from apps.core.i18n import primary_language
from apps.core.models import BaseModel
from apps.vendors.scoping import VendorScopedManager


class Cart(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="carts",
        help_text="The signed-in customer; empty for a guest cart.",
    )
    session_key = models.CharField(
        max_length=64,
        blank=True,
        help_text="The guest's session key when the cart was created; empty for a user's cart.",
    )
    language = LanguageField(default=primary_language)
    country_hint = CountryField(
        blank=True, help_text="Where the visitor said they ship to, for early estimates."
    )

    class Meta:
        ordering = ["-updated_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(user__isnull=True) & ~models.Q(session_key=""))
                | (models.Q(user__isnull=False) & models.Q(session_key="")),
                name="cart_cart_user_or_session",
                violation_error_message="A cart belongs to a user or to a guest session.",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(user__isnull=False),
                name="cart_cart_one_per_user",
            ),
            models.UniqueConstraint(
                fields=["session_key"],
                condition=~models.Q(session_key=""),
                name="cart_cart_one_per_session",
            ),
        ]

    def __str__(self) -> str:
        owner = f"user {self.user_id}" if self.user_id else "guest"
        return f"Cart {self.public_id} ({owner})"


class CartLine(BaseModel):
    vendor_scope: ClassVar[str] = "product__vendor"
    objects = VendorScopedManager()

    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="cart_lines")
    variant = models.ForeignKey(
        Variant, on_delete=models.CASCADE, null=True, blank=True, related_name="cart_lines"
    )
    quantity = models.PositiveIntegerField(default=1)
    added_price_net = MoneyField(
        "net price when added",
        null=True,
        blank=True,
        help_text="Display only: shown to warn the customer when the price has changed.",
    )

    class Meta:
        ordering = ["cart", "created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "product", "variant"],
                nulls_distinct=False,
                name="cart_cartline_one_per_item",
                violation_error_message="The cart already has a line for this item.",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="cart_cartline_quantity_positive",
                violation_error_message="The quantity must be at least 1.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.variant or self.product}"
