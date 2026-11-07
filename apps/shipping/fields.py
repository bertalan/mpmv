"""Shipping model fields."""

from typing import Any

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError

from apps.core.countries import InvalidCountry
from apps.core.fields import CountryField

from . import engine


class ZoneCountryField(CountryField):
    """A CountryField that also accepts the "rest of the world" marker `*` (engine.WORLD)."""

    def to_python(self, value: Any) -> Any:
        if isinstance(value, str) and value.strip() == engine.WORLD:
            return engine.WORLD
        return super().to_python(value)


class ZoneCountriesField(ArrayField[Any, Any]):
    """The countries of a zone: ISO alpha-2 codes, normalized on validation, or `["*"]`.

    ArrayField validates the items of a list without converting them, so this field runs
    each item through its base field: "el" is stored as "GR" whether it comes from the admin
    or from code that calls `full_clean()`.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("base_field", ZoneCountryField())
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> Any:
        value = super().to_python(value)
        if not isinstance(value, list):
            return value
        try:
            return [
                engine.normalize_zone_country(item) if isinstance(item, str) else item
                for item in value
            ]
        except InvalidCountry as exc:
            raise ValidationError(str(exc), code="invalid_country") from exc
