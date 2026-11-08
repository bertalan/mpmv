"""Shipping model fields: arrays whose items are normalized and validated one by one."""

from typing import Any, ClassVar

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.countries import InvalidCountry
from apps.core.fields import CountryField
from apps.core.postcodes import (
    MAX_PATTERN_LENGTH,
    InvalidPostcodePattern,
    normalize_postcode_pattern,
)

from . import engine

QUALIFIER_LENGTH = 3  # "PT:" in front of a pattern or province


class ZoneCountryField(CountryField):
    """A CountryField that also accepts the "rest of the world" marker `*` (engine.WORLD)."""

    def to_python(self, value: Any) -> Any:
        if isinstance(value, str) and value.strip() == engine.WORLD:
            return engine.WORLD
        return super().to_python(value)


class NormalizedArrayField(ArrayField[Any, Any]):
    """An array whose string items run through `normalize` on validation.

    ArrayField validates the items of a list without converting them, so a subclass names the
    normalizer and the errors it raises: "el" is stored as "GR", " bt* " as "BT*", whether the
    value comes from the admin or from code that calls `full_clean()`.
    """

    errors: ClassVar[tuple[type[ValueError], ...]] = ()
    error_code: ClassVar[str] = "invalid"

    def default_base_field(self) -> models.Field[Any, Any]:
        raise NotImplementedError

    def normalize(self, item: str) -> str:
        raise NotImplementedError

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("base_field", self.default_base_field())
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> Any:
        value = super().to_python(value)
        if not isinstance(value, list):
            return value
        try:
            return [self.normalize(item) if isinstance(item, str) else item for item in value]
        except self.errors as exc:
            raise ValidationError(str(exc), code=self.error_code) from exc


class ZoneCountriesField(NormalizedArrayField):
    """The countries of a zone or group: ISO alpha-2 codes, normalized, or `["*"]`."""

    errors = (InvalidCountry,)
    error_code = "invalid_country"

    def default_base_field(self) -> models.Field[Any, Any]:
        return ZoneCountryField()

    def normalize(self, item: str) -> str:
        return engine.normalize_zone_country(item)


class PostcodePatternsField(NormalizedArrayField):
    """Postcode patterns of the shared grammar (`apps.core.postcodes`): `BT*`, `70000..74999`,
    `84600`, each optionally qualified by its country (`PT:9000..9999`)."""

    errors = (InvalidPostcodePattern,)
    error_code = "invalid_postcode_pattern"

    def default_base_field(self) -> models.Field[Any, Any]:
        return models.CharField(max_length=MAX_PATTERN_LENGTH + QUALIFIER_LENGTH)

    def normalize(self, item: str) -> str:
        return normalize_postcode_pattern(item)


class StatesField(NormalizedArrayField):
    """Province / state codes as the checkout collects them (`SS`, `CA`), upper case."""

    errors = (engine.InvalidState,)
    error_code = "invalid_state"

    def default_base_field(self) -> models.Field[Any, Any]:
        return models.CharField(max_length=engine.STATE_MAX_LENGTH + QUALIFIER_LENGTH)

    def normalize(self, item: str) -> str:
        return engine.normalize_state(item)


class CountryGroupCodesField(NormalizedArrayField):
    """Codes of `CountryGroup` rows, lower case; the model checks that they exist."""

    def default_base_field(self) -> models.Field[Any, Any]:
        return models.SlugField(max_length=50)

    def normalize(self, item: str) -> str:
        return item.strip().lower()
