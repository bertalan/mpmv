"""I18N.15: model language defaults are the callable, so migrations do not freeze the code."""

from apps.accounts.models import User
from apps.cart.models import Cart
from apps.core.i18n import primary_language
from apps.orders.models import Order


def test_model_language_defaults_are_the_callable_not_a_frozen_code() -> None:
    for field in (
        User._meta.get_field("preferred_language"),
        Cart._meta.get_field("language"),
        Order._meta.get_field("language"),
    ):
        assert field.default is primary_language
