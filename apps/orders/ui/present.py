"""View models of the cart and checkout pages (ORD.5).

Everything shown comes from the `CheckoutPreview` of `build_checkout`; this module only formats
it (money, messages in the visitor's language, the vendor names and links) and never decides
anything: whether the cart can be ordered is `preview.placeable`.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.utils.formats import number_format
from django.utils.translation import get_language, gettext_lazy
from django.utils.translation import gettext as _

from apps.cart.models import Cart, CartLine
from apps.catalog import slugs
from apps.core.money import CURRENCY, round_line
from apps.orders import engine
from apps.orders.services import CheckoutPreview
from apps.vendors.models import Vendor

# The message of each blocking code, in the visitor's language. Unknown codes fall back to the
# engine's own English message.
BLOCK_MESSAGES = {
    engine.CHECKOUT_OFF: gettext_lazy(
        "Ordering is not available right now. Please try again later."
    ),
    engine.EMPTY_CART: gettext_lazy("Your cart is empty."),
    engine.ADDRESS_MISSING: gettext_lazy("Enter the delivery address to see shipping and VAT."),
    engine.NOT_FOR_SALE: gettext_lazy("This item cannot be bought right now."),
    engine.ON_REQUEST: gettext_lazy(
        "This item is sold on request only. Ask the vendor for a quote."
    ),
    engine.OUT_OF_STOCK: gettext_lazy(
        "This item is out of stock, or fewer are available than the quantity in your cart."
    ),
    engine.NEEDS_QUOTE: gettext_lazy(
        "Shipping to your address needs a quote from the vendor. Ask the vendor for a quote."
    ),
    engine.SHIPPING_BLOCKED: gettext_lazy("This vendor does not ship to your address."),
    engine.SHIPPING_CHOICE_INVALID: gettext_lazy("Choose one of the shipping options."),
    engine.TAX_BLOCKED: gettext_lazy(
        "We cannot calculate the VAT for this vendor's items to your address. Contact the vendor."
    ),
    engine.NO_PRICE: gettext_lazy("This item has no price yet."),
    engine.SHIPPING_VAT_SPLIT: gettext_lazy(
        "Shipping on a package with different VAT rates cannot be ordered yet. "
        "Order these items in separate orders."
    ),
}
NBSP = "\N{NO-BREAK SPACE}"
QUOTE_CODES = frozenset({engine.ON_REQUEST, engine.NEEDS_QUOTE})


def money(amount: Decimal | None) -> str:
    """`€ 1,234.50` in the visitor's number format."""
    if amount is None:
        return ""
    return f"{CURRENCY_SIGN}{NBSP}{number_format(amount, 2, use_l10n=True, force_grouping=True)}"


CURRENCY_SIGN = "€" if CURRENCY == "EUR" else CURRENCY


def block_message(block: engine.Block) -> str:
    known = BLOCK_MESSAGES.get(block.code)
    return str(known) if known is not None else block.message


@dataclass
class LineView:
    id: str
    name: str
    unit_price: str
    quantity: int
    total: str
    variant: str = ""
    problem: str = ""


@dataclass
class OptionView:
    key: str
    label: str
    price: str
    delivery: str
    checked: bool


@dataclass
class PackageView:
    vendor: str
    state: str
    lines: list[LineView]
    messages: list[str] = field(default_factory=list)
    quote_url: str = ""
    options: list[OptionView] = field(default_factory=list)
    field_name: str = ""
    summary_id: str = ""
    summary_lines: list[dict[str, str]] = field(default_factory=list)
    total: str = ""
    note: str = ""


def _delivery(min_days: int | None, max_days: int | None) -> str:
    if min_days is None and max_days is None:
        return ""
    low, high = min_days or max_days or 0, max_days or min_days or 0
    if low == high:
        return _("%(days)s working days") % {"days": low}
    return _("%(low)s to %(high)s working days") % {"low": low, "high": high}


def package_views(
    preview: CheckoutPreview, cart: Cart, vendors: Mapping[int, Vendor]
) -> list[PackageView]:
    """One view per vendor package, in the order of the preview."""
    lines: dict[tuple[int, int | None], CartLine] = {
        (line.product_id, line.variant_id): line
        for line in cart.lines.select_related("product", "variant")
    }
    language = get_language() or "en"
    default_language = "en"
    out: list[PackageView] = []
    for index, package in enumerate(preview.packages, start=1):
        vendor = vendors.get(package.vendor_id)
        name = vendor.display_name if vendor is not None else ""
        by_product: dict[int, list[str]] = {}
        shared: list[str] = []
        quote = False
        for block in package.blocks:
            message = block_message(block)
            quote = quote or block.code in QUOTE_CODES
            if block.product_id is not None:
                by_product.setdefault(block.product_id, []).append(message)
            elif message not in shared:
                shared.append(message)
        view = PackageView(
            vendor=name,
            state=package.state,
            lines=[],
            messages=shared,
            field_name=f"ship_{vendor.public_id}" if vendor is not None else "",
            summary_id=f"package-{index}",
        )
        if quote and vendor is not None and vendor.slug:
            view.quote_url = slugs.vendor_path(
                language, vendor.slug, default_language=default_language
            )
        for line in package.lines:
            cart_line = lines.get((line.product_id, line.variant_id))
            net = (
                line.net
                if line.net is not None
                else round_line(line.unit_price_net * line.quantity)
            )
            view.lines.append(
                LineView(
                    id=str(cart_line.public_id) if cart_line is not None else "",
                    name=line.title,
                    unit_price=money(line.unit_price_net),
                    quantity=line.quantity,
                    total=money(net),
                    problem=" ".join(by_product.get(line.product_id, [])),
                )
            )
        view.options = [
            OptionView(
                key=option.key,
                label=option.label,
                price=money(option.net),
                delivery=_delivery(option.min_days, option.max_days),
                checked=option.key == package.selected_shipping,
            )
            for option in package.shipping_options
        ]
        totals = package.totals
        if totals is not None:
            view.summary_lines = [
                {"label": _("Goods, excluding VAT"), "amount": money(totals.goods_net)},
                {"label": _("Shipping, excluding VAT"), "amount": money(totals.shipping_net)},
                {"label": _("VAT"), "amount": money(totals.tax_total)},
            ]
            view.total = money(totals.grand_total)
            view.note = _("VAT included in the total")
        out.append(view)
    return out


def cart_blocks(preview: CheckoutPreview, *, hide: Iterable[str] = ()) -> list[str]:
    """The messages that are about the whole cart (no vendor), without the `hide` codes."""
    hidden = set(hide)
    seen: list[str] = []
    for block in preview.blocks:
        if block.vendor_id is None and block.code not in hidden:
            message = block_message(block)
            if message not in seen:
                seen.append(message)
    return seen


def order_totals(preview: CheckoutPreview) -> dict[str, Any] | None:
    totals = preview.totals
    if totals is None or not preview.placeable:
        return None
    return {
        "lines": [
            {"label": _("Goods, excluding VAT"), "amount": money(totals.goods_net)},
            {"label": _("Shipping, excluding VAT"), "amount": money(totals.shipping_net)},
            {"label": _("VAT"), "amount": money(totals.tax_total)},
        ],
        "total": money(totals.grand_total),
        "note": _("VAT included in the total"),
    }
