"""Pure tax rules behind the TAX.1 models (spec 05 §5.2). No ORM.

This module holds the vocabulary of the tax configuration and the consistency rules of its
rows. The tax decision itself (`resolve_goods_tax`, `resolve_shipping_tax`) is TAX.3 and TAX.4,
and depends on open decisions D-01 (country of taxation), D-02 (Northern Ireland) and D-03
(carrier `force_vat`): nothing here decides them.
"""

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from apps.vendors import engine as vendors

# --- Vendor VAT regime (VendorTaxProfile)

OSS: Final = "oss"  # registered for the EU One-Stop Shop: destination rates for EU B2C
DOMESTIC_ONLY: Final = "domestic_only"  # vendor-country VAT for every EU B2C sale
EXEMPT: Final = "exempt"  # no VAT charged; `exempt_reason` says why
REGIMES: Final = (OSS, DOMESTIC_ONLY, EXEMPT)

ART74_PUBLISHING: Final = "art74_publishing"  # IT art. 74 c.1 lett. c DPR 633/72
FLAT_RATE_FORFETTARIO: Final = "flat_rate_forfettario"  # IT forfettario (fiscal regime RF19)
OTHER_EXEMPTION: Final = "other"
EXEMPT_REASONS: Final = (ART74_PUBLISHING, FLAT_RATE_FORFETTARIO, OTHER_EXEMPTION)


def profile_errors(regime: str, exempt_reason: str, *, fiscal_regime: str) -> dict[str, str]:
    """What is wrong with a VAT regime for a vendor declaring `fiscal_regime` (VEN.12).

    The fiscal regime (FatturaPA RegimeFiscale) is what the vendor declares on its invoices and
    stays on the vendor; the VAT regime lives here. They are never duplicated, only kept
    consistent: the flat-rate exemption is exactly the regime of a vendor declaring RF19
    (forfettario), which never charges VAT. A blank fiscal regime (identity not complete yet)
    allows every regime except the flat-rate exemption.
    """
    errors: dict[str, str] = {}
    if regime not in REGIMES:
        errors["regime"] = f"Unknown VAT regime {regime!r}."
    if exempt_reason and exempt_reason not in EXEMPT_REASONS:
        errors["exempt_reason"] = f"Unknown exemption reason {exempt_reason!r}."
    elif regime == EXEMPT and not exempt_reason:
        errors["exempt_reason"] = "Required when the regime is exempt."
    elif regime in REGIMES and regime != EXEMPT and exempt_reason:
        errors["exempt_reason"] = "Only an exempt regime has an exemption reason."
    elif exempt_reason == FLAT_RATE_FORFETTARIO and fiscal_regime != vendors.FLAT_RATE_REGIME:
        errors["exempt_reason"] = (
            f"The flat-rate (forfettario) exemption needs fiscal regime "
            f"{vendors.FLAT_RATE_REGIME} in the vendor's legal identity."
        )
    if (
        "regime" not in errors
        and fiscal_regime == vendors.FLAT_RATE_REGIME
        and (regime, exempt_reason) != (EXEMPT, FLAT_RATE_FORFETTARIO)
    ):
        errors["regime"] = (
            f"The vendor declares fiscal regime {vendors.FLAT_RATE_REGIME} (forfettario), "
            f"which charges no VAT: use the exempt regime with reason {FLAT_RATE_FORFETTARIO}."
        )
    return errors


# --- Tax rates

STANDARD: Final = "standard"
REDUCED: Final = "reduced"
SUPER_REDUCED: Final = "super_reduced"
ZERO: Final = "zero"
BOOKS: Final = "books"
CATEGORIES: Final = (STANDARD, REDUCED, SUPER_REDUCED, ZERO, BOOKS)

