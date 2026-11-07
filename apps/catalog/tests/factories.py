"""factory_boy factories for the catalog (CAT.1, CAT.2).

SKUs are synthetic ("SKU-00001", "VAR-00001"), slugs and codes are numbered ("category-1",
"taxonomy-1") and texts come from the seeded Faker: never real catalog data.

Tree nodes (categories, terms) are created through treebeard: pass `parent=` for a child,
otherwise the node is added as the last root.
"""

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import factory
from factory.django import DjangoModelFactory

from apps.catalog.models import (
    Brand,
    BrandTranslation,
    Category,
    CategoryTranslation,
    Product,
    ProductTranslation,
    Taxonomy,
    TaxonomyTranslation,
    Term,
    TermTranslation,
    Variant,
    VariantTranslation,
)
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


class TreeNodeCreation:
    """Factory mixin: adds the node with treebeard (`add_root`, or `add_child` of the `parent`
    declaration), which sets its path; a built node has no path."""

    @classmethod
    def _build(cls, model_class: type[Any], *args: Any, **kwargs: Any) -> Any:
        kwargs.pop("parent", None)
        return model_class(*args, **kwargs)

    @classmethod
    def _create(cls, model_class: type[Any], *args: Any, **kwargs: Any) -> Any:
        parent = kwargs.pop("parent", None)
        return model_class.add_root(**kwargs) if parent is None else parent.add_child(**kwargs)


class CategoryFactory(TreeNodeCreation, DjangoModelFactory[Category]):
    """A root category, or a child of `parent`; `languages=[...]` adds one text per language."""

    class Meta:
        model = Category
        skip_postgeneration_save = True

    parent = None

    @factory.post_generation
    def languages(
        obj: Category, create: bool, extracted: Iterable[str] | None, **kwargs: Any
    ) -> None:
        if create and extracted:
            for language in extracted:
                CategoryTranslationFactory.create(category=obj, language=language)


class CategoryTranslationFactory(DjangoModelFactory[CategoryTranslation]):
    class Meta:
        model = CategoryTranslation

    category = factory.SubFactory(CategoryFactory)
    language = "en"
    name = factory.Faker("word")
    slug = factory.Sequence(lambda n: f"category-{n}")


class TaxonomyFactory(DjangoModelFactory[Taxonomy]):
    """A hierarchical, filterable taxonomy with no extra term attributes."""

    class Meta:
        model = Taxonomy

    code = factory.Sequence(lambda n: f"taxonomy_{n}")
    hierarchical = True
    filterable = True


class TaxonomyTranslationFactory(DjangoModelFactory[TaxonomyTranslation]):
    class Meta:
        model = TaxonomyTranslation

    taxonomy = factory.SubFactory(TaxonomyFactory)
    language = "en"
    name = factory.Faker("word")
    slug = factory.Sequence(lambda n: f"taxonomy-{n}")


class TermFactory(TreeNodeCreation, DjangoModelFactory[Term]):
    """A top-level term of a new taxonomy, or a child of `parent` in the parent's taxonomy."""

    class Meta:
        model = Term

    parent = None
    taxonomy = factory.Maybe(
        "parent",
        yes_declaration=factory.SelfAttribute("parent.taxonomy"),
        no_declaration=factory.SubFactory(TaxonomyFactory),
    )


class TermTranslationFactory(DjangoModelFactory[TermTranslation]):
    class Meta:
        model = TermTranslation

    term = factory.SubFactory(TermFactory)
    language = "en"
    name = factory.Faker("word")
    slug = factory.Sequence(lambda n: f"term-{n}")


class BrandFactory(DjangoModelFactory[Brand]):
    class Meta:
        model = Brand

    name = factory.Sequence(lambda n: f"Brand {n}")


class BrandTranslationFactory(DjangoModelFactory[BrandTranslation]):
    class Meta:
        model = BrandTranslation

    brand = factory.SubFactory(BrandFactory)
    language = "en"
    slug = factory.Sequence(lambda n: f"brand-{n}")
    description = factory.Faker("sentence")
