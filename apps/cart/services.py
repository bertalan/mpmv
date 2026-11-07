"""Cart services (spec 07 §7.2, ORD.1): get a cart, add and change lines, merge a guest cart
into the user's cart on sign-in, and group lines by vendor.

The mutating services take `dry_run` and return a ChangeSet, like every service (spec 01). A
cart is a shopper's working list, not a financial or security fact, so they write no AuditLog:
the order placed from it is audited and snapshotted (ORD.2, ORD.4). Request handling (which cart
belongs to this visitor) is in `session.py`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Product, Variant
from apps.core.changeset import ChangeSet

from . import engine
from .models import Cart, CartLine

CART_MODEL = "cart.cart"
LINE_MODEL = "cart.cartline"


def _cart_snapshot(cart: Cart) -> dict[str, Any]:
    return {
        "user": cart.user_id,
        "session_key": cart.session_key,
        "language": cart.language,
        "country_hint": cart.country_hint,
    }


def _line_snapshot(line: CartLine) -> dict[str, Any]:
    return {
        "cart": str(line.cart.public_id),
        "product": line.product_id,
        "variant": line.variant_id,
        "quantity": line.quantity,
        "added_price_net": line.added_price_net,
    }


def _owner(user: User | None, session_key: str | None) -> dict[str, Any]:
    if (user is None) == (not session_key):
        raise ValueError("A cart belongs to exactly one of a user or a guest session key")
    if user is not None:
        return {"user": user, "session_key": ""}
    return {"user": None, "session_key": session_key}


# --- Getting a cart


def get_cart(*, user: User | None = None, session_key: str | None = None) -> Cart | None:
    """The cart of `user`, or the guest cart of `session_key`; None if there is none yet."""
    return Cart.objects.filter(**_owner(user, session_key)).first()


def get_or_create_cart(
    *,
    user: User | None = None,
    session_key: str | None = None,
    language: str | None = None,
    country_hint: str = "",
) -> Cart:
    """The cart of `user` or of the guest `session_key`, created on first use.

    `language` and `country_hint` apply only when the cart is created.
    """
    owner = _owner(user, session_key)
    defaults: dict[str, Any] = {"country_hint": country_hint}
    if language:
        defaults["language"] = language
    cart = Cart.objects.filter(**owner).first()
    if cart is not None:
        return cart
    cart = Cart(**owner, **defaults)
    cart.full_clean(validate_unique=False, validate_constraints=False)  # normalizes the country
    try:
        with transaction.atomic():
            cart.save()
    except IntegrityError:  # created meanwhile by a concurrent request of the same visitor
        return Cart.objects.get(**owner)
    return cart


def _touch(cart_id: int) -> None:
    Cart.objects.filter(pk=cart_id).update(updated_at=timezone.now())


# --- Lines


def _purchase_limit(product: Product, variant: Variant | None) -> int:
    """The most units of this item a line may hold; ValidationError if it cannot be bought."""
    if variant is not None and variant.product_id != product.pk:
        raise ValidationError({"variant": "This variant does not belong to the product."})
    error = engine.purchase_error(
        product_status=product.status,
        sales_mode=product.sales_mode,
        vendor_status=product.vendor.status,
        is_variable=product.kind == Product.Kind.VARIABLE,
        has_variant=variant is not None,
    )
    if error is not None:
        raise ValidationError(error)
    stock = variant if variant is not None else product
    available = engine.available_quantity(product.stock_policy, stock.stock_qty, stock.stock_status)
    return engine.line_limit(available)


def _unit_price(product: Product, variant: Variant | None) -> Decimal | None:
    override = variant.price_net_override if variant is not None else None
    return engine.display_unit_price(product.price_net, product.sales_mode, override)


def _check_quantity(quantity: int, limit: int) -> None:
    error = engine.quantity_error(quantity, limit=limit)
    if error is not None:
        raise ValidationError({"quantity": error})


def add_to_cart(
    cart: Cart,
    product: Product,
    *,
    variant: Variant | None = None,
    quantity: int = 1,
    dry_run: bool = False,
) -> ChangeSet:
    """Add `quantity` units of the product (or of its variant) to the cart.

    Adding an item already in the cart adds up the quantities on its line. Refused with a
    ValidationError when the item cannot be bought or the total exceeds the available stock.
    The line remembers the current display price (`added_price_net`).
    """
    if quantity < 1:
        raise ValidationError({"quantity": "The quantity must be at least 1."})
    with transaction.atomic():
        Cart.objects.select_for_update().get(pk=cart.pk)  # one change at a time per cart
        product = Product.objects.select_related("vendor").get(pk=product.pk)
        variant = None if variant is None else Variant.objects.get(pk=variant.pk)
        limit = _purchase_limit(product, variant)
        price = _unit_price(product, variant)
        line = CartLine.objects.filter(cart=cart, product=product, variant=variant).first()
        changeset = ChangeSet()
        if line is None:
            line = CartLine(
                cart=cart,
                product=product,
                variant=variant,
                quantity=quantity,
                added_price_net=price,
            )
            _check_quantity(line.quantity, limit)
            changeset.create(LINE_MODEL, str(line.public_id), _line_snapshot(line))
        else:
            before = _line_snapshot(line)
            line.quantity += quantity
            line.added_price_net = price
            _check_quantity(line.quantity, limit)
            changeset.update(LINE_MODEL, str(line.public_id), before, _line_snapshot(line))
        if not dry_run:
            line.save()
            _touch(cart.pk)
    return changeset


def set_line_quantity(line: CartLine, quantity: int, *, dry_run: bool = False) -> ChangeSet:
    """Set the line's quantity; 0 removes the line. Refused above the available stock."""
    if quantity <= 0:
        return remove_line(line, dry_run=dry_run)
    with transaction.atomic():
        line = CartLine.objects.select_for_update().get(pk=line.pk)
        product = Product.objects.select_related("vendor").get(pk=line.product_id)
        limit = _purchase_limit(product, line.variant)
        _check_quantity(quantity, limit)
        changeset = ChangeSet()
        before = _line_snapshot(line)
        line.quantity = quantity
        changeset.update(LINE_MODEL, str(line.public_id), before, _line_snapshot(line))
        if not dry_run and not changeset.is_empty:
            line.save(update_fields=["quantity", "updated_at"])
            _touch(line.cart_id)
    return changeset


