"""Model fields and the abstract base model, checked without a database."""

import uuid
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import models
from django.test import override_settings

from apps.core.fields import CountryField, LanguageField, MoneyField
from apps.core.i18n import language_choices
from apps.core.models import BaseModel, TranslationModel


def test_money_field_is_decimal_12_2() -> None:
    field = MoneyField()
    assert isinstance(field, models.DecimalField)
    assert (field.max_digits, field.decimal_places) == (12, 2)


def test_money_field_refuses_floats_before_they_reach_the_database() -> None:
    with pytest.raises(TypeError, match="float"):
        MoneyField().get_prep_value(10.1)


def test_money_field_accepts_decimals() -> None:
    assert MoneyField().get_prep_value(Decimal("10.10")) == Decimal("10.10")


def test_country_field_normalizes_and_validates() -> None:
    field = CountryField()
    assert field.max_length == 2
    assert field.clean("el", None) == "GR"
    with pytest.raises(ValidationError):
        field.clean("ZZ", None)


def test_base_model_is_abstract_with_public_id_and_timestamps() -> None:
    assert BaseModel._meta.abstract
    public_id = BaseModel._meta.get_field("public_id")
    assert isinstance(public_id, models.UUIDField)
    assert public_id.unique
    assert not public_id.editable
    assert public_id.default is uuid.uuid4
    assert BaseModel._meta.get_field("created_at").auto_now_add
    assert BaseModel._meta.get_field("updated_at").auto_now


# --- Languages (I18N.2): choices come from settings at runtime, never frozen in migrations

LANGUAGES_PLUS_FRENCH = [
    ("en", "English"),
    ("it", "Italiano"),
    ("de", "Deutsch"),
    ("fr", "Français"),
]


def test_language_field_deconstructs_to_the_callable_not_the_list() -> None:
    _name, path, _args, kwargs = LanguageField().deconstruct()
    assert path == "apps.core.fields.LanguageField"
    assert kwargs["choices"] is language_choices
    assert kwargs["max_length"] == 8


def test_language_field_accepts_only_configured_languages() -> None:
    field = LanguageField()
    assert field.clean("de", None) == "de"
    with pytest.raises(ValidationError):
        field.clean("fr", None)


@override_settings(LANGUAGES=LANGUAGES_PLUS_FRENCH)
def test_a_language_added_in_settings_is_valid_without_a_migration() -> None:
    assert LanguageField().clean("fr", None) == "fr"


def test_translation_model_is_abstract_with_language_status_and_source_hash() -> None:
    assert TranslationModel._meta.abstract
    assert issubclass(TranslationModel, BaseModel)
    assert isinstance(TranslationModel._meta.get_field("language"), LanguageField)
    status = TranslationModel._meta.get_field("status")
    assert status.default == "machine"
    assert [value for value, _label in status.choices] == ["machine", "reviewed", "published"]
    source_hash = TranslationModel._meta.get_field("source_hash")
    assert source_hash.max_length == 64
    assert not source_hash.editable
