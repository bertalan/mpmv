"""CAT.1: product and variant translations reuse the shared TranslationModel (spec 09 §9.2)."""

from apps.catalog.models import ProductTranslation, VariantTranslation
from apps.catalog.tests.factories import ProductTranslationFactory, VariantTranslationFactory
from apps.core.checks import check_translation_model
from apps.core.models import TranslationModel


def test_product_translation_follows_the_translation_contract() -> None:
    assert issubclass(ProductTranslation, TranslationModel)
    assert ProductTranslation.translation_of == "product"
    assert ProductTranslation.translated_fields == (
        "title",
        "short_description",
        "description",
        "seo_title",
        "seo_description",
    )
    assert check_translation_model(ProductTranslation) == []


def test_the_slug_is_derived_and_not_a_translated_text() -> None:
    # {title}-{lang}-{sku} (CAT.5): it changes with the title, it is not a source text.
    assert "slug" not in ProductTranslation.translated_fields


def test_variant_translation_follows_the_translation_contract() -> None:
    assert issubclass(VariantTranslation, TranslationModel)
    assert VariantTranslation.translation_of == "variant"
    assert VariantTranslation.translated_fields == ("label", "description")
    assert check_translation_model(VariantTranslation) == []


def test_new_translations_start_as_machine_drafts() -> None:
    assert ProductTranslationFactory.build().status == "machine"
    assert VariantTranslationFactory.build().status == "machine"
