"""VEN.1: Vendor and VendorTranslation models against the real database."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.vendors import engine
from apps.vendors.models import Vendor, VendorTranslation
from apps.vendors.tests.factories import VendorFactory, VendorTranslationFactory

pytestmark = pytest.mark.django_db


def test_new_vendor_defaults() -> None:
    vendor = Vendor.objects.create(  # model defaults under test: no factory values
        legal_name="Vendor A S.r.l.",
        display_name="Vendor A",
        country="IT",
        contact_email="vendor-a@example.com",
    )
    assert vendor.status == Vendor.Status.APPLICANT
    assert vendor.sales_mode_default == Vendor.SalesMode.BUY
    assert vendor.enquiries_enabled is False
    assert vendor.legacy_wp_user_id is None
    assert vendor.registered_street == ""
    assert vendor.legal_form == ""
    assert vendor.vies_status == Vendor.ViesStatus.UNCHECKED
    assert vendor.share_capital is None
    assert vendor.public_id is not None
    assert str(vendor) == "Vendor A"


def test_status_choices_match_the_state_machine() -> None:
    assert tuple(Vendor.Status.values) == engine.STATUSES


def test_slug_is_generated_from_the_display_name() -> None:
    assert (
        VendorFactory.create(display_name="Vendor B Mountain Shop").slug == "vendor-b-mountain-shop"
    )


def test_generated_slugs_are_unique() -> None:
    first = VendorFactory.create(display_name="Vendor C")
    second = VendorFactory.create(display_name="Vendor C")
    assert first.slug == "vendor-c"
    assert second.slug == "vendor-c-2"


def test_an_explicit_slug_is_kept() -> None:
    assert VendorFactory.create(slug="custom-store").slug == "custom-store"


def test_slug_does_not_change_when_the_display_name_changes() -> None:
    vendor = VendorFactory.create(display_name="Vendor D")
    vendor.display_name = "Vendor D Renamed"
    vendor.save()
    vendor.refresh_from_db()
    assert vendor.slug == "vendor-d"


def test_duplicate_explicit_slug_is_refused_by_the_database() -> None:
    VendorFactory.create(slug="same")
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorFactory.create(slug="same")


def test_status_outside_the_choices_is_refused_by_the_database() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorFactory.create(status="deleted")


def test_sales_mode_outside_the_choices_is_refused_by_the_database() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorFactory.create(sales_mode_default="auction")


def test_country_is_normalized_to_iso_alpha2() -> None:
    vendor = VendorFactory.build(country="el")
    vendor.full_clean()
    assert vendor.country == "GR"


def test_invalid_registered_address_fails_validation() -> None:
    vendor = VendorFactory.build(registered_country="XX")
    with pytest.raises(ValidationError) as excinfo:
        vendor.full_clean()
    assert "registered_country" in excinfo.value.message_dict


def test_legacy_wp_user_id_is_optional_but_unique() -> None:
    VendorFactory.create(legacy_wp_user_id=101)
    VendorFactory.create_batch(2)  # null twice is fine
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorFactory.create(legacy_wp_user_id=101)


# --- Translations and policies


def test_translations_hold_tagline_description_and_policies() -> None:
    vendor = VendorFactory.create()
    VendorTranslationFactory.create(
        vendor=vendor,
        language="en",
        tagline="Outdoor gear",
        description="Family business.",
        shipping_policy="Ships in 2 days.",
        refund_policy="Refunds within 14 days.",
        cancellation_policy="Cancel before shipping.",
    )
    # Model defaults under test: blank texts and policies, so no factory values here.
    VendorTranslation.objects.create(vendor=vendor, language="it", tagline="Attrezzatura")
    assert sorted(vendor.translations.values_list("language", flat=True)) == ["en", "it"]
    it = vendor.translations.get(language="it")
    assert it.description == ""
    assert it.shipping_policy == ""


def test_one_translation_per_vendor_and_language() -> None:
    vendor = VendorFactory.create(languages=["en"])
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorTranslationFactory.create(vendor=vendor, language="en")


def test_same_language_is_allowed_for_different_vendors() -> None:
    VendorTranslationFactory.create_batch(2, language="en")
    assert VendorTranslation.objects.filter(language="en").count() == 2


def test_translation_language_must_be_a_configured_language() -> None:
    translation = VendorTranslationFactory.build(vendor=VendorFactory.create(), language="pt")
    with pytest.raises(ValidationError) as excinfo:
        translation.full_clean()
    assert "language" in excinfo.value.message_dict


def test_translations_are_deleted_with_the_vendor() -> None:
    vendor = VendorFactory.create(languages=["en"])
    vendor.delete()
    assert VendorTranslation.objects.count() == 0
