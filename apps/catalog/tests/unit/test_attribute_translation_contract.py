"""CAT.3: attribute names and value labels reuse the shared TranslationModel (spec 09 §9.2)."""

from apps.catalog.models import AttributeTranslation, AttributeValueTranslation
from apps.catalog.tests.factories import (
    AttributeTranslationFactory,
    AttributeValueTranslationFactory,
)
from apps.core.checks import check_translation_model
from apps.core.models import TranslationModel


def test_attribute_translation_follows_the_translation_contract() -> None:
    assert issubclass(AttributeTranslation, TranslationModel)
    assert AttributeTranslation.translation_of == "attribute"
    assert AttributeTranslation.translated_fields == ("name",)
    assert check_translation_model(AttributeTranslation) == []


def test_attribute_value_translation_follows_the_translation_contract() -> None:
    assert issubclass(AttributeValueTranslation, TranslationModel)
    assert AttributeValueTranslation.translation_of == "value"
    assert AttributeValueTranslation.translated_fields == ("label",)
    assert check_translation_model(AttributeValueTranslation) == []


def test_new_names_and_labels_start_as_machine_drafts() -> None:
    assert AttributeTranslationFactory.build().status == "machine"
    assert AttributeValueTranslationFactory.build().status == "machine"
