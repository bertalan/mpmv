"""ORD.1: the database rules of carts and cart lines."""

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.tests.factories import UserFactory
from apps.cart.models import Cart, CartLine
from apps.cart.tests.factories import CartFactory, CartLineFactory
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, VariantFactory

pytestmark = pytest.mark.django_db


def test_a_cart_belongs_to_a_user_or_to_a_guest_session_never_both_or_neither() -> None:
    user = UserFactory.create()
    assert CartFactory.create(user=user, session_key="").user == user
    assert CartFactory.create(session_key="guest-session").user is None
    with transaction.atomic(), pytest.raises(IntegrityError):
        Cart.objects.create(session_key="")
    with transaction.atomic(), pytest.raises(IntegrityError):
        Cart.objects.create(user=UserFactory.create(), session_key="other-session")


def test_a_user_has_one_cart_and_a_session_one_guest_cart() -> None:
    user = UserFactory.create()
    CartFactory.create(user=user, session_key="")
    with transaction.atomic(), pytest.raises(IntegrityError):
        CartFactory.create(user=user, session_key="")
    CartFactory.create(session_key="same-session")
    with transaction.atomic(), pytest.raises(IntegrityError):
        CartFactory.create(session_key="same-session")


def test_one_line_per_product_and_variant_even_without_a_variant() -> None:
    line = CartLineFactory.create()
    with transaction.atomic(), pytest.raises(IntegrityError):
        CartLineFactory.create(cart=line.cart, product=line.product, variant=None)

    variant = VariantFactory.create()
    CartLineFactory.create(cart=line.cart, product=variant.product, variant=variant)
    other = VariantFactory.create(product=variant.product)
    CartLineFactory.create(cart=line.cart, product=variant.product, variant=other)
    with transaction.atomic(), pytest.raises(IntegrityError):
        CartLineFactory.create(cart=line.cart, product=variant.product, variant=variant)


def test_a_line_holds_at_least_one_unit() -> None:
    with transaction.atomic(), pytest.raises(IntegrityError):
        CartLineFactory.create(quantity=0)


def test_lines_reference_the_internal_product_identity_not_the_sku() -> None:
    """CAT.13: the vendor SKU is a label; renaming it leaves carts untouched."""
    line = CartLineFactory.create(product=ProductFactory.create(sku="OLD-LABEL"))
    Product.objects.filter(pk=line.product_id).update(sku="NEW-LABEL")
    line.refresh_from_db()
    assert line.product.sku == "NEW-LABEL"
    assert {f.name for f in CartLine._meta.get_fields()}.isdisjoint({"sku"})


def test_deleting_a_cart_deletes_its_lines() -> None:
    line = CartLineFactory.create()
    line.cart.delete()
    assert not CartLine.objects.exists()
