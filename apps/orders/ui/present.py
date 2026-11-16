"""View models of the cart and checkout pages (ORD.5).

Everything shown comes from the `CheckoutPreview` of `build_checkout`; this module only formats
it (money, messages in the visitor's language, the vendor names and links) and never decides
anything: whether the cart can be ordered is `preview.placeable`.

Line prices are consumer prices (TAX.12): with VAT, at the rate the tax engine decided for the
delivery address, or before the address at the estimated country's rate (`apps.tax.prices`). A
business buyer who opted in to net prices sees them without VAT.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.http import HttpRequest
from django.utils.formats import number_format
from django.utils.translation import get_language, gettext_lazy
from django.utils.translation import gettext as _

from apps.cart.models import Cart, CartLine
from apps.catalog import slugs
from apps.core.money import CURRENCY, round_line
from apps.orders import engine
from apps.orders.services import CheckoutPreview
from apps.tax import engine as tax_engine
from apps.tax import prices as tax_prices
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


def line_prices(
    unit_net: Decimal,
    quantity: int,
    fiscal_category: str,
    *,
    vendor: Vendor | None,
    request: HttpRequest | None,
    line_net: Decimal | None = None,
    tax_rate: Decimal | None = None,
    line_tax: Decimal | None = None,
) -> tuple[Decimal | None, Decimal | None]:
    """The unit price and the line total a buyer sees (TAX.12), VAT included.

    With the tax engine's decision for the delivery address (`tax_rate`, `line_tax`) the amounts
    are final; before it, the vendor's decision for the estimated destination (`apps.tax.prices`:
    what checkout will charge, the vendor's regime included). A business buyer
    who opted in sees the net amounts. Each amount is rounded ROUND_HALF_UP on its own line.
    (None, None) when no rate can be decided: no amount is shown, never a net one.
    """
    net = line_net if line_net is not None else round_line(unit_net * quantity)
    if tax_prices.business_net_view(request):
        return unit_net, net
    if tax_rate is not None:
        total = net + line_tax if line_tax is not None else tax_engine.gross_amount(net, tax_rate)
        return tax_engine.gross_amount(unit_net, tax_rate), total
    if vendor is None:
        return None, None
    try:
        unit = tax_prices.consumer_price(unit_net, fiscal_category, vendor=vendor, request=request)
    except tax_prices.PriceUnavailable:
        return None, None
    return unit.gross, tax_engine.gross_amount(net, unit.rate)


def option_price(option: engine.ShippingOption, *, net_view: bool) -> str:
    """A shipping option's price: with its VAT for a consumer (Directive 2011/83 art. 6(1)(e),
    TAX.12), net for a business buyer who opted in. Never a net price shown as a consumer one."""
    if net_view:
        return f"{money(option.net)} {_('excl. VAT')}"
    if option.tax is not None:
        return money(option.net + option.tax)
    return money(option.net) if option.net == 0 else _("Price at checkout")


def summary_rows(
    totals: engine.Totals, lines: Iterable[engine.SummaryLine], *, net_view: bool
) -> list[dict[str, str]]:
    """The rows above a total. A consumer sees goods and shipping with VAT and the VAT they
    contain (TAX.12); a business buyer who opted in sees the net rows and the VAT added."""
    if net_view:
        return [
            {"label": _("Goods, excluding VAT"), "amount": money(totals.goods_net)},
            {"label": _("Shipping, excluding VAT"), "amount": money(totals.shipping_net)},
            {"label": _("VAT"), "amount": money(totals.tax_total)},
        ]
    goods_tax = sum((line.tax for line in lines if line.tax is not None), start=Decimal("0.00"))
    goods = totals.goods_net + goods_tax
    return [
        {"label": _("Goods, VAT included"), "amount": money(goods)},
        {"label": _("Shipping, VAT included"), "amount": money(totals.grand_total - goods)},
        {"label": _("Of which VAT"), "amount": money(totals.tax_total)},
    ]


def package_views(
    preview: CheckoutPreview,
    cart: Cart,
    vendors: Mapping[int, Vendor],
    *,
    request: HttpRequest | None = None,
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
            unit, total = line_prices(
                line.unit_price_net,
                line.quantity,
                cart_line.product.fiscal_category if cart_line is not None else "",
                vendor=vendor,
                request=request,
                line_net=net,
                tax_rate=line.tax_rate,
                line_tax=line.tax,
            )
            view.lines.append(
                LineView(
                    id=str(cart_line.public_id) if cart_line is not None else "",
                    name=line.title,
                    unit_price=money(unit),
                    quantity=line.quantity,
                    total=money(total),
                    problem=" ".join(by_product.get(line.product_id, [])),
                )
            )
        net_view = tax_prices.business_net_view(request)
        view.options = [
            OptionView(
                key=option.key,
                label=option.label,
                price=option_price(option, net_view=net_view),
                delivery=_delivery(option.min_days, option.max_days),
                checked=option.key == package.selected_shipping,
            )
            for option in package.shipping_options
        ]
        totals = package.totals
        if totals is not None:
            view.summary_lines = summary_rows(totals, package.lines, net_view=net_view)
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


def order_totals(
    preview: CheckoutPreview, *, request: HttpRequest | None = None
) -> dict[str, Any] | None:
    totals = preview.totals
    if totals is None or not preview.placeable:
        return None
    return {
        "lines": summary_rows(
            totals,
            [line for package in preview.packages for line in package.lines],
            net_view=tax_prices.business_net_view(request),
        ),
        "total": money(totals.grand_total),
        "note": _("VAT included in the total"),
    }
