"""I18N.2: pure translation rules (spec 09 §9.2), no database.

`source_hash` fingerprints the EN source fields a translation was made from; `resolve` picks the
published translation for a language or falls back explicitly to EN, with a flag for the notice.
"""

from dataclasses import dataclass

import pytest
from django.test import override_settings

from apps.core.translations import (
    MACHINE,
    PUBLISHED,
    REVIEWED,
    STATUSES,
    resolve,
    source_hash,
    source_language,
)

pytestmark = pytest.mark.usefixtures("default_languages")


@dataclass
class Row:
    language: str
    status: str
    text: str = ""


# --- Status workflow


def test_statuses_follow_the_spec_workflow_order() -> None:
    assert STATUSES == (MACHINE, REVIEWED, PUBLISHED) == ("machine", "reviewed", "published")


def test_source_language_is_the_primary_language() -> None:
    assert source_language() == "en"


@override_settings(LANGUAGE_CODE="it")
def test_source_language_is_configuration() -> None:
    assert source_language() == "it"


# --- source_hash


def test_source_hash_is_a_sha256_hex_digest() -> None:
    digest = source_hash({"tagline": "Outdoor gear"})
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")


def test_source_hash_is_stable_and_ignores_field_order() -> None:
    first = source_hash({"tagline": "Outdoor gear", "description": "Family business."})
    second = source_hash({"description": "Family business.", "tagline": "Outdoor gear"})
    assert first == second


@pytest.mark.parametrize(
    "changed",
    [
        {"tagline": "Outdoor gear!", "description": "Family business."},
        {"tagline": "Outdoor gear", "description": "Family business. "},
        {"tagline": "Outdoor gear", "description": ""},
        # The same text moved to another field is a different source.
        {"tagline": "Family business.", "description": "Outdoor gear"},
    ],
)
def test_any_source_change_changes_the_hash(changed: dict[str, str]) -> None:
    original = {"tagline": "Outdoor gear", "description": "Family business."}
    assert source_hash(changed) != source_hash(original)


def test_field_boundaries_are_part_of_the_hash() -> None:
    assert source_hash({"a": "x|y", "b": ""}) != source_hash({"a": "x", "b": "y"})


# --- resolve: published translation, else explicit EN fallback


def test_published_translation_in_the_requested_language_wins() -> None:
    it = Row("it", PUBLISHED)
    resolved = resolve([Row("en", PUBLISHED), it], "it")
    assert resolved.translation is it
    assert resolved.language == "it"
    assert resolved.requested == "it"
    assert resolved.is_fallback is False
    assert resolved.is_missing is False


@pytest.mark.parametrize("status", [MACHINE, REVIEWED])
def test_unpublished_translation_falls_back_to_en_with_a_flag(status: str) -> None:
    en = Row("en", PUBLISHED)
    resolved = resolve([en, Row("it", status)], "it")
    assert resolved.translation is en
    assert resolved.language == "en"
    assert resolved.requested == "it"
    assert resolved.is_fallback is True


def test_missing_translation_falls_back_to_en() -> None:
    en = Row("en", PUBLISHED)
    resolved = resolve([en, Row("it", PUBLISHED)], "de")
    assert resolved.translation is en
    assert resolved.is_fallback is True


def test_en_requested_is_never_a_fallback() -> None:
    en = Row("en", PUBLISHED)
    resolved = resolve([Row("it", PUBLISHED), en], "en")
    assert resolved.translation is en
    assert resolved.is_fallback is False


def test_nothing_published_resolves_to_missing() -> None:
    resolved = resolve([Row("en", MACHINE), Row("it", REVIEWED)], "it")
    assert resolved.translation is None
    assert resolved.language is None
    assert resolved.is_missing is True
    assert resolved.is_fallback is False


def test_no_rows_resolves_to_missing() -> None:
    assert resolve([], "en").is_missing is True


def test_fallback_never_goes_to_a_language_other_than_en() -> None:
    resolved = resolve([Row("de", PUBLISHED)], "it")
    assert resolved.is_missing is True


@override_settings(
    LANGUAGES=[
        ("en", "English"),
        ("it", "Italiano"),
        ("de", "Deutsch"),
        ("fr", "Français"),
        ("es", "Español"),
        ("pt", "Português"),
    ]
)
def test_a_language_added_in_settings_resolves_without_code_changes() -> None:
    pt = Row("pt", PUBLISHED)
    assert resolve([Row("en", PUBLISHED), pt], "pt").translation is pt
