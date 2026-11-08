"""factory_boy factories for carts (ORD.1). Synthetic values only.

`CartFactory` makes a guest cart (a session key, no user); pass `user=` and `session_key=""`
for a signed-in customer's cart. `CartLineFactory` puts one unit of a new simple product in a
new guest cart; the product is a draft unless the test asks for a purchasable one (published,
`buy`, active vendor), because the services refuse anything else.
"""

from decimal import Decimal

import factory
from factory.django import DjangoModelFactory

from apps.cart.models import Cart, CartLine
from apps.catalog.tests.factories import ProductFactory


class CartFactory(DjangoModelFactory[Cart]):
    class Meta:
        model = Cart

    session_key = factory.Sequence(lambda n: f"session{n:028d}")
    language = "en"


class CartLineFactory(DjangoModelFactory[CartLine]):
    class Meta:
        model = CartLine

    cart = factory.SubFactory(CartFactory)
    product = factory.SubFactory(ProductFactory)
    variant = None
    quantity = 1
    added_price_net = Decimal("10.00")
