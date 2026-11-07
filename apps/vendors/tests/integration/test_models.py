"""VEN.1: Vendor and VendorTranslation models against the real database."""

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.vendors import engine
from apps.vendors.models import Vendor, VendorTranslation

pytestmark = pytest.mark.django_db


def make_vendor(**fields: Any) -> Vendor:
    data: dict[str, Any] = {
        "legal_name": "Vendor A S.r.l.",
        "display_name": "Vendor A",
        "country": "IT",
        "contact_email": "vendor-a@example.com",
    }
    data.update(fields)
    return Vendor.objects.create(**data)


def test_new_vendor_defaults() -> None:
    vendor = make_vendor()
    assert vendor.status == Vendor.Status.APPLICANT
    assert vendor.sales_mode_default == Vendor.SalesMode.BUY
    assert vendor.enquiries_enabled is False
    assert vendor.legacy_wp_user_id is None
    assert vendor.registered_address == {}
    assert vendor.public_id is not None
    assert str(vendor) == "Vendor A"


def test_status_choices_match_the_state_machine() -> None:
    assert tuple(Vendor.Status.values) == engine.STATUSES


def test_slug_is_generated_from_the_display_name() -> None:
    assert make_vendor(display_name="Vendor B Mountain Shop").slug == "vendor-b-mountain-shop"


def test_generated_slugs_are_unique() -> None:
    first = make_vendor(display_name="Vendor C")
    second = make_vendor(display_name="Vendor C", contact_email="c2@example.com")
    assert first.slug == "vendor-c"
    assert second.slug == "vendor-c-2"


def test_an_explicit_slug_is_kept() -> None:
    assert make_vendor(slug="custom-store").slug == "custom-store"


def test_slug_does_not_change_when_the_display_name_changes() -> None:
    vendor = make_vendor(display_name="Vendor D")
    vendor.display_name = "Vendor D Renamed"
    vendor.save()
    vendor.refresh_from_db()
    assert vendor.slug == "vendor-d"


def test_duplicate_explicit_slug_is_refused_by_the_database() -> None:
    make_vendor(slug="same")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_vendor(slug="same", contact_email="other@example.com")


def test_status_outside_the_choices_is_refused_by_the_database() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_vendor(status="deleted")


def test_sales_mode_outside_the_choices_is_refused_by_the_database() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_vendor(sales_mode_default="auction")


def test_country_is_normalized_to_iso_alpha2() -> None:
    vendor = Vendor(
        legal_name="Vendor E Ltd",
        display_name="Vendor E",
        country="el",
        contact_email="vendor-e@example.com",
    )
    vendor.full_clean()
    assert vendor.country == "GR"


def test_invalid_registered_address_fails_validation() -> None:
    vendor = Vendor(
        legal_name="Vendor A S.r.l.",
        display_name="Vendor A",
        country="IT",
        contact_email="vendor-a@example.com",
        registered_address={"street": "Via Example 1"},
    )
    with pytest.raises(ValidationError) as excinfo:
        vendor.full_clean()
    assert "registered_address" in excinfo.value.message_dict


def test_legacy_wp_user_id_is_optional_but_unique() -> None:
    make_vendor(legacy_wp_user_id=101)
    make_vendor(contact_email="b@example.com")  # null twice is fine
    make_vendor(contact_email="c@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_vendor(contact_email="d@example.com", legacy_wp_user_id=101)


# --- Translations and policies


def test_translations_hold_tagline_description_and_policies() -> None:
    vendor = make_vendor()
    VendorTranslation.objects.create(
        vendor=vendor,
        language="en",
        tagline="Outdoor gear",
        description="Family business.",
        shipping_policy="Ships in 2 days.",
        refund_policy="Refunds within 14 days.",
        cancellation_policy="Cancel before shipping.",
    )
    VendorTranslation.objects.create(vendor=vendor, language="it", tagline="Attrezzatura")
    assert sorted(vendor.translations.values_list("language", flat=True)) == ["en", "it"]
    it = vendor.translations.get(language="it")
    assert it.description == ""
    assert it.shipping_policy == ""


def test_one_translation_per_vendor_and_language() -> None:
    vendor = make_vendor()
    VendorTranslation.objects.create(vendor=vendor, language="en")
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorTranslation.objects.create(vendor=vendor, language="en")


def test_same_language_is_allowed_for_different_vendors() -> None:
    VendorTranslation.objects.create(vendor=make_vendor(), language="en")
    other = make_vendor(display_name="Vendor B", contact_email="b@example.com")
    VendorTranslation.objects.create(vendor=other, language="en")
    assert VendorTranslation.objects.filter(language="en").count() == 2


def test_translation_language_must_be_a_configured_language() -> None:
    translation = VendorTranslation(vendor=make_vendor(), language="fr")
    with pytest.raises(ValidationError) as excinfo:
        translation.full_clean()
    assert "language" in excinfo.value.message_dict


def test_translations_are_deleted_with_the_vendor() -> None:
    vendor = make_vendor()
    VendorTranslation.objects.create(vendor=vendor, language="en")
    vendor.delete()
    assert VendorTranslation.objects.count() == 0
