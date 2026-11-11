"""Pure tax rules behind the TAX.1 models (spec 05 §5.2) and the TAX.2 regime change
workflow, and the rules of the VIES check (TAX.5). No ORM.

This module holds the vocabulary of the tax configuration and the consistency rules of its
rows, and the goods tax decision (`resolve_goods_tax`, TAX.3, decision D-01: B2C goods are
taxed in the delivery country). The shipping tax decision is TAX.4. D-02 (Northern Ireland) and
D-03 (carrier `force_vat`) are open: nothing here decides them, and a sale to Northern Ireland is
blocked (`UnresolvedTerritory`).
"""

import datetime as dt
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final, Literal

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

# The country of the law that defines each exemption reason (TAX.13, owner decision MQ-3).
# `other` has none here: each profile stores the country of the law its legal note cites.
EXEMPTION_LAW_COUNTRIES: Final[Mapping[str, str]] = {
    ART74_PUBLISHING: vendors.ITALY,
    FLAT_RATE_FORFETTARIO: vendors.ITALY,
}


def exemption_law_country(exempt_reason: str, exempt_country: str) -> str:
    """The country of the law behind `exempt_reason`; for `other`, the stored `exempt_country`.

    Empty when there is no reason, or `other` has no valid country yet.
    """
    if exempt_reason == OTHER_EXEMPTION:
        try:
            return normalize_country(exempt_country) if exempt_country else ""
        except InvalidCountry:
            return ""
    return EXEMPTION_LAW_COUNTRIES.get(exempt_reason, "")


def vat_registration_country(vat_number: str, country: str) -> str:
    """The country that registered `vat_number`: its prefix when it has one (EL is Greece),
    otherwise `country`, the vendor's country of establishment (numbers stored without one)."""
    code = vendors.compact(vat_number)
    prefix = code[:2]
    if len(code) > 2 and prefix.isalpha() and prefix.isascii():
        try:
            return normalize_country(prefix)  # EL -> GR, the VAT prefix of Greece
        except InvalidCountry:
            pass
    return country


def _exemption_country_errors(
    regime: str, exempt_reason: str, *, exempt_country: str, vat_country: str
) -> dict[str, str]:
    """TAX.13: the reason's law must be the law of the country of the vendor's VAT number."""
    if exempt_reason != OTHER_EXEMPTION:
        if exempt_country:
            return {"exempt_country": "Only the 'other' exemption stores the country of its law."}
    elif not exempt_country:
        return {
            "exempt_country": (
                "Required for the 'other' exemption: the country of the law its legal note cites."
            )
        }
    elif not exemption_law_country(exempt_reason, exempt_country):
        return {"exempt_country": f"Unknown country {exempt_country!r}."}
    law_country = exemption_law_country(exempt_reason, exempt_country)
    if regime != EXEMPT or not law_country or not vat_country or law_country == vat_country:
        return {}
    field = "exempt_country" if exempt_reason == OTHER_EXEMPTION else "exempt_reason"
    return {
        field: (
            f"The exemption {exempt_reason!r} is defined by the law of {law_country}: it is for "
            f"VAT numbers of {law_country} only, and the vendor's VAT number is from "
            f"{vat_country}."
        )
    }


def profile_errors(
    regime: str,
    exempt_reason: str,
    *,
    fiscal_regime: str,
    oss_registered_since: dt.date | None = None,
    exempt_country: str = "",
    vat_country: str = "",
) -> dict[str, str]:
    """What is wrong with a VAT regime for a vendor declaring `fiscal_regime` (VEN.12) whose
    VAT number is registered in `vat_country` (`vat_registration_country`).

    The fiscal regime (FatturaPA RegimeFiscale) is what the vendor declares on its invoices and
    stays on the vendor; the VAT regime lives here. They are never duplicated, only kept
    consistent: the flat-rate exemption is exactly the regime of a vendor declaring RF19
    (forfettario), which never charges VAT. A blank fiscal regime (identity not complete yet)
    allows every regime except the flat-rate exemption. Only the OSS regime has an OSS
    registration date (optional there).

    An exemption reason is for VAT numbers of the country whose law defines it (TAX.13, MQ-3):
    `EXEMPTION_LAW_COUNTRIES`, or `exempt_country` for `other`, which needs one. An empty
    `vat_country` (unknown) skips only the comparison.
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
    if "exempt_reason" not in errors and "regime" not in errors:
        errors.update(
            _exemption_country_errors(
                regime, exempt_reason, exempt_country=exempt_country, vat_country=vat_country
            )
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
    exempt_country: str = "",
    vat_country: str = "",
) -> dict[str, str]:
    """What is wrong with a requested regime for a vendor declaring `current_fiscal_regime`.

    The request must leave the vendor consistent (`profile_errors`, the exemption country rule
    included) with the fiscal regime it will declare. Leaving the flat-rate scheme needs the
    new fiscal regime: nothing else says what the vendor declares once it charges VAT.
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
        regime,
        exempt_reason,
        fiscal_regime=resulting,
        oss_registered_since=oss_registered_since,
        exempt_country=exempt_country,
        vat_country=vat_country,
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


