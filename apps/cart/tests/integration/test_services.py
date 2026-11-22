"""ORD.1: cart services (add, change, remove, merge on login, packages per vendor)."""

from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.cart import services
from apps.cart.models import Cart, CartLine
from apps.cart.tests.factories import CartFactory, CartLineFactory
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, VariantFactory
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.i18n import primary_language
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import other_language

# Items in a cart need a consumer price (TAX.12, #757): an operator, rates, tax profiles.
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]


def active_vendor() -> Vendor:
    return VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)


def sellable(vendor: Vendor | None = None, **fields: Any) -> Product:
    """A published `buy` product of an active vendor, 10 in stock, 10.00 net."""
    values: dict[str, Any] = {
        "vendor": vendor or active_vendor(),
        "status": Product.Status.PUBLISHED,
        "stock_qty": 10,
        "price_net": Decimal("10.00"),
        **fields,
    }
    return ProductFactory.create(**values)


def guest_cart(key: str = "guest-session") -> Cart:
    return CartFactory.create(session_key=key)


def quantities(cart: Cart) -> dict[tuple[int, int | None], int]:
    return {(line.product_id, line.variant_id): line.quantity for line in cart.lines.all()}


def merge(guest: Cart, user: User, *, dry_run: bool = False) -> services.CartMerge:
    """A merge that must happen (the guest cart is still a guest cart)."""
    outcome = services.merge_carts(guest, user, dry_run=dry_run)
    assert outcome is not None
    return outcome


# --- Getting a cart


def test_get_or_create_cart_for_a_user_and_for_a_guest_session() -> None:
    user = UserFactory.create()
    mine = services.get_or_create_cart(user=user)
    assert (mine.user, mine.session_key) == (user, "")
    assert services.get_or_create_cart(user=user) == mine

    guest = services.get_or_create_cart(
        session_key="abc", language=other_language(), country_hint="de"
    )
    assert (guest.user, guest.session_key, guest.language, guest.country_hint) == (
        None,
        "abc",
        other_language(),
        "DE",
    )
    assert services.get_cart(session_key="abc") == guest
    assert services.get_cart(session_key="nope") is None


def test_get_or_create_cart_dry_run_writes_nothing_and_describes_the_cart() -> None:
    user = UserFactory.create()
    changes = services.get_or_create_cart(
        user=user, language=other_language(), country_hint="de", dry_run=True
    )
    assert isinstance(changes, ChangeSet)
    assert Cart.objects.count() == 0
    [change] = changes.changes
    assert (change.op, change.model) == ("create", "cart.cart")
    assert change.after is not None
    assert (change.after["user"], change.after["language"], change.after["country_hint"]) == (
        user.pk,
        other_language(),
        "DE",
    )


def test_get_or_create_cart_dry_run_is_empty_when_the_cart_exists() -> None:
    user = UserFactory.create()
    services.get_or_create_cart(user=user)
    changes = services.get_or_create_cart(user=user, dry_run=True)
    assert changes.changes == []
    assert Cart.objects.count() == 1


def test_a_cart_is_for_a_user_or_a_session_not_both() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        services.get_or_create_cart(user=UserFactory.create(), session_key="abc")
    with pytest.raises(ValueError, match="exactly one"):
        services.get_or_create_cart()


# --- Adding and changing lines


def test_adding_a_product_records_the_display_price_and_adding_again_adds_up() -> None:
    cart = guest_cart()
    product = sellable(price_net=Decimal("12.34"))

    changes = services.add_to_cart(cart, product, quantity=2)
    assert changes.summary() == {"create": 1, "update": 0, "delete": 0}
    line = CartLine.objects.get(cart=cart)
    assert (line.product_id, line.variant_id, line.quantity) == (product.pk, None, 2)
    assert line.added_price_net == Decimal("12.34")

    services.add_to_cart(cart, product, quantity=3)
    assert quantities(cart) == {(product.pk, None): 5}


