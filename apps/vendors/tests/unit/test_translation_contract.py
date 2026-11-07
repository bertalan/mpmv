"""I18N.2: VendorTranslation is built on the shared TranslationModel (spec 09 §9.2)."""

from apps.core.checks import check_translation_model
from apps.core.models import TranslationModel
from apps.vendors.models import VendorTranslation
from apps.vendors.tests.factories import VendorTranslationFactory


def test_vendor_translation_reuses_the_translation_model() -> None:
    assert issubclass(VendorTranslation, TranslationModel)
    assert VendorTranslation.translation_of == "vendor"
    assert VendorTranslation.translated_fields == (
        "tagline",
        "description",
        "shipping_policy",
        "refund_policy",
        "cancellation_policy",
    )
    assert check_translation_model(VendorTranslation) == []


def test_source_values_are_the_translated_fields() -> None:
    translation = VendorTranslationFactory.build(
        tagline="Outdoor gear",
        description="Family business.",
        shipping_policy="Ships in 2 days.",
        refund_policy="Refunds within 14 days.",
        cancellation_policy="Cancel before shipping.",
    )
    assert translation.source_values() == {
        "tagline": "Outdoor gear",
        "description": "Family business.",
        "shipping_policy": "Ships in 2 days.",
        "refund_policy": "Refunds within 14 days.",
        "cancellation_policy": "Cancel before shipping.",
    }


def test_new_translations_start_as_machine_drafts() -> None:
    assert VendorTranslationFactory.build().status == "machine"