# The tax reason of a sale to an address in a territory outside the VAT area (spec 05 T15).
OUTSIDE_VAT_TERRITORY: Final = "outside_vat_territory"

# A territory with an ISO 3166-1 code of its own, written as the country of an address, is named
# by that code alone: no postcode is needed, and a blank or wrong one cannot send the address to
# mainland VAT (fail closed). Written as France, Saint-Martin (97150) and Saint-Barthelemy (97133)
# are found by their postcode.
TERRITORY_BY_ALIAS_COUNTRY: Final = {
    "AX": "FI-01",
    "GP": "FR-971",
    "MQ": "FR-972",
    "GF": "FR-973",
    "RE": "FR-974",
    "YT": "FR-976",
    "MF": "FR-MF",
    "BL": "FR-BL",
}

# Postcode length (digits) of the countries with territories: a prefix pattern such as `35*`
# matches a whole postcode only, never a truncated or over-long one.
POSTCODE_LENGTH: Final = {
    "DE": 5,
    "ES": 5,
    "FI": 5,
    "FR": 5,
    "GR": 5,
    "IT": 5,  # country-literal: postcode shape of the territory data, not the platform country
}


def territory_country(country: str) -> str:
    """The country a Territory row is filed under for an address written in `country`."""
    code = normalize_country(country)
    alias = TERRITORY_BY_ALIAS_COUNTRY.get(code)
    return alias[:2] if alias else code


@dataclass(frozen=True)
class TerritoryRule:
    """A territory as the engine reads it: code, country, VAT status, parsed postcode patterns."""

    code: str
    country: str
    vat_status: str
    patterns: tuple[PostcodePattern, ...]


def territory_rule(
    code: str, country: str, vat_status: str, patterns: Iterable[str | PostcodePattern]
) -> TerritoryRule:
    """Build a rule; raises TerritoryConfigError for an invalid pattern, ValueError for an
    unknown status."""
    if vat_status not in TERRITORY_STATUSES:
        raise ValueError(f"territory {code}: {vat_status!r} is not a VAT status")
    return TerritoryRule(
        code=code,
        country=normalize_country(country),
        vat_status=vat_status,
        patterns=parse_territory_patterns(patterns, code=code),
    )


_EXACT_SPECIFICITY: Final = 1000  # above any prefix or range length


def _specificity(pattern: PostcodePattern) -> int:
    """How narrow a pattern is: an exact postcode beats any prefix or range, and a longer
    prefix (or range bound) beats a shorter one."""
    return _EXACT_SPECIFICITY if pattern.kind == "exact" else len(pattern.low)


def find_territory(
    country: str, postcode: str, rules: Iterable[TerritoryRule]
) -> TerritoryRule | None:
    """The territory that contains the address, or None (mainland, no postcode).

    The country must be the territory's own: Padova (IT 35100) is not in the Canary Islands
    (ES `35*`). A country with an ISO code of its own (`AX`, `GP`...) names its territory by
    itself, whatever the postcode (`TERRITORY_BY_ALIAS_COUNTRY`).

    Territories may overlap (Saint-Martin 97150 lies inside Guadeloupe's `971*`): the most
    specific matching pattern wins, whatever the order of the rules (`_specificity`). Equally
    specific rules keep the order given.
    """
    rules = list(rules)
    code = normalize_country(country)
    alias = TERRITORY_BY_ALIAS_COUNTRY.get(code)
    if alias is not None:
        return next((rule for rule in rules if rule.code == alias), None)
    expected = POSTCODE_LENGTH.get(code)
    normalized = normalize_postcode(postcode)
    if not normalized or (expected is not None and len(normalized) != expected):
        return None
    best: TerritoryRule | None = None
    best_score = -1
    for rule in rules:
        if rule.country != code:
            continue
        score = max(
            (_specificity(p) for p in rule.patterns if p.matches_normalized(normalized)),
            default=-1,
        )
        if score > best_score:
            best, best_score = rule, score
    return best


