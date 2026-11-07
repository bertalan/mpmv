"""I18N.2: translation workflow on VendorTranslation against the real database (spec 09 §9.2).

The EN row is the source. Every other language stores the `source_hash` of the EN texts it was
translated or reviewed from; when the EN texts change, reviewed and published translations go
back to "needs review" (the `machine` status). Visitors get the published translation for their
language, or EN with an explicit fallback flag.
"""

import pytest
from django.db import IntegrityError, transaction
from django.test import override_settings

from apps.core.translations import MACHINE, PUBLISHED, REVIEWED, source_hash
from apps.vendors.models import Vendor, VendorTranslation
from apps.vendors.tests.factories import VendorFactory, VendorTranslationFactory

pytestmark = pytest.mark.django_db

LANGUAGES_PLUS_FRENCH = [
    ("en", "English"),
    ("it", "Italiano"),
    ("de", "Deutsch"),
    ("fr", "Français"),
]


def refreshed(row: VendorTranslation) -> VendorTranslation:
    row.refresh_from_db()
    return row


def en_row(vendor: Vendor, **kwargs: str) -> VendorTranslation:
    return VendorTranslationFactory.create(vendor=vendor, language="en", status=PUBLISHED, **kwargs)


# --- source_hash


def test_the_en_row_stores_the_hash_of_its_own_texts() -> None:
    en = en_row(VendorFactory.create(), tagline="Outdoor gear")
    assert en.source_hash == source_hash(en.source_values())
    assert refreshed(en).source_hash == source_hash(en.source_values())


def test_a_new_translation_records_the_current_en_hash() -> None:
    vendor = VendorFactory.create()
    en = en_row(vendor)
    it = VendorTranslationFactory.create(vendor=vendor, language="it")
    assert refreshed(it).source_hash == en.source_hash
    assert it.is_stale is False


def test_a_translation_without_an_en_row_is_not_stale() -> None:
    it = VendorTranslationFactory.create(language="it", status=PUBLISHED)
    assert it.source_hash == ""
    assert it.is_stale is False


def test_source_hash_is_not_editable_in_forms() -> None:
    assert not VendorTranslation._meta.get_field("source_hash").editable


# --- EN change → other languages go back to "needs review"


def test_en_text_change_sends_reviewed_and_published_translations_back_to_review() -> None:
    vendor = VendorFactory.create()
    en = en_row(vendor, tagline="Outdoor gear")
    it = VendorTranslationFactory.create(vendor=vendor, language="it", status=PUBLISHED)
    de = VendorTranslationFactory.create(vendor=vendor, language="de", status=REVIEWED)
    old_hash = en.source_hash

    en.tagline = "Outdoor and climbing gear"
    en.save()

    assert refreshed(en).status == PUBLISHED  # the source itself stays as it is
    assert en.source_hash != old_hash
    for row in (refreshed(it), refreshed(de)):
        assert row.status == MACHINE
        assert row.source_hash == old_hash  # kept: it tells the reviewer what it was made from
        assert row.is_stale is True


def test_saving_en_without_a_text_change_keeps_translations_published() -> None:
    vendor = VendorFactory.create()
    en = en_row(vendor)
    it = VendorTranslationFactory.create(vendor=vendor, language="it", status=PUBLISHED)
    en.save()
    assert refreshed(it).status == PUBLISHED
    assert it.is_stale is False


def test_save_with_update_fields_still_refreshes_the_en_hash() -> None:
    vendor = VendorFactory.create()
    en = en_row(vendor, tagline="Outdoor gear")
    it = VendorTranslationFactory.create(vendor=vendor, language="it", status=PUBLISHED)
    en.tagline = "Climbing gear"
    en.save(update_fields=["tagline"])
    assert refreshed(en).source_hash == source_hash(en.source_values())
    assert refreshed(it).status == MACHINE