def remove_line(line: CartLine, *, dry_run: bool = False) -> ChangeSet:
    """Remove the line from its cart."""
    changeset = ChangeSet()
    changeset.delete(LINE_MODEL, str(line.public_id), _line_snapshot(line))
    if not dry_run:
        with transaction.atomic():
            CartLine.objects.filter(pk=line.pk).delete()
            _touch(line.cart_id)
    return changeset


# --- Merge on sign-in


@dataclass
class CartMerge:
    """The user's cart after the merge (or the cart it would be, on a dry run), the changes,
    and the guest lines whose quantity was capped or that were dropped (tell the customer)."""

    cart: Cart
    changeset: ChangeSet
    adjusted: list[engine.MergedLine] = field(default_factory=list)


def _key(line: CartLine) -> engine.LineKey:
    return engine.LineKey(product_id=line.product_id, variant_id=line.variant_id)


def _state(line: CartLine) -> engine.LineState:
    return engine.LineState(
        key=_key(line), quantity=line.quantity, added_price_net=line.added_price_net
    )


def _limit_or_zero(line: CartLine) -> int:
    try:
        return _purchase_limit(line.product, line.variant)
    except ValidationError:
        return 0


def merge_carts(guest: Cart, user: User, *, dry_run: bool = False) -> CartMerge | None:
    """Merge the guest cart into `user`'s cart, when the visitor signs in.

    The line rule is `engine.merge_lines`: same lines add up, guest-only lines move, every
    merged line is capped at its stock, and lines that can no longer be bought are dropped. If
    the user has no cart yet, the guest cart becomes theirs. Otherwise the guest cart is deleted
    once its lines are merged, and its language and country hint (the visitor's current ones)
    replace the user cart's.

    Returns None, changing nothing, when the guest cart is gone or no longer a guest cart by
    the time it is locked: a concurrent sign-in on the same session (a double-submitted form)
    merged it first.
    """
    if guest.user_id is not None or not guest.session_key:
        raise ValueError("Only a guest cart can be merged into a user's cart")
    with transaction.atomic():
        # Lock both carts in one statement, in pk order, so concurrent merges cannot deadlock;
        # then check again what the rows are now that nobody else can change them.
        locked = list(
            Cart.objects.select_for_update().filter(Q(pk=guest.pk) | Q(user=user)).order_by("pk")
        )
        current = next((cart for cart in locked if cart.pk == guest.pk), None)
        if current is None or current.user_id is not None or not current.session_key:
            return None
        guest = current
        target = next((cart for cart in locked if cart.pk != guest.pk), None)
        guest_lines = list(guest.lines.select_related("product__vendor", "variant"))
        target_lines = (
            [] if target is None else list(target.lines.select_related("product", "variant"))
        )
        limits = {_key(line): _limit_or_zero(line) for line in guest_lines}
        plan = engine.merge_lines(
            [_state(line) for line in target_lines],
            [_state(line) for line in guest_lines],
            limits,
        )
        if target is None:
            outcome = _adopt(guest, user, guest_lines, plan)
        else:
            outcome = _merge_into(target, guest, target_lines, guest_lines, plan)
        changeset, apply = outcome
        if not dry_run:
            apply()
        # On a dry run the cart shows the planned values, unsaved.
        cart = target if target is not None else guest
        return CartMerge(cart=cart, changeset=changeset, adjusted=[m for m in plan if m.adjusted])


