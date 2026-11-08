"""Pure cart rules (ORD.1): stock limits, what can go in a cart, the merge on sign-in and the
packages per vendor. No ORM: `services.py` reads the rows and applies the results.

Cart prices are for display only. A line remembers the net price shown when it was added
(`added_price_net`) to warn the customer when the price changed; the prices that count are
snapshotted when the order is placed (ORD.2, ORD.4).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from apps.catalog.engine import BUY
from apps.core.money import round_line, sum_lines
from apps.vendors.engine import ACTIVE as VENDOR_ACTIVE

# Catalog choice values (apps.catalog.models), repeated so the engine stays free of models; a
# unit test keeps them equal to the model choices.
PUBLISHED = "published"
ALWAYS_OPEN = "always_open"
OUT_OF_STOCK = "out_of_stock"

# A technical ceiling on one line, so a typo cannot ask for millions of units. Not a business
# limit: stock is the real one. A per-product maximum, if the owner wants one, would go here.
MAX_LINE_QUANTITY = 999


@dataclass(frozen=True)
class LineKey:
    """What a cart line is: a product, and its variant for a variable product (internal ids,
    never the vendor SKU, CAT.13)."""

    product_id: int
    variant_id: int | None = None


# --- Stock and limits


def available_quantity(stock_policy: str, stock_qty: int, stock_status: str) -> int | None:
    """Units that can be put in a cart now; None when backorders make it unlimited.

    Tracked stock (`track`, `from_source`) is the stored quantity, or 0 when marked out of
    stock. Reservations of other carts (StockReservation) are ORD.4's: the cart does not hold
    stock, the order does.
    """
    if stock_policy == ALWAYS_OPEN:
        return None
    if stock_status == OUT_OF_STOCK:
        return 0
    return max(stock_qty, 0)


def line_limit(available: int | None) -> int:
    """The most units one line may hold: the available stock, at most MAX_LINE_QUANTITY."""
    if available is None:
        return MAX_LINE_QUANTITY
    return max(0, min(available, MAX_LINE_QUANTITY))


def quantity_error(quantity: int, *, limit: int) -> str | None:
    """Why a line cannot hold `quantity` units, or None."""
    if quantity < 1:
        return "The quantity must be at least 1."
    if limit <= 0:
        return "This item is out of stock."
    if quantity > limit:
        return f"Only {limit} available."
    return None


# --- What can go in a cart


def purchase_error(
    *,
    product_status: str,
    sales_mode: str,
    vendor_status: str,
    is_variable: bool,
    has_variant: bool,
) -> str | None:
    """Why this product (and variant) cannot go in a cart, or None.

    Only a published `buy` product of an active vendor can be bought. A variable product is
    bought as one of its variants; a simple product has none.
    """
    if product_status != PUBLISHED:
        return "This product is not for sale."
    if sales_mode != BUY:
        return "This product is available on request only."
    if vendor_status != VENDOR_ACTIVE:
        return "This vendor is not selling at the moment."
    if is_variable and not has_variant:
        return "Choose a variant of this product."
    if has_variant and not is_variable:
        return "This product has no variants."
    return None


def display_unit_price(
    price_net: Decimal | None, sales_mode: str, override: Decimal | None
) -> Decimal | None:
    """The net unit price shown in the cart: the variant's override, else the product's price;
    None when the product is not for sale (its price is never public, CAT.14 / Q-31).

    Sale prices are not applied yet: the catalog has no rule for a sale on a variant with an
    override. The checkout (ORD.3) prices lines with the rule the owner confirms.
    """
    if sales_mode != BUY:
        return None
    return override if override is not None else price_net


# --- Merge on sign-in


@dataclass(frozen=True)
class LineState:
    key: LineKey
    quantity: int
    added_price_net: Decimal | None


@dataclass(frozen=True)
class MergedLine:
    """The outcome for one line of the guest cart. `quantity` 0 means the line is dropped."""

    key: LineKey
    quantity: int
    requested: int
    added_price_net: Decimal | None

    @property
    def adjusted(self) -> bool:
        """The customer gets fewer units than the two carts held: tell them."""
        return self.quantity != self.requested


def merge_lines(
    user_lines: Sequence[LineState],
    guest_lines: Sequence[LineState],
    limits: Mapping[LineKey, int],
) -> list[MergedLine]:
    """Merge a guest cart into the user's cart when the visitor signs in.

    The rule (ORD.1):

    - the same line (same product and same variant) in both carts becomes one line, with the
      quantities added up;
    - a line only in the guest cart moves to the user's cart;
    - a line only in the user's cart is left as it is (the checkout re-checks it);
    - every merged or moved line is capped at its limit (`line_limit`: stock, at most
      MAX_LINE_QUANTITY). A line that can no longer be bought (limit 0, or no limit given) is
      dropped. Capped and dropped lines are reported (`adjusted`) so the customer is told;
    - the merged line keeps the price the customer saw last, the guest cart's: the warning
      about a price change compares the current price with what they saw most recently.

    Two variants of one product are two lines and never merge. Returns one entry per guest
    line, in guest-cart order.
    """
    mine = {line.key: line for line in user_lines}
    merged = []
    for guest in guest_lines:
        existing = mine.get(guest.key)
        requested = guest.quantity + (existing.quantity if existing else 0)
        quantity = min(requested, max(limits.get(guest.key, 0), 0))
        merged.append(
            MergedLine(
                key=guest.key,
                quantity=quantity,
                requested=requested,
                added_price_net=guest.added_price_net,
            )
        )
    return merged


# --- Packages per vendor (the multi-vendor split, ORD.3)


@dataclass(frozen=True)
class PricedLine[T]:
    """A cart line with its vendor and current display price; `item` is the caller's line."""

    item: T
    vendor_id: int
    quantity: int
    unit_price_net: Decimal | None
    added_price_net: Decimal | None

    @property
    def line_net(self) -> Decimal | None:
        """Unit price times quantity, rounded per line (ROUND_HALF_UP); None without a price."""
        if self.unit_price_net is None:
            return None
        return round_line(self.unit_price_net * self.quantity)

    @property
    def price_changed(self) -> bool:
        return self.unit_price_net != self.added_price_net


@dataclass(frozen=True)
class VendorPackage[T]:
    """One vendor's lines: the unit that shipping, tax and the order split work on."""

    vendor_id: int
    lines: tuple[PricedLine[T], ...]

    @property
    def goods_net(self) -> Decimal:
        """Display total of the goods, VAT and shipping excluded."""
        return sum_lines(line.line_net for line in self.lines if line.line_net is not None)


def group_by_vendor[T](lines: Iterable[PricedLine[T]]) -> list[VendorPackage[T]]:
    """Packages in the order their vendor first appears; lines keep their order."""
    groups: dict[int, list[PricedLine[T]]] = {}
    for line in lines:
        groups.setdefault(line.vendor_id, []).append(line)
    return [VendorPackage(vendor_id=v, lines=tuple(ls)) for v, ls in groups.items()]
