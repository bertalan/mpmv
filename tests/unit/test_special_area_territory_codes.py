"""SHP.14 guard: shipping's extra-VAT special areas name TAX.1 territories by code.

`apps.shipping` and `apps.tax` are independent siblings (import-linter), so a special area stores
the territory's code (`SpecialArea.territory_code`) instead of a foreign key, and shipping keeps
its own copy of the code format. This test lives outside both apps and keeps the two formats
identical, so every code shipping accepts is a valid `Territory.code`.
"""

import pytest

from apps.shipping import engine as shipping
from apps.tax import engine as tax

SAMPLES = [
    "ES-CN",
    " es-cn ",
    "IT-LIVIGNO",
    "XI",
    "FI-01",
    "FR-971",
    "DE-HELGOLAND",
    "",
    "E",
    "ES_CN",
    "ES-",
    "ES-CANARY-ISLANDS",
    "1T-X",
    "IT LIV",
]


def outcome(normalize: object, raw: str) -> str | None:
    try:
        return normalize(raw)  # type: ignore[operator, no-any-return]
    except ValueError:
        return None


@pytest.mark.parametrize("raw", SAMPLES)
def test_shipping_and_tax_read_territory_codes_alike(raw: str) -> None:
    assert outcome(shipping.normalize_territory_code, raw) == outcome(
        tax.normalize_territory_code, raw
    )


def test_the_code_length_is_the_same() -> None:
    assert shipping.TERRITORY_CODE_MAX_LENGTH == tax.TERRITORY_CODE_MAX_LENGTH