type Apply = Callable[[], None]  # applies a planned merge


def _adopt(
    guest: Cart, user: User, lines: list[CartLine], plan: list[engine.MergedLine]
) -> tuple[ChangeSet, Apply]:
    """The user has no cart: the guest cart becomes theirs, its lines capped or dropped."""
    changeset = ChangeSet()
    before = _cart_snapshot(guest)
    guest.user, guest.session_key = user, ""
    changeset.update(CART_MODEL, str(guest.public_id), before, _cart_snapshot(guest))
    by_key = {_key(line): line for line in lines}
    updates, deletes = _line_changes(changeset, by_key, plan)

    def apply() -> None:
        guest.save(update_fields=["user", "session_key", "updated_at"])
        for line in deletes:
            line.delete()
        for line in updates:
            line.save(update_fields=["quantity", "added_price_net", "updated_at"])

    return changeset, apply


def _merge_into(
    target: Cart,
    guest: Cart,
    target_lines: list[CartLine],
    guest_lines: list[CartLine],
    plan: list[engine.MergedLine],
) -> tuple[ChangeSet, Apply]:
    """Merge the guest lines into the user's cart, then delete the guest cart."""
    changeset = ChangeSet()
    before = _cart_snapshot(target)
    if guest.language:
        target.language = guest.language
    if guest.country_hint:
        target.country_hint = guest.country_hint
    changeset.update(CART_MODEL, str(target.public_id), before, _cart_snapshot(target))

    existing = {_key(line): line for line in target_lines}
    updates, deletes = _line_changes(changeset, existing, plan)
    guest_by_key = {_key(line): line for line in guest_lines}
    creates = []
    for merged in plan:
        if merged.key in existing or merged.quantity == 0:
            continue
        source = guest_by_key[merged.key]
        line = CartLine(
            cart=target,
            product_id=source.product_id,
            variant_id=source.variant_id,
            quantity=merged.quantity,
            added_price_net=merged.added_price_net,
        )
        changeset.create(LINE_MODEL, str(line.public_id), _line_snapshot(line))
        creates.append(line)
    changeset.delete(CART_MODEL, str(guest.public_id), _cart_snapshot(guest))

    def apply() -> None:
        guest.delete()  # its lines go with it, before the moved ones are created
        for line in deletes:
            line.delete()
        for line in updates:
            line.save(update_fields=["quantity", "added_price_net", "updated_at"])
        CartLine.objects.bulk_create(creates)
        target.save(update_fields=["language", "country_hint", "updated_at"])

    return changeset, apply


def _line_changes(
    changeset: ChangeSet, lines: dict[engine.LineKey, CartLine], plan: list[engine.MergedLine]
) -> tuple[list[CartLine], list[CartLine]]:
    """Plan the updates and deletions of the `lines` that the merge touches."""
    updates, deletes = [], []
    for merged in plan:
        line = lines.get(merged.key)
        if line is None:
            continue
        before = _line_snapshot(line)
        if merged.quantity == 0:
            changeset.delete(LINE_MODEL, str(line.public_id), before)
            deletes.append(line)
            continue
        line.quantity, line.added_price_net = merged.quantity, merged.added_price_net
        changeset.update(LINE_MODEL, str(line.public_id), before, _line_snapshot(line))
        updates.append(line)
    return updates, deletes


# --- Packages per vendor


def vendor_packages(cart: Cart) -> list[engine.VendorPackage[CartLine]]:
    """The cart's lines grouped by vendor, with current display prices and goods totals.

    This is the per-vendor split that shipping, tax and the order build on (SHP.4, ORD.3).
    """
    lines = cart.lines.select_related("product", "variant").order_by("created_at", "id")
    return engine.group_by_vendor(
        engine.PricedLine(
            item=line,
            vendor_id=line.product.vendor_id,
            quantity=line.quantity,
            unit_price_net=_unit_price(line.product, line.variant),
            added_price_net=line.added_price_net,
        )
        for line in lines
    )