def is_outside_vat_territory(country: str, postcode: str, rules: Iterable[TerritoryRule]) -> bool:
    """True when the address lies in a territory outside the VAT area: the sale is 0% with the
    reason `outside_vat_territory` (T15). `own_rates` and `special` (XI, D-02) territories are not
    outside: their rule is for the tax decision."""
    rule = find_territory(country, postcode, rules)
    return rule is not None and rule.vat_status == OUTSIDE_VAT_AREA


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
#: The vendor has no VAT number VIES can use as requester: no reverse-charge supply (art. 138).
VAT_REASON_NO_VENDOR_VAT: Final = "no_vendor_vat_number"
#: VIES said valid but gave no consultation number: no usable proof for an order.
VAT_REASON_NO_CONSULTATION_NUMBER: Final = "no_consultation_number"

# Why VIES gave no answer, when it is not one of VIES's own error codes (MS_UNAVAILABLE…).
DETAIL_TIMEOUT: Final = "timeout"
DETAIL_CONNECTION: Final = "connection_failed"
DETAIL_BAD_RESPONSE: Final = "bad_response"
DETAIL_CLIENT_ERROR: Final = "client_error"
#: VIES refused the requester (the vendor's or the operator's VAT number) of the consultation.
DETAIL_INVALID_REQUESTER: Final = "invalid_requester"

_TIMEOUT_DETAILS: Final = frozenset({DETAIL_TIMEOUT, "TIMEOUT"})
_INVALID_INPUT = "INVALID_INPUT"
_INVALID_REQUESTER_INFO = "INVALID_REQUESTER_INFO"
_VIES_UNDISCLOSED = "---"  # a name or address the member state does not disclose