RATE_SOURCE_TEDB: Final = "ec_tedb"  # the Commission's Taxes in Europe Database (TAX.6)
RATE_SOURCE_MANUAL: Final = "manual"
RATE_SOURCES: Final = (RATE_SOURCE_TEDB, RATE_SOURCE_MANUAL)

# Rates are percentages (25.5 means 25.5%) with 4 decimals, so no published rate is rounded.
RATE_MAX_DIGITS: Final = 7
RATE_DECIMAL_PLACES: Final = 4
RATE_QUANTUM: Final = Decimal(1).scaleb(-RATE_DECIMAL_PLACES)
MIN_RATE: Final = Decimal(0)
MAX_RATE: Final = Decimal(100)


def quantize_rate(rate: Decimal) -> Decimal:
    """`rate` with exactly 4 decimals, as the database stores it (24 -> 24.0000)."""
    return rate.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def rate_error(category: str, rate: Decimal) -> str | None:
    """Why `rate` cannot be the percentage of `category`; None when it can."""
    if not isinstance(rate, Decimal):
        raise TypeError("rates are Decimal percentages, never floats")
    if not MIN_RATE <= rate <= MAX_RATE:
        return "A rate is a percentage from 0 to 100."
    if category == ZERO and rate != 0:
        return "The zero category always has a 0% rate."
    return None


# --- Territories with a special VAT status (data only; TAX.7 matches postcodes)

OUTSIDE_VAT_AREA: Final = "outside_vat_area"  # e.g. Canary Islands, Livigno, Åland
OWN_RATES: Final = "own_rates"  # inside the VAT area with rates of its own (e.g. Madeira)
SPECIAL: Final = "special"  # an explicit engine rule decides (e.g. XI, open decision D-02)
TERRITORY_STATUSES: Final = (OUTSIDE_VAT_AREA, OWN_RATES, SPECIAL)

TERRITORY_CODE_MAX_LENGTH: Final = 16
_TERRITORY_CODE = re.compile(r"[A-Z]{2}(-[A-Z0-9]{1,12})?", re.ASCII)
_POSTCODE_PATTERN = re.compile(r"[A-Z0-9]+( [A-Z0-9]+)?\*?|[A-Z0-9]+-[A-Z0-9]+", re.ASCII)


class InvalidTerritoryCode(ValueError):
    pass


class InvalidPostcodePattern(ValueError):
    pass


def normalize_territory_code(raw: str) -> str:
    """`ES-CN`, `IT-LIVIGNO`, `XI`: two letters, optionally a dash and a subdivision code."""
    code = raw.strip().upper()
    if not _TERRITORY_CODE.fullmatch(code):
        raise InvalidTerritoryCode(
            f"{raw!r} is not a territory code: two letters, optionally '-' and up to 12 "
            "letters or digits (e.g. ES-CN, IT-LIVIGNO, XI)."
        )
    return code


def normalize_postcode_pattern(raw: str) -> str:
    """An exact postcode (`23041`), a prefix ending in `*` (`35*`) or a range (`22100-22199`)."""
    pattern = raw.strip().upper()
    if not _POSTCODE_PATTERN.fullmatch(pattern):
        raise InvalidPostcodePattern(
            f"{raw!r} is not a postcode pattern: an exact postcode, a prefix ending in '*' or "
            "a range like 22100-22199."
        )
    return pattern


# --- VIES answers (VatValidation), the results of VEN.12's VatNumberValidator

VAT_VALID: Final = vendors.VIES_VALID
VAT_INVALID: Final = vendors.VIES_INVALID
VAT_UNAVAILABLE: Final = vendors.VIES_UNAVAILABLE
VAT_RESULTS: Final = (VAT_VALID, VAT_INVALID, VAT_UNAVAILABLE)

VAT_SOURCE_VIES: Final = "vies"
VAT_SOURCE_MANUAL: Final = "manual"  # recorded by staff from an official check
VAT_SOURCES: Final = (VAT_SOURCE_VIES, VAT_SOURCE_MANUAL)
