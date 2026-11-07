"""Shared model fields."""

from typing import Any

from django.core.exceptions import ValidationError
from django.db import models

from .countries import InvalidCountry, normalize_country


class MoneyField(models.DecimalField[Any, Any]):
    """Amount in EUR with 2 decimals (`DecimalField(12, 2)`). Floats are refused."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("max_digits", 12)
        kwargs.setdefault("decimal_places", 2)
        super().__init__(*args, **kwargs)

    def get_prep_value(self, value: Any) -> Any:
        if isinstance(value, float):
            raise TypeError("float amounts are not allowed; use Decimal")
        return super().get_prep_value(value)


class CountryField(models.CharField[Any, Any]):
    """ISO 3166-1 alpha-2 country code, normalized to upper case (EL → GR, UK → GB)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("max_length", 2)
        super().__init__(*args, **kwargs)

    def to_python(self, value: Any) -> Any:
        value = super().to_python(value)
        if value in (None, ""):
            return value
        try:
            return normalize_country(value)
        except InvalidCountry as exc:
            raise ValidationError(str(exc), code="invalid_country") from exc
