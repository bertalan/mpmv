"""CAT.3: typed attributes, translated values and variant options against the real database.

The database enforces what must hold for imports and raw SQL too: one row per attribute value,
one value per variant axis, and a variant option whose value belongs to its attribute.
"""

from collections.abc import Callable
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.catalog.models import Attribute, AttributeValue, Product, VariantOption
from apps.catalog.tests.factories import (
    AttributeFactory,
    AttributeValueFactory,
    ProductAttributeFactory,
    ProductFactory,
    VariantFactory,
    VariantOptionFactory,
    clothing_attributes,
)

pytestmark = pytest.mark.django_db


def _refused(make: Callable[[], object]) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make()


def test_an_attribute_value_is_unique_within_its_attribute() -> None:
    first = AttributeValueFactory.create(value="red")
    AttributeValueFactory.create(value="red")  # another attribute: fine
    _refused(lambda: AttributeValueFactory.create(attribute=first.attribute, value="red"))


def test_attribute_codes_are_unique() -> None:
    AttributeFactory.create(code="size")
    _refused(lambda: AttributeFactory.create(code="size"))


def test_the_attribute_kind_is_checked_by_the_database() -> None:
    _refused(lambda: AttributeFactory.create(kind="date"))


def test_only_a_number_attribute_has_a_unit() -> None:
    AttributeFactory.create(kind=Attribute.Kind.NUMBER, unit="cm")
    _refused(lambda: AttributeFactory.create(kind=Attribute.Kind.CHOICE, unit="cm"))


def test_only_a_choice_attribute_has_values() -> None:
    number = AttributeFactory.create(kind=Attribute.Kind.NUMBER, unit="cm")
    with pytest.raises(ValidationError):
        AttributeValueFactory.build(attribute=number, value="wide").full_clean()


def test_values_are_ordered_by_position() -> None:
    attributes = clothing_attributes()
    assert list(attributes["size"].values.values_list("value", flat=True)) == ["s", "m", "l"]


def test_a_product_has_an_attribute_once() -> None:
    row = ProductAttributeFactory.create()
    _refused(lambda: ProductAttributeFactory.create(product=row.product, attribute=row.attribute))


def test_a_product_attribute_stores_typed_values() -> None:
    attributes = clothing_attributes()
    product = ProductFactory.create()
    material = ProductAttributeFactory.create(product=product, attribute=attributes["material"])
    material.values.set(attributes["material"].values.all())
    width = ProductAttributeFactory.create(
        product=product, attribute=attributes["bag_width"], numeric_value=Decimal("30.50")
    )
    width.refresh_from_db()
    assert width.numeric_value == Decimal("30.50")
    assert sorted(material.values.values_list("value", flat=True)) == ["cotton", "linen"]


def test_a_variant_takes_one_value_per_attribute() -> None:
    attributes = clothing_attributes()
    variant = VariantFactory.create()
    red, blue = attributes["color"].values.all()[:2]
    VariantOptionFactory.create(variant=variant, value=red)
    _refused(lambda: VariantOptionFactory.create(variant=variant, value=blue))
    assert list(variant.options.all()) == [red]


def test_an_option_value_must_belong_to_its_attribute() -> None:
    attributes = clothing_attributes()
    red = AttributeValue.objects.get(attribute=attributes["color"], value="red")
    _refused(lambda: VariantOptionFactory.create(value=red, attribute=attributes["size"]))


def test_an_option_copies_its_variants_vendor() -> None:
    option = VariantOptionFactory.create()
    assert option.variant.vendor_id == option.variant.product.vendor_id
    assert VariantOption.objects.for_vendor(option.variant.vendor).get() == option


def test_a_value_used_by_a_variant_cannot_be_deleted() -> None:
    option = VariantOptionFactory.create()
    with pytest.raises(ProtectedError):
        option.value.delete()


def test_deleting_a_product_deletes_its_attributes_and_options() -> None:
    attributes = clothing_attributes()
    product = ProductFactory.create(kind=Product.Kind.VARIABLE)
    ProductAttributeFactory.create(product=product, attribute=attributes["size"])
    variant = VariantFactory.create(product=product)
    VariantOptionFactory.create(variant=variant, value=attributes["size"].values.first())
    product.delete()
    assert not VariantOption.objects.exists()
    assert attributes["size"].values.count() == 3


def test_value_labels_fall_back_to_english() -> None:
    attributes = clothing_attributes()
    red = attributes["color"].values.get(value="red")
    small = attributes["size"].values.get(value="s")
    rosso = red.translation("it").translation
    assert rosso is not None
    assert rosso.label == "Rosso"
    resolved = small.translation("it")
    assert resolved.is_fallback
    assert resolved.translation is not None
    assert resolved.translation.label == "S"
    colore = attributes["color"].translation("it").translation
    assert colore is not None
    assert colore.name == "Colore"
