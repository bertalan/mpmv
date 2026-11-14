"""What each vendor of a paid order is owed, from the order's frozen snapshots (PAY.3, D-08).

One place computes the figures, so the PaymentIntent (shares and platform fee at checkout) and
the ledger postings and transfers after payment (apps.ledger) can never disagree. Everything is
the paid-time figure: `compute_commission` on the vendor order's commission snapshot, with the
vendor order's own goods, shipping and gross totals, never a live rule or a live price.

A vendor order whose snapshot cannot be used (no rule, an invalid rule) is `blocked` with the
reason: it is never settled at 0%. Its money stays on the platform and is reported, not lost.
A vendor without a ready payout account has no `account_ref`: it gets ledger lines (the receivable
the platform owes it) and no transfer until the account is ready (D-19 makes this rare).
"""

from dataclasses import dataclass
from decimal import Decimal

from apps.core.money import round_line
from apps.orders.models import Order, VendorOrder
from apps.vendors import engine as vendor_engine
from apps.vendors.commission import compute_commission

from . import stripe_engine
from .engine import VendorShare
from .models import ConnectedAccount

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class VendorSettlement:
    """The ledger lines and the transfer of one vendor order.

    `sale` is the goods with their VAT and `shipping` the shipping with its VAT: the vendor
    collects both and accounts for the VAT itself, so `sale + shipping` is the gross paid and
    `vendor_net` = gross paid - commission - payment fee (spec 04 section 4.4).
    """

    vendor_order: VendorOrder
    sale: Decimal
    shipping: Decimal
    commission: Decimal
    payment_fee: Decimal
    vendor_net: Decimal
    account_ref: str  # the vendor's ready connected account, "" when none

    @property
    def gross(self) -> Decimal:
        return round_line(self.sale + self.shipping)

    @property
    def transferable(self) -> bool:
        return bool(self.account_ref) and self.vendor_net > 0


@dataclass(frozen=True)
class BlockedVendor:
    vendor_order: VendorOrder
    reason: str


@dataclass(frozen=True)
class OrderSettlement:
    order: Order
    vendors: tuple[VendorSettlement, ...]
    blocked: tuple[BlockedVendor, ...]

    def shares(self, currency: str) -> tuple[VendorShare, ...]:
        """The vendor shares of the PaymentIntent: only vendors that get a transfer, each rounded
        to the currency's minor unit."""
        return tuple(
            VendorShare(v.account_ref, stripe_engine.round_to_currency(v.vendor_net, currency))
            for v in self.vendors
            if v.transferable
        )


def ready_account_ref(vendor_order: VendorOrder) -> str:
    account = ConnectedAccount.objects.filter(
        vendor_id=vendor_order.vendor_id,
        provider="stripe",
        status=vendor_engine.PAYOUT_ACCOUNT_READY,
    ).first()
    return account.account_ref if account is not None else ""


def vendor_settlement(vendor_order: VendorOrder) -> VendorSettlement:
    """The figures of one vendor order. Raises `NoCommissionRule` for a missing snapshot and
    `ValueError` for an invalid one; `order_settlement` turns both into a blocked vendor."""
    snapshot = dict(vendor_order.commission_snapshot or {})
    if not snapshot:
        raise vendor_engine.NoCommissionRule("the vendor order has no commission snapshot")
    snapshot.setdefault("promo_label", "")  # optional in the order snapshot, required by the rule
    shipping_gross = round_line(vendor_order.shipping_net + vendor_order.shipping_tax_amount)
    result = compute_commission(
        snapshot,
        goods_net=vendor_order.goods_net,
        gross_paid=vendor_order.gross_total,
        shipping_net=vendor_order.shipping_net,
    )
    return VendorSettlement(
        vendor_order=vendor_order,
        sale=round_line(vendor_order.gross_total - shipping_gross),
        shipping=shipping_gross,
        commission=result.commission,
        payment_fee=result.payment_fee,
        vendor_net=result.vendor_net,
        account_ref=ready_account_ref(vendor_order),
    )


def order_settlement(order: Order) -> OrderSettlement:
    settled: list[VendorSettlement] = []
    blocked: list[BlockedVendor] = []
    for vendor_order in order.vendor_orders.select_related("vendor").order_by("id"):
        try:
            settled.append(vendor_settlement(vendor_order))
        except (ValueError, vendor_engine.NoCommissionRule) as error:
            blocked.append(BlockedVendor(vendor_order, str(error)))
    return OrderSettlement(order, tuple(settled), tuple(blocked))
