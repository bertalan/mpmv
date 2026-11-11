"""factory_boy factories for the catalog (CAT.1, CAT.2, CAT.3).

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
from wagtail.models import Collection

from apps.catalog.models import (
    Attribute,
    AttributeTranslation,
    AttributeValue,
    AttributeValueTranslation,
    Brand,
    BrandBatch,
    BrandTranslation,
    Category,
    CategoryTranslation,
    Manufacturer,
    Product,
    ProductAttribute,
    ProductTranslation,
    Redirect,
    SkuAlias,
    SlugHistory,
    Taxonomy,
    TaxonomyTranslation,
    Term,
    TermTranslation,
    Variant,
    VariantOption,
    VariantTranslation,
)
from apps.core.models import MediaImage
from apps.core.tests.factories import MediaImageFactory, SvgCollectionFactory
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


class SlugHistoryFactory(DjangoModelFactory[SlugHistory]):
    """An old slug of a product (CAT.5); the product has no translation unless given one."""

    class Meta:
        model = SlugHistory

    product = factory.SubFactory(ProductFactory)
    language = "en"
    slug = factory.Sequence(lambda n: f"old-product-{n}-en")


class RedirectFactory(DjangoModelFactory[Redirect]):
    """A 301 from an old path to a path on this site (CAT.6); written around the services, so
    nothing is validated or audited."""

    class Meta:
        model = Redirect

    source_path = factory.Sequence(lambda n: f"/old-page-{n}/")
    target_kind = Redirect.Target.PATH
    target_path = "/"


class VariantFactory(DjangoModelFactory[Variant]):
    """A variant of a new variable product; its vendor is always the product's vendor."""

    class Meta:
        model = Variant

    product = factory.SubFactory(ProductFactory, kind=Product.Kind.VARIABLE)
    sku = factory.Sequence(lambda n: f"VAR-{n:05d}")
    position = factory.Sequence(lambda n: n)


class SkuAliasFactory(DjangoModelFactory[SkuAlias]):
    """An old SKU of a product. Real aliases are written by the database on a rename (CAT.13);
    this inserts one directly, e.g. to stage an import history."""

    class Meta:
        model = SkuAlias

    product = factory.SubFactory(ProductFactory)
    vendor = factory.SelfAttribute("product.vendor")
    sku = factory.Sequence(lambda n: f"OLD-{n:05d}")
    sku_key = factory.LazyAttribute(lambda o: o.sku.casefold())


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


class BrandBatchFactory(DjangoModelFactory[BrandBatch]):
    """The brands of one import run, pending review (CAT.20)."""

    class Meta:
        model = BrandBatch

    vendor = factory.SubFactory(VendorFactory)
    source = factory.Sequence(lambda n: f"import run {n}")


class ManufacturerFactory(DjangoModelFactory[Manufacturer]):
    class Meta:
        model = Manufacturer

    legal_name = factory.Sequence(lambda n: f"Maker {n} Ltd")


class BrandTranslationFactory(DjangoModelFactory[BrandTranslation]):
    class Meta:
        model = BrandTranslation

    brand = factory.SubFactory(BrandFactory)
    language = "en"
    slug = factory.Sequence(lambda n: f"brand-{n}")
    description = factory.Faker("sentence")


class AttributeFactory(DjangoModelFactory[Attribute]):
    """A choice attribute ("attribute_1"); pass `kind=` and `unit=` for a number attribute."""

    class Meta:
        model = Attribute

    code = factory.Sequence(lambda n: f"attribute_{n}")
    kind = Attribute.Kind.CHOICE


class AttributeTranslationFactory(DjangoModelFactory[AttributeTranslation]):
    class Meta:
        model = AttributeTranslation

    attribute = factory.SubFactory(AttributeFactory)
    language = "en"
    name = factory.Faker("word")


class AttributeValueFactory(DjangoModelFactory[AttributeValue]):
    """A value of a new choice attribute."""

    class Meta:
        model = AttributeValue

    attribute = factory.SubFactory(AttributeFactory)
    value = factory.Sequence(lambda n: f"value-{n}")
    position = factory.Sequence(lambda n: n)


class AttributeValueTranslationFactory(DjangoModelFactory[AttributeValueTranslation]):
    class Meta:
        model = AttributeValueTranslation

    value = factory.SubFactory(AttributeValueFactory)
    language = "en"
    label = factory.Faker("word")


class ProductAttributeFactory(DjangoModelFactory[ProductAttribute]):
    """A choice attribute of a new simple product, without values; add them with `values.set`."""

    class Meta:
        model = ProductAttribute

    product = factory.SubFactory(ProductFactory)
    attribute = factory.SubFactory(AttributeFactory)
    position = factory.Sequence(lambda n: n)


class VariantOptionFactory(DjangoModelFactory[VariantOption]):
    """The value a variant takes on one axis; the attribute is always the value's."""

    class Meta:
        model = VariantOption

    variant = factory.SubFactory(VariantFactory)
    value = factory.SubFactory(AttributeValueFactory)
    attribute = factory.SelfAttribute("value.attribute")


PUBLISHED = AttributeValueTranslation.Status.PUBLISHED


def attribute_with_values(
    code: str,
    names: dict[str, str],
    values: dict[str, dict[str, str]],
    *,
    kind: str = Attribute.Kind.CHOICE,
    unit: str = "",
) -> Attribute:
    """An attribute with published names and, for a choice attribute, values in the given order.

    `names` and each value's labels map a language to its text, e.g. {"en": "Red", "it": "Rosso"}.
    """
    attribute = AttributeFactory.create(code=code, kind=kind, unit=unit)
    for language, name in names.items():
        AttributeTranslationFactory.create(
            attribute=attribute, language=language, name=name, status=PUBLISHED
        )
    for position, (value, labels) in enumerate(values.items()):
        row = AttributeValueFactory.create(attribute=attribute, value=value, position=position)
        for language, label in labels.items():
            AttributeValueTranslationFactory.create(
                value=row, language=language, label=label, status=PUBLISHED
            )
    return attribute


def clothing_attributes() -> dict[str, Attribute]:
    """Synthetic size, color, material, bag_width (cm) and author attributes.

    Sizes have EN labels only (an IT page falls back to them); colors are also in IT.
    """
    return {
        "size": attribute_with_values(
            "size",
            {"en": "Size", "it": "Taglia"},
            {"s": {"en": "S"}, "m": {"en": "M"}, "l": {"en": "L"}},
        ),
        "color": attribute_with_values(
            "color",
            {"en": "Color", "it": "Colore"},
            {"red": {"en": "Red", "it": "Rosso"}, "blue": {"en": "Blue", "it": "Blu"}},
        ),
        "material": attribute_with_values(
            "material",
            {"en": "Material"},
            {"cotton": {"en": "Cotton", "it": "Cotone"}, "linen": {"en": "Linen"}},
        ),
        "bag_width": attribute_with_values(
            "bag_width", {"en": "Width"}, {}, kind=Attribute.Kind.NUMBER, unit="cm"
        ),
        "author": attribute_with_values("author", {"en": "Author"}, {}, kind=Attribute.Kind.TEXT),
    }


def brand_logo_image(**fields: Any) -> MediaImage:
    """A classified image in the platform logos collection, the only place a brand logo may
    come from (CAT.20 3/3)."""
    collection = Collection.objects.filter(svg_policy__isnull=False).order_by("path").first()
    if collection is None:
        collection = SvgCollectionFactory.create().collection
    return MediaImageFactory.create(collection=collection, **fields)
