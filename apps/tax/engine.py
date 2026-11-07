"""Pure tax rules behind the TAX.1 models (spec 05 §5.2) and the TAX.2 regime change
workflow. No ORM.

This module holds the vocabulary of the tax configuration and the consistency rules of its
rows. The tax decision itself (`resolve_goods_tax`, `resolve_shipping_tax`) is TAX.3 and TAX.4,
and depends on open decisions D-01 (country of taxation), D-02 (Northern Ireland) and D-03
(carrier `force_vat`): nothing here decides them.
"""

import datetime as dt
import re
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from apps.core import postcodes
from apps.core.countries import normalize_country
from apps.core.postcodes import PostcodePattern, normalize_postcode, parse_postcode_pattern
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


def profile_errors(
    regime: str,
    exempt_reason: str,
    *,
    fiscal_regime: str,
    oss_registered_since: dt.date | None = None,
) -> dict[str, str]:
    """What is wrong with a VAT regime for a vendor declaring `fiscal_regime` (VEN.12).

    The fiscal regime (FatturaPA RegimeFiscale) is what the vendor declares on its invoices and
    stays on the vendor; the VAT regime lives here. They are never duplicated, only kept
    consistent: the flat-rate exemption is exactly the regime of a vendor declaring RF19
    (forfettario), which never charges VAT. A blank fiscal regime (identity not complete yet)
    allows every regime except the flat-rate exemption. Only the OSS regime has an OSS
    registration date (optional there).
    """
    errors: dict[str, str] = {}
    if oss_registered_since is not None and regime in REGIMES and regime != OSS:
        errors["oss_registered_since"] = "Only the OSS regime has an OSS registration date."
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


def profile_review_notes(regime: str, exempt_reason: str, *, fiscal_regime: str) -> dict[str, str]:
    """What staff must check by hand about a VAT regime the rules cannot confirm (VEN.18).

    Never blocks a save: `profile_errors` decides what is refused. A vendor declaring RF20
    (cross-border VAT franchise, Directive (EU) 2020/285) is exempt only in the member states
    that admitted it to the franchise, so no single VAT regime or exemption reason fits it.
    Until the tax advisor gives that mapping, `profile_errors` refuses only the flat-rate
    (forfettario) exemption for RF20, and every RF20 profile is flagged here for review.
    """
    del regime, exempt_reason  # the RF20 mapping is still open: every combination is flagged
    if fiscal_regime == vendors.CROSS_BORDER_FRANCHISE_REGIME:
        return {
            "regime": (
                f"The vendor declares fiscal regime {vendors.CROSS_BORDER_FRANCHISE_REGIME} "
                "(cross-border VAT franchise, Directive (EU) 2020/285), which applies only in "
                "the member states that admitted the vendor: check the VAT regime and the "
                "exemption reason by hand."
            )
        }
    return {}


# --- Regime change workflow (TAX.2): a vendor owner requests, staff approve or reject

PENDING: Final = "pending"
APPROVED: Final = "approved"
REJECTED: Final = "rejected"
REQUEST_STATUSES: Final = (PENDING, APPROVED, REJECTED)


class RegimeChangeError(Exception):
    """A regime change step that the workflow refuses."""


class RegimeChangeNotAllowed(RegimeChangeError):
    """The user may not take this step: not an owner of the vendor, or not a valid reviewer."""


class PendingRequestExists(RegimeChangeError):
    """The vendor already has a request waiting for review."""


class RequestNotPending(RegimeChangeError):
    """The request was already approved or rejected."""


class FiscalRegimeChangeNotDue(RegimeChangeError):
    """The approval would change the declared fiscal regime before the request's date."""


def check_reviewable(status: str) -> None:
    """Only a pending request can be approved or rejected; reviews are final."""
    if status != PENDING:
        raise RequestNotPending(f"the request is {status}, not pending: reviews are final")


def resulting_fiscal_regime(
    regime: str, exempt_reason: str, *, requested: str, current: str
) -> str:
    """The fiscal regime (VEN.12) the vendor declares once the requested regime applies.

    A requested fiscal regime wins; otherwise the flat-rate exemption implies RF19
    (forfettario), and any other regime keeps the vendor's current one.
    """
    if requested:
        return requested
    if (regime, exempt_reason) == (EXEMPT, FLAT_RATE_FORFETTARIO):
        return vendors.FLAT_RATE_REGIME
    return current


def request_errors(
    regime: str,
    exempt_reason: str,
    *,
    requested_fiscal_regime: str,
    current_fiscal_regime: str,
    oss_registered_since: dt.date | None = None,
) -> dict[str, str]:
    """What is wrong with a requested regime for a vendor declaring `current_fiscal_regime`.

    The request must leave the vendor consistent (`profile_errors`) with the fiscal regime it
    will declare. Leaving the flat-rate scheme needs the new fiscal regime: nothing else says
    what the vendor declares once it charges VAT.
    """
    if requested_fiscal_regime and requested_fiscal_regime not in vendors.FISCAL_REGIMES:
        return {"fiscal_regime": f"Unknown fiscal regime {requested_fiscal_regime!r}."}
    flat_rate = (regime, exempt_reason) == (EXEMPT, FLAT_RATE_FORFETTARIO)
    if (
        current_fiscal_regime == vendors.FLAT_RATE_REGIME
        and not requested_fiscal_regime
        and not flat_rate
    ):
        return {
            "fiscal_regime": (
                f"The vendor declares fiscal regime {vendors.FLAT_RATE_REGIME} (forfettario): "
                "leaving the flat-rate scheme needs the fiscal regime it will declare."
            )
        }
    resulting = resulting_fiscal_regime(
        regime, exempt_reason, requested=requested_fiscal_regime, current=current_fiscal_regime
    )
    return profile_errors(
        regime, exempt_reason, fiscal_regime=resulting, oss_registered_since=oss_registered_since
    )


