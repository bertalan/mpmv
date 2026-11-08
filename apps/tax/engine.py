"""Pure tax rules behind the TAX.1 models (spec 05 §5.2) and the TAX.2 regime change
workflow, and the rules of the VIES check (TAX.5). No ORM.

This module holds the vocabulary of the tax configuration and the consistency rules of its
rows. The tax decision itself (`resolve_goods_tax`, `resolve_shipping_tax`) is TAX.3 and TAX.4,
and depends on open decisions D-01 (country of taxation), D-02 (Northern Ireland) and D-03
(carrier `force_vat`): nothing here decides them.
"""

import datetime as dt
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from apps.core import postcodes
from apps.core.countries import EU_MEMBER_STATES, InvalidCountry, normalize_country
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


# --- The VIES check (TAX.5): what is asked, how an answer is read, why a number is not verified
#
# VIES fails closed (spec 05 §5.2): only a clear "valid" from VIES verifies a VAT number, so
# only then can a B2B sale be reverse charged. Anything else (invalid, VIES or the member
# state's service down, too slow, an answer about another number, a prefix that is not the
# country's) leaves the number not verified and VAT is charged.

VIES_COUNTRIES: Final = EU_MEMBER_STATES  # ISO codes; Northern Ireland (XI) waits for D-02
# Countries whose national number may itself start with two letters (the French key), so two
# leading letters other than the prefix are not a sign of another country's number there.
NUMBER_MAY_START_WITH_LETTERS: Final = frozenset({"FR"})
_VAT_SEPARATORS = re.compile(r"[\s.\-/]+")
_VIES_NUMBER = re.compile(r"[0-9A-Z+*]{2,12}", re.ASCII)  # the VIES REST API's own pattern

VAT_REASON_VALID: Final = "vies_valid"
VAT_REASON_INVALID: Final = "vies_invalid"
VAT_REASON_UNAVAILABLE: Final = "vies_unavailable"
VAT_REASON_TIMEOUT: Final = "vies_timeout"
VAT_REASON_MALFORMED: Final = "malformed_vat_number"
VAT_REASON_PREFIX_MISMATCH: Final = "prefix_mismatch"
VAT_REASON_NOT_IN_VIES: Final = "country_not_in_vies"

# Why VIES gave no answer, when it is not one of VIES's own error codes (MS_UNAVAILABLE…).
DETAIL_TIMEOUT: Final = "timeout"
DETAIL_CONNECTION: Final = "connection_failed"
DETAIL_BAD_RESPONSE: Final = "bad_response"
DETAIL_CLIENT_ERROR: Final = "client_error"

_TIMEOUT_DETAILS: Final = frozenset({DETAIL_TIMEOUT, "TIMEOUT"})
_INVALID_INPUT = "INVALID_INPUT"
_VIES_UNDISCLOSED = "---"  # a name or address the member state does not disclose

_MESSAGES: Final = {
    VAT_REASON_VALID: "VIES confirmed the VAT number.",
    VAT_REASON_INVALID: "VIES says the VAT number is not valid",
    VAT_REASON_UNAVAILABLE: "VIES could not be reached",
    VAT_REASON_TIMEOUT: "VIES did not answer in time",
    VAT_REASON_MALFORMED: "This is not a well-formed VAT number",
    VAT_REASON_PREFIX_MISMATCH: "The VAT number's prefix is not the one of {country}",
    VAT_REASON_NOT_IN_VIES: "VIES does not cover {country}",
}
_NOT_VERIFIED = ": the VAT number is not verified and VAT is charged."


@dataclass(frozen=True)
class ViesQuery:
    """A VAT number VIES can be asked about: ISO `country`, VIES `prefix` (EL for GR) and the
    national `number` without the prefix."""

    country: str
    prefix: str
    number: str

    @property
    def full_number(self) -> str:
        return f"{self.prefix}{self.number}"


@dataclass(frozen=True)
class ViesAnswer:
    """What VIES said about a number. `detail` is VIES's error code, or why there was no
    answer (`DETAIL_*`); `request_id` is the consultation number, when VIES gives one."""

    result: str
    detail: str = ""
    name: str = ""
    address: str = ""
    request_id: str = ""