def test_a_variant_line_uses_the_variant_price_and_stock() -> None:
    cart = guest_cart()
    product = sellable(kind=Product.Kind.VARIABLE)
    variant = VariantFactory.create(
        product=product, price_net_override=Decimal("15.00"), stock_qty=2
    )

    services.add_to_cart(cart, product, variant=variant, quantity=2)
    line = CartLine.objects.get(cart=cart)
    assert (line.variant_id, line.added_price_net) == (variant.pk, Decimal("15.00"))
    with pytest.raises(ValidationError, match="Only 2 available"):
        services.add_to_cart(cart, product, variant=variant)


def test_adding_more_than_the_stock_is_refused() -> None:
    cart = guest_cart()
    product = sellable(stock_qty=3)
    services.add_to_cart(cart, product, quantity=2)
    with pytest.raises(ValidationError, match="Only 3 available"):
        services.add_to_cart(cart, product, quantity=2)
    assert quantities(cart) == {(product.pk, None): 2}


def test_backorders_are_not_limited_by_stock() -> None:
    cart = guest_cart()
    product = sellable(
        stock_qty=0,
        stock_policy=Product.StockPolicy.ALWAYS_OPEN,
        stock_status=Product.StockStatus.ON_BACKORDER,
    )
    services.add_to_cart(cart, product, quantity=40)
    assert quantities(cart) == {(product.pk, None): 40}


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"status": Product.Status.DRAFT}, "not for sale"),
        ({"status": Product.Status.RETIRED}, "not for sale"),
        ({"sales_mode": Product.SalesMode.ON_REQUEST}, "on request"),
        ({"kind": Product.Kind.VARIABLE}, "Choose a variant"),
    ],
)
def test_products_that_cannot_be_bought_are_refused(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        services.add_to_cart(guest_cart(), sellable(**fields))
    assert not CartLine.objects.exists()


def test_products_of_a_vendor_that_is_not_active_are_refused() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    with pytest.raises(ValidationError, match="not selling"):
        services.add_to_cart(guest_cart(), sellable(vendor))


def test_a_variant_of_another_product_is_refused() -> None:
    product = sellable(kind=Product.Kind.VARIABLE)
    stranger = VariantFactory.create()
    with pytest.raises(ValidationError, match="does not belong"):
        services.add_to_cart(guest_cart(), product, variant=stranger)


def test_dry_run_reports_the_change_without_applying_it() -> None:
    cart = guest_cart()
    changes = services.add_to_cart(cart, sellable(), quantity=1, dry_run=True)
    assert changes.summary() == {"create": 1, "update": 0, "delete": 0}
    assert not CartLine.objects.exists()


def test_set_line_quantity_changes_or_removes_the_line() -> None:
    cart = guest_cart()
    product = sellable(stock_qty=4)
    services.add_to_cart(cart, product, quantity=1)
    line = CartLine.objects.get()

    services.set_line_quantity(line, 4)
    assert quantities(cart) == {(product.pk, None): 4}
    with pytest.raises(ValidationError, match="Only 4 available"):
        services.set_line_quantity(line, 5)

    changes = services.set_line_quantity(line, 0)
    assert changes.summary() == {"create": 0, "update": 0, "delete": 1}
    assert not CartLine.objects.exists()


def test_remove_line() -> None:
    line = CartLineFactory.create(product=sellable())
    services.remove_line(line, dry_run=True)
    assert CartLine.objects.exists()
    services.remove_line(line)
    assert not CartLine.objects.exists()


# --- Two vendors in one cart


def test_two_vendors_in_one_cart_make_two_packages() -> None:
    vendor_a, vendor_b = active_vendor(), active_vendor()
    cart = guest_cart()
    a1 = sellable(vendor_a, price_net=Decimal("10.00"))
    b1 = sellable(vendor_b, price_net=Decimal("4.99"))
    a2 = sellable(vendor_a, price_net=Decimal("1.50"))
    services.add_to_cart(cart, a1, quantity=1)
    services.add_to_cart(cart, b1, quantity=3)
    services.add_to_cart(cart, a2, quantity=2)

    packages = services.vendor_packages(cart)
    assert [p.vendor_id for p in packages] == [vendor_a.pk, vendor_b.pk]
    assert [[line.item.product_id for line in p.lines] for p in packages] == [
        [a1.pk, a2.pk],
        [b1.pk],
    ]
    assert [p.goods_net for p in packages] == [Decimal("13.00"), Decimal("14.97")]


def test_packages_show_current_prices_and_flag_changes() -> None:
    cart = guest_cart()
    product = sellable(price_net=Decimal("10.00"))
    services.add_to_cart(cart, product)
    Product.objects.filter(pk=product.pk).update(price_net=Decimal("11.00"))

    (package,) = services.vendor_packages(cart)
    (line,) = package.lines
    assert (line.unit_price_net, line.added_price_net, line.price_changed) == (
        Decimal("11.00"),
        Decimal("10.00"),
        True,
    )


# --- Merge on login


def test_guest_cart_becomes_the_users_cart_when_the_user_has_none() -> None:
    user = UserFactory.create()
    guest = guest_cart()
    product = sellable()
    services.add_to_cart(guest, product, quantity=2)

    outcome = merge(guest, user)
    cart = Cart.objects.get()
    assert outcome.cart == cart
    assert (cart.pk, cart.user, cart.session_key) == (guest.pk, user, "")
    assert quantities(cart) == {(product.pk, None): 2}
    assert outcome.adjusted == []


def test_merge_adds_up_same_lines_moves_the_others_and_deletes_the_guest_cart() -> None:
    user = UserFactory.create()
    mine = services.get_or_create_cart(user=user)
    vendor_a, vendor_b = active_vendor(), active_vendor()
    both = sellable(vendor_a, stock_qty=10)
    only_mine = sellable(vendor_a)
    only_guest = sellable(vendor_b)
    services.add_to_cart(mine, both, quantity=2)
    services.add_to_cart(mine, only_mine, quantity=1)
    guest = guest_cart()
    services.add_to_cart(guest, both, quantity=3)
    services.add_to_cart(guest, only_guest, quantity=4)

    outcome = merge(guest, user)

    assert outcome.cart == mine
    assert quantities(mine) == {
        (both.pk, None): 5,
        (only_mine.pk, None): 1,
        (only_guest.pk, None): 4,
    }
    assert not Cart.objects.filter(pk=guest.pk).exists()
    assert Cart.objects.count() == 1
    assert [p.vendor_id for p in services.vendor_packages(mine)] == [vendor_a.pk, vendor_b.pk]


def test_merge_caps_quantities_at_the_stock_and_reports_the_adjustment() -> None:
    user = UserFactory.create()
    mine = services.get_or_create_cart(user=user)
    product = sellable(stock_qty=5)
    services.add_to_cart(mine, product, quantity=4)
    guest = guest_cart()
    services.add_to_cart(guest, product, quantity=3)

    outcome = merge(guest, user)

    assert quantities(mine) == {(product.pk, None): 5}
    (adjusted,) = outcome.adjusted
    assert (adjusted.key.product_id, adjusted.requested, adjusted.quantity) == (product.pk, 7, 5)


def test_merge_drops_guest_lines_that_can_no_longer_be_bought() -> None:
    user = UserFactory.create()
    product = sellable()
    guest = guest_cart()
    services.add_to_cart(guest, product, quantity=1)
    Product.objects.filter(pk=product.pk).update(status=Product.Status.RETIRED)

    outcome = merge(guest, user)

    assert outcome.cart.user == user
    assert not outcome.cart.lines.exists()
    assert [(a.key.product_id, a.quantity) for a in outcome.adjusted] == [(product.pk, 0)]


def test_merge_takes_the_guest_language_and_country_hint() -> None:
    user = UserFactory.create()
    mine = services.get_or_create_cart(user=user, language=primary_language())
    guest = services.get_or_create_cart(
        session_key="s", language=other_language(), country_hint="FR"
    )
    services.add_to_cart(guest, sellable())

    services.merge_carts(guest, user)
    mine.refresh_from_db()
    assert (mine.language, mine.country_hint) == (other_language(), "FR")


def test_merge_dry_run_changes_nothing() -> None:
    user = UserFactory.create()
    mine = services.get_or_create_cart(user=user)
    product = sellable()
    services.add_to_cart(mine, product, quantity=1)
    guest = guest_cart()
    services.add_to_cart(guest, product, quantity=1)

    outcome = merge(guest, user, dry_run=True)

    assert outcome.changeset.summary() == {"create": 0, "update": 1, "delete": 1}
    assert quantities(mine) == {(product.pk, None): 1}
    assert Cart.objects.filter(pk=guest.pk).exists()


def test_merging_a_guest_cart_that_was_already_adopted_is_a_no_op() -> None:
    # A double-submitted sign-in: both requests found the guest cart before either merged it.
    user = UserFactory.create()
    product = sellable()
    guest = guest_cart()
    services.add_to_cart(guest, product, quantity=2)
    stale = Cart.objects.get(pk=guest.pk)

    first = services.merge_carts(guest, user)
    second = services.merge_carts(stale, user)

    assert first is not None
    assert second is None
    cart = Cart.objects.get()
    assert (cart.pk, cart.user, cart.session_key) == (guest.pk, user, "")
    assert quantities(cart) == {(product.pk, None): 2}


def test_merging_a_guest_cart_that_was_already_merged_is_a_no_op() -> None:
    user = UserFactory.create()
    mine = services.get_or_create_cart(user=user)
    product = sellable()
    services.add_to_cart(mine, product, quantity=1)
    guest = guest_cart()
    services.add_to_cart(guest, product, quantity=2)
    stale = Cart.objects.get(pk=guest.pk)

    first = services.merge_carts(guest, user)
    second = services.merge_carts(stale, user)

    assert first is not None
    assert second is None
    assert list(Cart.objects.all()) == [mine]
    assert quantities(mine) == {(product.pk, None): 3}


def test_a_users_own_cart_is_not_a_guest_cart() -> None:
    user = UserFactory.create()
    mine = services.get_or_create_cart(user=user)
    with pytest.raises(ValueError, match="guest"):
        services.merge_carts(mine, UserFactory.create())


CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)


