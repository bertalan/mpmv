"""ORD.31: "order again". The customer puts the still-available items of a past order back in
the cart, at today's prices, availability and shipping.

Nothing of the old order is reused except what was bought (product, variant, quantity): prices
come from the catalog, stock from the catalog, and shipping is checked against the cart's
destination country (the customer's current address, as far as the cart knows it). Nothing is
placed or paid here: the cart goes through the normal checkout (ORD.3, ORD.4). Items already in
the cart get the quantities added, capped at the stock available.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import User
from apps.cart import engine as cart_engine
from apps.cart import services as cart_services
from apps.cart.models import Cart
from apps.catalog.models import Product, Variant
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.countries import InvalidCountry
from apps.shipping import engine as shipping_engine
from apps.shipping.services import quote_shipping
from apps.tax import prices as tax_prices

from . import engine, services
from .models import OrderLine

type ItemKey = tuple[int, int | None]  # (product id, variant id)


@dataclass(frozen=True)
class ReorderedItem:
    title: str
    variant_label: str
    quantity: int  # units of the item in the cart now
    added: int
    requested: int  # what the cart and the order asked for together
    capped: bool


@dataclass(frozen=True)
class SkippedItem:
    title: str
    variant_label: str
    reason: str  # engine.REORDER_*


@dataclass
class ReorderResult:
    added: list[ReorderedItem] = field(default_factory=list)
    skipped: list[SkippedItem] = field(default_factory=list)
    changeset: ChangeSet = field(default_factory=ChangeSet)


@dataclass
class _Item:
    key: ItemKey
    title: str
    variant_label: str
    quantity: int
    product: Product | None
    variant: Variant | None


def _items(lines: list[OrderLine]) -> list[_Item]:
    """The order's lines, one entry per product and variant, quantities added up. Products
    (with their vendor and its payout account) and variants are read in one query each."""
    products = Product.objects.select_related("vendor__connected_account").in_bulk(
        {line.product_id for line in lines if line.product_id}
    )
    variants = Variant.objects.in_bulk({line.variant_id for line in lines if line.variant_id})
    found: dict[ItemKey, _Item] = {}
    for line in lines:
        product = products.get(line.product_id) if line.product_id else None
        if product is None:  # the product was deleted: nothing to put back
            found[(-line.pk, None)] = _Item(
                (-line.pk, None), line.title, line.variant_label, line.quantity, None, None
            )
            continue
        key = (product.pk, line.variant_id)
        if key in found:
            found[key].quantity += line.quantity
            continue
        variant = variants.get(line.variant_id) if line.variant_id else None
        gone = bool(line.variant_id) and variant is None  # the variant was deleted
        found[key] = _Item(
            key, line.title, line.variant_label, line.quantity, None if gone else product, variant
        )
    return list(found.values())


def _unit_weight(product: Product, variant: Variant | None) -> Decimal:
    if variant is not None and variant.weight_kg_override is not None:
        return variant.weight_kg_override
    return product.weight_kg if product.weight_kg is not None else Decimal("0")


def item_for(
    key: ItemKey,
    title: str,
    variant_label: str,
    quantity: int,
    product: Product | None,
    variant: Variant | None,
) -> "_Item":
    """One item to put in a cart (ORD.32 builds them from a staff draft's lines). Load the
    product with `select_related("vendor__connected_account")`."""
    return _Item(key, title, variant_label, quantity, product, variant)


def order_items(lines: list[OrderLine]) -> list["_Item"]:
    """The items of a past order's lines, one per product and variant (ORD.31, ORD.32)."""
    return _items(lines)


def _undeliverable(
    items: list[_Item], limits: dict[ItemKey, int], country: str, language: str
) -> set[ItemKey]:
    """The items whose vendor cannot ship to `country` (shipping blocks the package). No
    destination known: nothing is blocked here, the checkout asks for it."""
    if not country:
        return set()
    try:
        destination = shipping_engine.Destination(country)
    except InvalidCountry:
        return set()
    by_vendor: dict[int, list[_Item]] = {}
    for item in items:
        if item.product is not None and limits.get(item.key, 0) > 0:
            by_vendor.setdefault(item.product.vendor_id, []).append(item)
    blocked: set[ItemKey] = set()
    for group in by_vendor.values():
        vendor = group[0].product.vendor  # type: ignore[union-attr]
        package = shipping_engine.VendorPackage(
            vendor_id=vendor.pk,
            lines=tuple(
                shipping_engine.PackageLine(
                    weight_kg=_unit_weight(i.product, i.variant),  # type: ignore[arg-type]
                    quantity=i.quantity,
                    net_goods=cart_engine.display_unit_price(
                        i.product.price_net,  # type: ignore[union-attr]
                        "buy",
                        i.variant.price_net_override if i.variant else None,
                    )
                    or Decimal("0"),
                )
                for i in group
            ),
        )
        outcome = quote_shipping(vendor, package, destination, language=language or "en")
        if isinstance(outcome, shipping_engine.Blocked):
            blocked.update(i.key for i in group)
    return blocked


def item_limits(items: list[_Item]) -> dict[ItemKey, int | None]:
    """The most units each item may have in a cart line today; None when it cannot be bought
    (retired, not for sale, vendor paused, on request)."""
    found: dict[ItemKey, int | None] = {}
    for item in items:
        if item.product is None:
            found[item.key] = None
            continue
        try:
            found[item.key] = cart_services.item_limit(item.product, item.variant)
        except ValidationError:
            found[item.key] = None
    return found


def plan_items(
    items: list[_Item],
    *,
    in_cart: dict[ItemKey, int],
    country: str,
    language: str,
    prices: tax_prices.PriceCheck | None = None,
) -> list[engine.ReorderOutcome]:
    """What of `items` can go into a cart today, with today's stock, consumer price (#757) and
    shipping to `country`, added to what the cart holds (`in_cart`); one outcome per item, in
    order. `prices` defaults to `cart.services.price_check(country=country)`."""
    limits = item_limits(items)
    known = {key: limit for key, limit in limits.items() if limit is not None}
    prices = prices or cart_services.price_check(country=country or None)
    unpriced = {
        item.key
        for item in items
        if item.product is not None
        and item.key in known
        and not cart_services.has_price(item.product, item.variant, prices)
    }
    blocked = _undeliverable(items, known, country, language)
    return engine.plan_reorder(
        [
            engine.ReorderWish(
                key=item.key,
                quantity=item.quantity,
                in_cart=in_cart.get(item.key, 0),
                limit=limits[item.key],
                unpriced=item.key in unpriced,
                undeliverable=item.key in blocked,
            )
            for item in items
        ]
    )


def fill_cart(
    cart: Cart, items: list[_Item], *, country: str, ctx: AuditContext, dry_run: bool = False
) -> ReorderResult:
    """Put `items` in `cart` at today's prices, capped at the stock available, skipping (with
    the reason) what cannot be bought or shipped to `country` today. An item already in the
    cart gets the quantities added. With `dry_run=True` nothing is written."""
    in_cart = (
        {(ln.product_id, ln.variant_id): ln.quantity for ln in cart.lines.all()} if cart.pk else {}
    )
    prices = cart_services.price_check(country=country or None)  # the tax tables, read once
    outcomes = plan_items(
        items, in_cart=in_cart, country=country, language=cart.language, prices=prices
    )
    result = ReorderResult()
    to_add: list[tuple[Product, Variant | None, int]] = []
    for item, outcome in zip(items, outcomes, strict=True):
        if outcome.skipped:
            result.skipped.append(SkippedItem(item.title, item.variant_label, outcome.skipped))
            continue
        if outcome.added > 0 and item.product is not None and cart.pk:
            to_add.append((item.product, item.variant, outcome.added))
        result.added.append(
            ReorderedItem(
                item.title,
                item.variant_label,
                outcome.quantity,
                outcome.added,
                outcome.requested,
                outcome.capped,
            )
        )
    if to_add:  # one lock and a constant number of queries, whatever the number of lines
        result.changeset.changes.extend(
            cart_services.add_many_to_cart(
                cart, to_add, ctx=ctx, prices=prices, dry_run=dry_run
            ).changes
        )
    return result


def order_again(
    user: User, public_id: uuid.UUID, *, ctx: AuditContext, dry_run: bool = False
) -> ReorderResult | None:
    """Put the still-available items of the customer's past order back in their cart.

    None when the order is not the user's (the view answers 404, also for a guessed id). Lines
    that cannot be bought today are listed as skipped with the reason; an item already in the
    cart gets the quantities added, capped at the stock available. With `dry_run=True` nothing
    is written and the result carries the planned ChangeSet."""
    order = services.customer_order(user, public_id)
    if order is None:
        return None
    lines = [line for sub in order.vendor_orders.all() for line in sub.lines.all()]
    items = _items(lines)
    with transaction.atomic():
        cart = (
            cart_services.get_cart(user=user)
            if dry_run
            else cart_services.get_or_create_cart(user=user)
        )
        if cart is None:
            cart = Cart(user=user)  # a dry run before the first cart: nothing in it yet
        return fill_cart(cart, items, country=cart.country_hint, ctx=ctx, dry_run=dry_run)