def fiscal_regime_change_error(effective_on: dt.date, *, today: dt.date) -> str | None:
    """Why an approval cannot change the declared fiscal regime yet; None when it can.

    The fiscal regime is not versioned (VEN.12): it is what invoices declare from now on. So a
    change that moves it is approved on or after its date, never ahead, or invoices issued
    before the date would declare the new regime. Both dates are business-time dates (invoices
    are dated by day), converted by the caller.
    """
    if effective_on > today:
        return (
            f"The request changes the declared fiscal regime from "
            f"{effective_on.isoformat()}: approve it on or after that date."
        )
    return None


def reviewer_error(
    *, is_staff: bool, is_active: bool, has_permission: bool, is_requester: bool
) -> str | None:
    """Why a user may not review a request; None when they may.

    Reviews are for active platform staff holding the review permission (being staff, i.e.
    admin login, is not enough), and never by the person who asked for the change.
    """
    if not (is_staff and is_active):
        return "Only active platform staff review tax regime change requests."
    if not has_permission:
        return "Reviewing tax regime change requests needs the review permission."
    if is_requester:
        return "A request is reviewed by someone other than the person who made it."
    return None


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


# --- Territories with a special VAT status (data only; TAX.7 decides what a match means)

OUTSIDE_VAT_AREA: Final = "outside_vat_area"  # e.g. Canary Islands, Livigno, Åland
OWN_RATES: Final = "own_rates"  # inside the VAT area with rates of its own (e.g. Madeira)
SPECIAL: Final = "special"  # an explicit engine rule decides (e.g. XI, open decision D-02)
TERRITORY_STATUSES: Final = (OUTSIDE_VAT_AREA, OWN_RATES, SPECIAL)

TERRITORY_CODE_MAX_LENGTH: Final = 16
_TERRITORY_CODE = re.compile(r"[A-Z]{2}(-[A-Z0-9]{1,12})?", re.ASCII)


class InvalidTerritoryCode(ValueError):
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


class TerritoryConfigError(ValueError):
    """A stored territory cannot be used: one of its postcode patterns is not in the shared
    grammar. Patterns are validated on save, but writes that bypass `save()` (queryset updates,
    bulk creation, fixtures) can still store one. Raised for every address and whatever the
    order of the patterns, so a tax decision fails the same controlled way each time."""


def parse_territory_patterns(
    patterns: Iterable[str | PostcodePattern], *, code: str = ""
) -> tuple[PostcodePattern, ...]:
    """Parse a territory's postcode patterns once, all of them; raises TerritoryConfigError
    naming the territory and the pattern. Already parsed patterns pass through."""
    parsed: list[PostcodePattern] = []
    for pattern in patterns:
        if isinstance(pattern, PostcodePattern):
            parsed.append(pattern)
            continue
        try:
            parsed.append(parse_postcode_pattern(pattern))
        except postcodes.InvalidPostcodePattern as exc:
            territory = f"territory {code}" if code else "a territory"
            raise TerritoryConfigError(
                f"{territory} has an invalid stored postcode pattern: {exc}"
            ) from exc
    return tuple(parsed)


def postcode_in_territory(
    territory_country: str,
    patterns: Iterable[str | PostcodePattern],
    country: str,
    postcode: str,
) -> bool:
    """True when an address in `country` with `postcode` lies in a territory of
    `territory_country` with these postcode patterns.

    A territory matches only addresses in its own country: Padova (IT 35100) is not in the
    Canary Islands (ES `35*`). Countries are compared as ISO codes (`EL` = `GR`); an invalid
    one raises InvalidCountry. The patterns follow the grammar shared with shipping zones
    (`apps.core.postcodes`, TAX.11): exact `23041`, prefix `35*`, range `9000..9999`. An empty
    postcode matches nothing. Every pattern is parsed before any comparison, so an invalid one
    raises TerritoryConfigError whatever the address and the order of the patterns; pass the
    result of `parse_territory_patterns` to avoid parsing on every call.
    """
    parsed = parse_territory_patterns(patterns)
    if normalize_country(country) != normalize_country(territory_country):
        return False
    code = normalize_postcode(postcode)
    return bool(code) and any(pattern.matches_normalized(code) for pattern in parsed)


# --- VIES answers (VatValidation), the results of VEN.12's VatNumberValidator

VAT_VALID: Final = vendors.VIES_VALID
VAT_INVALID: Final = vendors.VIES_INVALID
VAT_UNAVAILABLE: Final = vendors.VIES_UNAVAILABLE
VAT_RESULTS: Final = (VAT_VALID, VAT_INVALID, VAT_UNAVAILABLE)
# Every row is a VIES answer. A staff-recorded answer would be a claim that zeroes the
# customer's VAT (T1), so it needs its own task with an actor and an audited service.
