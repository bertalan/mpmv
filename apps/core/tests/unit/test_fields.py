"""Model fields and the abstract base model, checked without a database."""

import uuid
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.fields import CountryField, MoneyField
from apps.core.models import BaseModel


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
