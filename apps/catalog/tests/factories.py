"""factory_boy factories for the catalog (CAT.1).

SKUs are synthetic ("SKU-00001", "VAR-00001") and texts come from the seeded Faker: never real
catalog data.
"""

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import factory
from factory.django import DjangoModelFactory

from apps.catalog.models import Product, ProductTranslation, Variant, VariantTranslation
from apps.vendors.tests.factories import VendorFactory


class ProductFactory(DjangoModelFactory[Product]):
    """A simple draft product of a new vendor, with a net price and a weight.

    `languages=["en", "it"]` also creates one translation per language.
    """

    class Meta:
        model = Product
        skip_postgeneration_save = True

    vendor = factory.SubFactory(VendorFactory)
    sku = factory.Sequence(lambda n: f"SKU-{n:05d}")
    kind = Product.Kind.SIMPLE
    price_net = Decimal("10.00")
    weight_kg = Decimal("0.500")

    @factory.post_generation
    def languages(
        obj: Product, create: bool, extracted: Iterable[str] | None, **kwargs: Any
    ) -> None:
        if create and extracted:
            for language in extracted:
                ProductTranslationFactory.create(product=obj, language=language)


class ProductTranslationFactory(DjangoModelFactory[ProductTranslation]):
    class Meta:
        model = ProductTranslation

    product = factory.SubFactory(ProductFactory)
    language = "en"
    title = factory.Faker("sentence", nb_words=3)
    short_description = factory.Faker("sentence")
    description = factory.Faker("paragraph")


class VariantFactory(DjangoModelFactory[Variant]):
    """A variant of a new variable product; its vendor is always the product's vendor."""

    class Meta:
        model = Variant

    product = factory.SubFactory(ProductFactory, kind=Product.Kind.VARIABLE)
    sku = factory.Sequence(lambda n: f"VAR-{n:05d}")
    position = factory.Sequence(lambda n: n)


class VariantTranslationFactory(DjangoModelFactory[VariantTranslation]):
    class Meta:
        model = VariantTranslation

    variant = factory.SubFactory(VariantFactory)
    language = "en"
    label = factory.Faker("word")