class NotCheckable(ValueError):
    """VIES cannot confirm this number, so it is not asked: the number is not verified."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def _not_checkable(reason: str, country: str) -> NotCheckable:
    return NotCheckable(reason, vat_check_message(reason, country=country))


def vies_query(country: str, vat_number: str) -> ViesQuery:
    """The VIES question for a VAT number given with its country (spec 05 T2).

    Separators and the country's prefix (VIES prefix or ISO code) are removed. Raises
    `NotCheckable` for a country outside VIES, a number starting with another prefix, or a
    number VIES would refuse as malformed.
    """
    try:
        iso = normalize_country(country)
    except InvalidCountry:
        raise _not_checkable(VAT_REASON_NOT_IN_VIES, country.strip().upper()) from None
    if iso not in VIES_COUNTRIES:
        raise _not_checkable(VAT_REASON_NOT_IN_VIES, iso)
    prefix = vendors.vat_prefix(iso)
    code = _VAT_SEPARATORS.sub("", vat_number).upper()
    for own in (prefix, iso):
        if code.startswith(own):
            code = code[len(own) :]
            break
    else:
        leading = code[:2]
        two_letters = len(leading) == 2 and leading.isascii() and leading.isalpha()
        if two_letters and iso not in NUMBER_MAY_START_WITH_LETTERS:
            raise _not_checkable(VAT_REASON_PREFIX_MISMATCH, iso)
    if not _VIES_NUMBER.fullmatch(code):
        raise _not_checkable(VAT_REASON_MALFORMED, iso)
    return ViesQuery(iso, prefix, code)


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or value.strip() == _VIES_UNDISCLOSED:
        return ""
    return value.strip()


def read_vies_answer(query: ViesQuery, payload: Mapping[str, Any]) -> ViesAnswer:
    """Read a VIES REST answer (`check-vat-number`) about `query`. Fails closed.

    `valid` must be a boolean and the answer must be about the number asked. A VIES error code
    is kept in `detail`: `INVALID_INPUT` is invalid, any other code, or anything unclear, is
    unavailable.
    """
    bad = ViesAnswer(result=VAT_UNAVAILABLE, detail=DETAIL_BAD_RESPONSE)
    wrappers = payload.get("errorWrappers")
    if wrappers is not None:
        if not isinstance(wrappers, list) or not wrappers or not isinstance(wrappers[0], dict):
            return bad
        code = str(wrappers[0].get("error") or DETAIL_BAD_RESPONSE)
        return ViesAnswer(
            result=VAT_INVALID if code == _INVALID_INPUT else VAT_UNAVAILABLE, detail=code
        )

    error = _text(payload, "userError")
    if error not in ("", "VALID", "INVALID", _INVALID_INPUT):
        return ViesAnswer(result=VAT_UNAVAILABLE, detail=error)
    valid = payload.get("valid")
    if not isinstance(valid, bool):
        return bad
    for key, asked in (("countryCode", query.prefix), ("vatNumber", query.number)):
        echoed = payload.get(key)
        if echoed is not None and str(echoed).upper() != asked:
            return bad
    if valid and error in ("", "VALID"):
        result, detail = VAT_VALID, ""
    else:
        result, detail = VAT_INVALID, (error if error == _INVALID_INPUT else "")
    return ViesAnswer(
        result=result,
        detail=detail,
        name=_text(payload, "name"),
        address=_text(payload, "address"),
        request_id=_text(payload, "requestIdentifier"),
    )


def is_verified(result: str | None) -> bool:
    """Only a valid VIES answer verifies a number (reverse charge possible)."""
    return result == VAT_VALID


def vat_check_reason(result: str, detail: str = "") -> str:
    """The reason code of a VIES answer."""
    if result == VAT_VALID:
        return VAT_REASON_VALID
    if result == VAT_INVALID:
        return VAT_REASON_INVALID
    if detail in _TIMEOUT_DETAILS:
        return VAT_REASON_TIMEOUT
    return VAT_REASON_UNAVAILABLE


def vat_check_message(reason: str, *, country: str) -> str:
    """A sentence for staff and customers saying why the number is, or is not, verified."""
    text = _MESSAGES.get(reason, _MESSAGES[VAT_REASON_UNAVAILABLE]).format(country=country)
    return text if reason == VAT_REASON_VALID else text + _NOT_VERIFIED