def test_an_en_change_does_not_touch_other_vendors() -> None:
    first, second = VendorFactory.create_batch(2)
    en = en_row(first)
    en_row(second)
    other = VendorTranslationFactory.create(vendor=second, language="it", status=PUBLISHED)
    en.description = "New description."
    en.save()
    assert refreshed(other).status == PUBLISHED


def test_mark_stale_translations_reports_how_many_went_back_to_review() -> None:
    vendor = VendorFactory.create()
    en = en_row(vendor)
    VendorTranslationFactory.create(vendor=vendor, language="it", status=PUBLISHED)
    VendorTranslationFactory.create(vendor=vendor, language="de", status=MACHINE)
    VendorTranslation.objects.filter(language="en").update(tagline="Changed behind save()")
    en.refresh_from_db()
    en.save()  # recomputes the hash from the stored texts
    assert en.mark_stale_translations() == 0  # already done by save()
    assert sorted(vendor.translations.values_list("language", "status")) == [
        ("de", MACHINE),
        ("en", PUBLISHED),
        ("it", MACHINE),
    ]


def test_reviewing_a_stale_translation_records_the_new_en_hash() -> None:
    vendor = VendorFactory.create()
    en = en_row(vendor, tagline="Outdoor gear")
    it = VendorTranslationFactory.create(vendor=vendor, language="it", status=PUBLISHED)
    en.tagline = "Climbing gear"
    en.save()

    it = refreshed(it)
    it.tagline = "Attrezzatura da arrampicata"
    it.status = REVIEWED
    it.save()

    assert refreshed(it).source_hash == en.source_hash
    assert it.is_stale is False


def test_editing_a_stale_draft_without_review_keeps_it_stale() -> None:
    vendor = VendorFactory.create()
    en = en_row(vendor, tagline="Outdoor gear")
    it = VendorTranslationFactory.create(vendor=vendor, language="it", status=PUBLISHED)
    en.tagline = "Climbing gear"
    en.save()

    it = refreshed(it)
    it.tagline = "Bozza"
    it.save()
    assert refreshed(it).is_stale is True


# --- Status


def test_status_outside_the_workflow_is_refused_by_the_database() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorTranslationFactory.create(status="draft")


# --- Fallback resolver


def test_vendor_translation_resolves_the_published_language() -> None:
    vendor = VendorFactory.create()
    en_row(vendor)
    it = VendorTranslationFactory.create(vendor=vendor, language="it", status=PUBLISHED)
    resolved = vendor.translation("it")
    assert resolved.translation == it
    assert resolved.is_fallback is False


def test_vendor_translation_falls_back_to_en_when_not_published() -> None:
    vendor = VendorFactory.create()
    en = en_row(vendor)
    VendorTranslationFactory.create(vendor=vendor, language="it", status=REVIEWED)
    resolved = vendor.translation("it")
    assert resolved.translation == en
    assert resolved.language == "en"
    assert resolved.is_fallback is True


def test_vendor_translation_falls_back_after_an_en_change() -> None:
    vendor = VendorFactory.create()
    en = en_row(vendor, tagline="Outdoor gear")
    VendorTranslationFactory.create(vendor=vendor, language="de", status=PUBLISHED)
    en.tagline = "Climbing gear"
    en.save()
    assert vendor.translation("de").is_fallback is True


def test_vendor_without_published_texts_resolves_to_missing() -> None:
    vendor = VendorFactory.create(languages=["en", "it"])  # machine drafts only
    assert vendor.translation("it").is_missing is True


# --- Languages are configuration (coherence check 2, finding 1)


@override_settings(LANGUAGES=LANGUAGES_PLUS_FRENCH)
def test_a_language_added_in_settings_needs_no_migration() -> None:
    vendor = VendorFactory.create()
    en_row(vendor)
    fr = VendorTranslationFactory.build(vendor=vendor, language="fr", status=PUBLISHED)
    fr.full_clean()
    fr.save()
    assert vendor.translation("fr").translation == fr
