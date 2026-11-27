"""Model fields of the tax app."""

from typing import Any

from apps.core.fields import CountryField

from . import engine


class ViesCountryField(CountryField):
    """The country of a VIES answer: an ISO 3166-1 alpha-2 code (GR, never EL) or `XI`, the VAT
    prefix of Northern Ireland (TAX.19). Only VAT numbers use `XI`: addresses keep
    `CountryField`, so Northern Ireland stays GB with a BT postcode."""

    def to_python(self, value: Any) -> Any:
        if isinstance(value, str) and value.strip().upper() == engine.NORTHERN_IRELAND_CODE:
            return engine.NORTHERN_IRELAND_CODE
        return super().to_python(value)