_MESSAGES: Final = {
    VAT_REASON_VALID: "VIES confirmed the VAT number.",
    VAT_REASON_INVALID: "VIES says the VAT number is not valid",
    VAT_REASON_UNAVAILABLE: "VIES could not be reached",
    VAT_REASON_TIMEOUT: "VIES did not answer in time",
    VAT_REASON_MALFORMED: "This is not a well-formed VAT number",
    VAT_REASON_PREFIX_MISMATCH: "The VAT number's prefix is not the one of {country}",
    VAT_REASON_NOT_IN_VIES: "VIES does not cover {country}",
    VAT_REASON_NO_VENDOR_VAT: "The seller has no VAT number for an intra-EU supply",
    VAT_REASON_NO_CONSULTATION_NUMBER: "VIES gave no consultation number for this check",
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
    request_date: str = ""  # VIES `requestDate`, as sent


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
        if code == _INVALID_REQUESTER_INFO:
            return ViesAnswer(result=VAT_UNAVAILABLE, detail=DETAIL_INVALID_REQUESTER)
        return ViesAnswer(
            result=VAT_INVALID if code == _INVALID_INPUT else VAT_UNAVAILABLE, detail=code
        )

    error = _text(payload, "userError")
    if error == _INVALID_REQUESTER_INFO:
        return ViesAnswer(result=VAT_UNAVAILABLE, detail=DETAIL_INVALID_REQUESTER)
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
        request_date=_text(payload, "requestDate"),
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


# --- The goods tax decision (TAX.3, spec 05 §5.2)

# D-01 (owner, 2026-11-10): B2C goods are taxed in the country they are delivered to (VAT
# Directive art. 33, OSS since 2021). The billing country only validates B2B data. The decision
# reads `TaxContext.destination_country` and nothing else for the basis; this constant names
# the rule so a change of rule has one place to start from.
DELIVERY: Final = "delivery"
BILLING: Final = "billing"
GOODS_COUNTRY_BASIS: Final = DELIVERY

REASON_STANDARD_DESTINATION: Final = "standard_destination"
REASON_DOMESTIC_VENDOR_COUNTRY: Final = "domestic_vendor_country"
REASON_EXPORT: Final = "export"
REASON_REVERSE_CHARGE: Final = "reverse_charge"
REASON_ART74_PUBLISHING: Final = ART74_PUBLISHING
REASON_FLAT_RATE_VENDOR: Final = "flat_rate_vendor"
REASON_EXEMPT_OTHER: Final = "exempt_other"

# Italian e-invoice exemption natures (FatturaPA `Natura`), D-28. The platform issues no invoices
# (spec 00): the code is carried on the decision and the order snapshot so the vendor's own
# invoice and the OSS and intra-community reports can use it.
NATURE_CODES: Final = (
    "N1", "N2.1", "N2.2", "N3.1", "N3.2", "N3.3", "N3.4", "N3.5", "N3.6", "N4", "N5",
    "N6.1", "N6.2", "N6.3", "N6.4", "N6.5", "N6.6", "N6.7", "N6.8", "N6.9", "N7",
)  # fmt: skip
# Reason -> nature, for the reasons that map to one nature whatever the vendor. `exempt_other`
# takes the nature the vendor's profile declares; taxed reasons have none. Reviewed by the tax
# advisor before production (docs/dev/tax-nature-codes.md).
NATURE_BY_REASON: Final[Mapping[str, str]] = {
    "reverse_charge": "N3.2",  # intra-EU supply, art. 138 (IT art. 41 D.L. 331/93)
    "export": "N3.1",  # art. 146 (IT art. 8 DPR 633/72)
    "outside_vat_territory": "N2.1",  # outside the VAT area, art. 6 (IT arts. 7 to 7-septies)
    "art74_publishing": "N2.2",
    "flat_rate_vendor": "N2.2",
}

TaxReason = Literal[
    "standard_destination",
    "domestic_vendor_country",
    "export",
    "reverse_charge",
    "art74_publishing",
    "flat_rate_vendor",
    "exempt_other",
    "outside_vat_territory",
]

# D-02 (Northern Ireland) is open: `XI`, and GB with a `BT` postcode, block the sale.
NORTHERN_IRELAND_CODE: Final = "XI"
NORTHERN_IRELAND_POSTCODE_PREFIX: Final = "BT"

# Keys of the legal wording on the order document (the texts live in the CMS and the profile).
NOTE_REVERSE_CHARGE: Final = "reverse_charge_art196"  # Directive 2006/112/EC art. 196
NOTE_EXPORT: Final = "export_art146"  # Directive 2006/112/EC art. 146
NOTE_OUTSIDE_VAT_TERRITORY: Final = OUTSIDE_VAT_TERRITORY
NOTE_EXEMPT_PREFIX: Final = "exempt:"  # followed by the profile's exempt_reason

MONEY_QUANTUM: Final = Decimal("0.01")

FiscalCategory = str  # one of CATEGORIES


class BlockingTaxError(Exception):
    """The tax cannot be decided: checkout shows the message and the sale does not go on."""


class MissingTaxRate(BlockingTaxError, LookupError):
    """No rate for the country (or territory) and category: a blocking configuration error,
    never a silent 0% (T6)."""


class IncompleteTaxProfile(BlockingTaxError, ValueError):
    """The vendor's exempt profile lacks what the decision needs (an unknown reason, or `other`
    without its law country, its legal note or (Italy) its nature code; such profiles exist after
    migration, MQ-15): checkout shows a message."""


class UnresolvedTerritory(BlockingTaxError):
    """The address (or the vendor) lies in a territory whose VAT treatment is not decided or
    not supported: Northern Ireland (D-02 open), a `special` territory, a vendor established
    in a territory with its own rates or outside the VAT area."""


@dataclass(frozen=True)
class VendorTaxProfileSnapshot:
    """The vendor's tax profile valid on the date of the sale."""

    regime: str
    exempt_reason: str = ""
    exempt_country: str = ""
    # The profile's translated legal notes as (language, text) pairs (D-26): the wording of an
    # `other` exemption, which must not be empty.
    legal_notes: tuple[tuple[str, str], ...] = ()
    # An `other` exemption's structured reason, one of NATURE_CODES (D-28); Italian vendors need it.
    exemption_nature: str = ""


@dataclass(frozen=True)
class PlatformOperatorSnapshot:
    """The platform operator valid on the date of the sale. The goods tree does not read it
    (no deemed-supplier branch, D-21); it is part of the context for TAX.4 and CMP.18."""

    country: str


@dataclass(frozen=True)
class B2BValidated:
    """A business customer whose VAT number VIES confirmed (fail closed: anything else is a
    consumer). `vat_country` is the country of the number, not of the delivery."""

    vat_country: str
    vat_number: str
    checked_at: dt.datetime


@dataclass(frozen=True)
class VatCheckSnapshot:
    """The VIES evidence behind the customer's treatment, for the order snapshot: tells "no
    number" (no snapshot), "invalid", "VIES unavailable" and a valid answer too old to trust
    (`stale`) apart."""

    country: str
    vat_number: str
    result: str
    request_id: str
    checked_at: dt.datetime
    stale: bool = False


@dataclass(frozen=True)
class RateTable:
    """VAT percentages valid on the date of the sale, by (country or territory code, category)."""

    rates: Mapping[tuple[str, str], Decimal]

    def rate(self, code: str, category: str) -> Decimal:
        try:
            return self.rates[(code, category)]
        except KeyError:
            raise MissingTaxRate(f"no {category} tax rate configured for {code}") from None


@dataclass(frozen=True)
class TaxContext:
    on: dt.date
    vendor_country: str
    vendor_profile: VendorTaxProfileSnapshot
    platform: PlatformOperatorSnapshot
    destination_country: str
    destination_postcode: str | None  # for special territories
    customer: Literal["b2c"] | B2BValidated
    rates: RateTable
    territories: tuple[TerritoryRule, ...] = ()
    vat_check: VatCheckSnapshot | None = None


@dataclass(frozen=True)
class TaxDecision:
    rate: Decimal  # effective percentage, never a "class"
    reason: TaxReason
    country_of_taxation: str | None
    legal_note_key: str | None  # text on the order document
    nature_code: str | None = None  # Italian e-invoice nature (N1-N7), None when taxed


def _zero(reason: TaxReason, note: str | None, nature: str | None = None) -> TaxDecision:
    return TaxDecision(
        rate=Decimal(0),
        reason=reason,
        country_of_taxation=None,
        legal_note_key=note,
        nature_code=NATURE_BY_REASON.get(reason, nature),
    )


def _taxed(rate: Decimal, reason: TaxReason, country: str) -> TaxDecision:
    return TaxDecision(
        rate=quantize_rate(rate), reason=reason, country_of_taxation=country, legal_note_key=None
    )


def resolve_goods_tax(ctx: TaxContext, category: FiscalCategory) -> TaxDecision:
    """The VAT on one line of goods: the decision tree of spec 05 §5.2.

    1. an exempt vendor charges no VAT, whatever else is true (T4);
    2. an address in a territory outside the VAT area is 0% (T15), found before the EU test
       because Aaland, Saint-Martin and the like are written with a country code of their own;
    3. a destination outside the EU is an export (T14);
    4. a VIES-validated business with a VAT number from another member state than the vendor's,
       receiving goods in a member state other than the vendor's, is reverse charge (T1, N3.2);
    5. a validated business with a number of the vendor's own country, receiving goods in
       another member state, pays vendor-country VAT (D-26.1: place of supply is departure,
       art. 32; art. 138 needs a number of another member state);
    6. the vendor country's rate applies at home or for a `domestic_only` vendor (T3, T7, T8,
       D-26.2: a foreign number delivered in the vendor's country is a domestic sale);
    7. otherwise the destination country's rate (OSS, T2, T5).

    Rates come from the context's table; a missing rate raises `MissingTaxRate` (T6). A blank or
    invalid destination country raises `InvalidCountry`: there is no default country (T18).
    """
    if category not in CATEGORIES:
        raise ValueError(f"{category!r} is not a fiscal category")
    profile = ctx.vendor_profile
    if profile.regime == EXEMPT:
        return _exempt(profile, vendor_country=ctx.vendor_country)

    if _is_northern_ireland(ctx.destination_country, ctx.destination_postcode):
        raise UnresolvedTerritory("sales to Northern Ireland are blocked until D-02 is decided")
    destination = normalize_country(ctx.destination_country)
    vendor_country = normalize_country(ctx.vendor_country)
    territory = find_territory(destination, ctx.destination_postcode or "", ctx.territories)
    if territory is not None and territory.vat_status == OUTSIDE_VAT_AREA:
        return _zero(OUTSIDE_VAT_TERRITORY, NOTE_OUTSIDE_VAT_TERRITORY)
    if territory is not None and territory.vat_status == SPECIAL:
        raise UnresolvedTerritory(f"territory {territory.code} has no tax rule yet")
    destination = territory_country(destination)

    if destination not in EU_MEMBER_STATES:
        return _zero(REASON_EXPORT, NOTE_EXPORT)

    customer = ctx.customer
    if (
        customer != "b2c"
        and destination != vendor_country
        and customer.vat_country != vendor_country
        and customer.vat_country in EU_MEMBER_STATES
    ):
        return _zero(REASON_REVERSE_CHARGE, NOTE_REVERSE_CHARGE)

    # A territory with rates of its own is keyed by its code, in every branch below.
    own_rates = territory is not None and territory.vat_status == OWN_RATES
    rate_code = territory.code if territory is not None and own_rates else destination
    if (
        customer != "b2c"
        and destination != vendor_country
        and customer.vat_country == vendor_country
    ):
        # D-26: place of supply is departure (art. 32) and the intra-community exemption needs a
        # number of another member state (art. 138): vendor-country VAT (confirmed, D-28).
        return _taxed(
            ctx.rates.rate(vendor_country, category),
            REASON_DOMESTIC_VENDOR_COUNTRY,
            vendor_country,
        )
    if destination == vendor_country or profile.regime == DOMESTIC_ONLY:
        # Delivered at home, or a domestic_only vendor: the vendor country's rate; an own-rates
        # territory inside the delivery country keeps its own rate.
        code = rate_code if own_rates else vendor_country
        return _taxed(
            ctx.rates.rate(code, category), REASON_DOMESTIC_VENDOR_COUNTRY, vendor_country
        )

    return _taxed(ctx.rates.rate(rate_code, category), REASON_STANDARD_DESTINATION, destination)


def _is_northern_ireland(country: str, postcode: str | None) -> bool:
    code = country.strip().upper()
    if code == NORTHERN_IRELAND_CODE:
        return True
    return code in ("GB", "UK") and normalize_postcode(postcode or "").startswith(
        NORTHERN_IRELAND_POSTCODE_PREFIX
    )


def _exempt(profile: VendorTaxProfileSnapshot, *, vendor_country: str) -> TaxDecision:
    reason = profile.exempt_reason
    if reason == ART74_PUBLISHING:
        return _zero(REASON_ART74_PUBLISHING, NOTE_EXEMPT_PREFIX + reason)
    if reason == FLAT_RATE_FORFETTARIO:
        return _zero(REASON_FLAT_RATE_VENDOR, NOTE_EXEMPT_PREFIX + reason)
    if (
        reason == OTHER_EXEMPTION
        and profile.exempt_country
        and any(text.strip() for _, text in profile.legal_notes)
    ):
        nature = profile.exemption_nature
        if nature and nature not in NATURE_CODES:
            raise IncompleteTaxProfile(f"{nature!r} is not an exemption nature code")
        if not nature and normalize_country(vendor_country) == vendors.ITALY:
            raise IncompleteTaxProfile("an Italian vendor's `other` exemption needs a nature code")
        return _zero(
            REASON_EXEMPT_OTHER,
            f"{NOTE_EXEMPT_PREFIX}{reason}:{profile.exempt_country}",
            nature or None,
        )
    raise IncompleteTaxProfile(
        f"an exempt vendor profile needs a known exemption reason ({reason!r})"
    )


def tax_amount(net: Decimal, rate: Decimal) -> Decimal:
    """The VAT on a line's net amount, ROUND_HALF_UP to the cent (never floats)."""
    if not isinstance(net, Decimal) or not isinstance(rate, Decimal):
        raise TypeError("net and rate must be Decimal")
    if not (net.is_finite() and rate.is_finite()):
        raise ValueError("net and rate must be finite")
    return (net * rate / 100).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