def test_add_many_adds_new_lines_and_adds_up_existing_ones() -> None:
    cart = guest_cart()
    first, second = sellable(), sellable()
    services.add_to_cart(cart, first, quantity=2)
    changes = services.add_many_to_cart(
        cart, [(first, None, 3), (second, None, 1), (second, None, 1)], ctx=CTX
    )
    assert quantities(cart) == {(first.pk, None): 5, (second.pk, None): 2}
    assert len(changes.changes) == 2


def test_add_many_matches_single_adds() -> None:
    single, many = guest_cart("a"), guest_cart("b")
    product = sellable()
    variant_product = sellable()
    services.add_to_cart(single, product, quantity=2)
    services.add_to_cart(single, variant_product, quantity=1)
    services.add_many_to_cart(many, [(product, None, 2), (variant_product, None, 1)], ctx=CTX)
    assert sorted(quantities(single).values()) == sorted(quantities(many).values())


def test_add_many_dry_run_writes_nothing() -> None:
    cart = guest_cart()
    changes = services.add_many_to_cart(cart, [(sellable(), None, 1)], ctx=CTX, dry_run=True)
    assert len(changes.changes) == 1
    assert not CartLine.objects.filter(cart=cart).exists()
    assert not AuditLog.objects.filter(action=services.BULK_ADD_ACTION).exists()


def test_add_many_is_all_or_nothing() -> None:
    cart = guest_cart()
    ok = sellable()
    over = sellable(stock_qty=1)
    with pytest.raises(ValidationError):
        services.add_many_to_cart(cart, [(ok, None, 1), (over, None, 5)], ctx=CTX)
    assert not CartLine.objects.filter(cart=cart).exists()


def test_add_many_writes_one_audit_entry_without_personal_data() -> None:
    cart = guest_cart()
    first, second = sellable(), sellable()
    services.add_many_to_cart(cart, [(first, None, 2), (second, None, 1)], ctx=CTX)
    entry = AuditLog.objects.get(action=services.BULK_ADD_ACTION)
    assert entry.target_key == str(cart.public_id)
    assert entry.after == {
        "lines": [
            {"product": str(first.public_id), "variant": None, "quantity": 2},
            {"product": str(second.public_id), "variant": None, "quantity": 1},
        ]
    }
