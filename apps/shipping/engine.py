"""Pure shipping rules (no ORM) behind the models and constraints (SHP.1, SHP.12, SHP.14).

Zone matching and precedence live here (spec 06 §6.2): a vendor's own zone wins over platform
zones for that vendor, then the lowest priority, then the most specific match (postcode >
province > explicit country > country group), and the rest of the world (`*`) only as a last
resort. SHP.2 checks the legacy regression cases 1-7 against these functions, normalises the
destination country once (`EL` -> `GR`, `UK` -> `GB`) and parses a zone's postcode patterns once,
when its snapshot is built. Special areas (SHP.14) are resolved here too: which area an address
is in and which treatment a method gives it. The quote calculation (SHP.3) also lives here when
that task adds it.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import IntEnum
from typing import Final

from apps.core.countries import normalize_country
from apps.core.postcodes import PostcodePattern, normalize_postcode, parse_postcode_pattern

# "Rest of the world" in `ShippingZone.countries`: only alone, and only as a last resort.
WORLD: Final = "*"

# Weight above the last band of a method (spec 06, D-06). D-06 is open: a method stores an
# optional policy and no default is chosen. SHP.3 applies the policy, falling back to the
# platform-wide default that D-06 decides when a method has none (`over_max_weight` is empty).
PLATFORM_DEFAULT: Final = ""
LAST_BAND: Final = "last_band"
QUOTE: Final = "quote"
BLOCK: Final = "block"
OVER_MAX_WEIGHT_POLICIES: Final = (LAST_BAND, QUOTE, BLOCK)

# Service level of a shipping method (SHP.12).
STANDARD: Final = "standard"
EXPRESS: Final = "express"
ECONOMY: Final = "economy"
OTHER: Final = "other"
SERVICE_LEVELS: Final = (STANDARD, EXPRESS, ECONOMY, OTHER)

# VAT treatment of a method's shipping charge (SHP.12). Stored only: the tax engine (TAX.4,
# `resolve_shipping_tax`) applies it, and how `standard_rate` and `exempt` are computed depends
# on D-03, which is open. The default follows the goods (EU ancillary-supply rule).
FOLLOWS_GOODS: Final = "follows_goods"
STANDARD_RATE: Final = "standard_rate"
EXEMPT: Final = "exempt"
TAX_TREATMENTS: Final = (FOLLOWS_GOODS, STANDARD_RATE, EXEMPT)
DEFAULT_TAX_TREATMENT: Final = FOLLOWS_GOODS

# A weight band as (from_kg, to_kg); to_kg None means unlimited. Bounds are right-inclusive,
# (from_kg, to_kg]: 5.000 kg falls in 0-5 and 5.001 kg in 5-10 (spec 06 rule 3).
Band = tuple[Decimal, Decimal | None]

STATE_MAX_LENGTH: Final = 32
_STATE = re.compile(r"[A-Z0-9]+(-[A-Z0-9]+)*", re.ASCII)


class InvalidState(ValueError):
    """Raised for a province / state code with characters other than letters, digits and '-'."""


def normalize_zone_country(raw: str) -> str:
    """ISO 3166-1 alpha-2 code (EL → GR, UK → GB), or WORLD; raises InvalidCountry otherwise."""
    if raw.strip() == WORLD:
        return WORLD
    return normalize_country(raw)


def normalize_state(raw: str) -> str:
    """A province / state code as the checkout collects it (`SS`, `CA`, `IT-SS`), upper case."""
    code = raw.strip().upper()
    if not code or len(code) > STATE_MAX_LENGTH or not _STATE.fullmatch(code):
        raise InvalidState(
            f"{raw!r} is not a province or state code: letters and digits, optionally joined "
            f"by '-', at most {STATE_MAX_LENGTH} characters (e.g. SS, CA, IT-SS)."
        )
    return code


def bands_overlap(a: Band, b: Band) -> bool:
    """True when some weight falls in both bands. Same rule as the database constraint."""
    (a_from, a_to), (b_from, b_to) = a, b
    return (b_to is None or a_from < b_to) and (a_to is None or b_from < a_to)


def overlapping_bands(bands: Sequence[Band]) -> list[tuple[int, int]]:
    """Index pairs (i < j) of the bands that overlap, in order."""
    return [
        (i, j)
        for i in range(len(bands))
        for j in range(i + 1, len(bands))
        if bands_overlap(bands[i], bands[j])
    ]


# --- Zone matching and precedence (SHP.12)


class Specificity(IntEnum):
    """Why a zone matches an address; lower is more specific."""

    POSTCODE = 0
    PROVINCE = 1
    COUNTRY = 2
    GROUP = 3
    WORLD = 4


@dataclass(frozen=True)
class Destination:
    """Where a package goes. The country is normalised here, the one place every match goes
    through: an ISO alpha-2 code (`EL` -> `GR`, `UK` -> `GB`), or InvalidCountry. The province is
    compared upper case."""

    country: str
    postcode: str = ""
    state: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "country", normalize_country(self.country))
        object.__setattr__(self, "state", self.state.strip().upper())


@dataclass(frozen=True)
class ZoneSnap:
    """A zone as the pure functions see it. `group_countries` are the members of its country
    groups; `vendor_id` is None for a platform zone."""

    code: str
    priority: int
    countries: frozenset[str] = frozenset()
    group_countries: frozenset[str] = frozenset()
    postcode_patterns: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    vendor_id: int | None = None
    # Parsed once when the snapshot is built, never per address; a malformed pattern is refused
    # here (InvalidPostcodePattern). Derived from `postcode_patterns`, so not compared.
    parsed_patterns: tuple[PostcodePattern, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        parsed = tuple(parse_postcode_pattern(raw) for raw in self.postcode_patterns)
        object.__setattr__(self, "parsed_patterns", parsed)

    @property
    def is_world(self) -> bool:
        return WORLD in self.countries


def zone_match(zone: ZoneSnap, dest: Destination) -> Specificity | None:
    """How specifically `zone` matches `dest`, or None when it does not.

    Postcode patterns and provinces narrow a zone inside its countries (spec 06 case 6): the
    address matches when its postcode matches a pattern or its province is listed. An address
    without a postcode never matches a postcode-only zone (case 5).
    """
    in_countries = dest.country in zone.countries
    in_groups = dest.country in zone.group_countries
    if not (zone.is_world or in_countries or in_groups):
        return None
    if zone.parsed_patterns or zone.states:
        code = normalize_postcode(dest.postcode)
        if code and any(pattern.matches_normalized(code) for pattern in zone.parsed_patterns):
            return Specificity.POSTCODE
        if dest.state and dest.state in zone.states:
            return Specificity.PROVINCE
        return None
    if in_countries:
        return Specificity.COUNTRY
    if in_groups:
        return Specificity.GROUP
    return Specificity.WORLD


def resolve_zone(zones: Iterable[ZoneSnap], vendor_id: int, dest: Destination) -> ZoneSnap | None:
    """The zone that applies to `vendor_id`'s package for `dest` (spec 06 §6.2 precedence).

    Candidates are the platform zones and the vendor's own; another vendor's zone never applies.
    Order: `*` zones last; then a vendor zone before any platform zone; then the lowest priority;
    then the most specific match; then the code, so the result never depends on input order.
    """
    candidates = [
        (zone, specificity)
        for zone in zones
        if zone.vendor_id in (None, vendor_id)
        and (specificity := zone_match(zone, dest)) is not None
    ]
    if not candidates:
        return None
    best, _ = min(
        candidates,
        key=lambda item: (
            item[0].is_world,
            item[0].vendor_id is None,
            item[0].priority,
            item[1],
            item[0].code,
        ),
    )
    return best


# --- Special areas (SHP.14)
#
# Islands, remote areas and extra-VAT territories are postcode or province lists (platform data,
# `SpecialArea`). Each shipping method sets the treatment it gives an area; without one, the
# default applies: an indicative cost subject to quote (the buyer sees the estimate and requests a
# quote for the exact price, SHP.3 `IndicativeRates`). This module tells SHP.3 which area an
# address is in and which treatment applies; the quote itself is SHP.3.

ISLAND: Final = "island"
REMOTE: Final = "remote"
EXTRA_VAT: Final = "extra_vat"
# In the order an address in two areas is resolved: the extra-VAT territory first.
AREA_KINDS: Final = (EXTRA_VAT, ISLAND, REMOTE)

INDICATIVE: Final = "indicative"  # an estimate, then a quote for the exact price (the default)
SURCHARGE: Final = "surcharge"  # a rate with a surcharge
QUOTE_ONLY: Final = "quote_only"  # no estimate: the vendor quotes the price
UNAVAILABLE: Final = "unavailable"  # the method is not offered there; others may be
BLOCKED: Final = "blocked"  # the method refuses the area: SHP.3 returns Blocked
TREATMENT_MODES: Final = (INDICATIVE, SURCHARGE, QUOTE_ONLY, UNAVAILABLE, BLOCKED)
DEFAULT_TREATMENT_MODE: Final = INDICATIVE
PRICED_MODES: Final = (INDICATIVE, SURCHARGE)  # the buyer sees a cost
QUOTE_MODES: Final = (INDICATIVE, QUOTE_ONLY)  # the buyer requests a quote
NO_DAYS_MODES: Final = (UNAVAILABLE, BLOCKED)  # nothing is delivered: no extra days

NO_SURCHARGE: Final = ""
FIXED: Final = "fixed"  # a net amount per package
PERCENT: Final = "percent"  # a percentage of the method's cost
PER_KG: Final = "per_kg"  # a net amount per kg of the package
SURCHARGE_TYPES: Final = (FIXED, PERCENT, PER_KG)

ZERO_AMOUNT: Final = Decimal("0.00")
_CENT: Final = Decimal("0.01")
_HUNDRED: Final = Decimal(100)

# A tax territory code (TAX.1 `Territory.code`): two letters, optionally a dash and a subdivision
# code. Shipping cannot import the tax app (independent siblings), so it keeps its own copy of
# the format; `tests/unit/test_special_area_territory_codes.py` keeps the two identical.
TERRITORY_CODE_MAX_LENGTH: Final = 16
_TERRITORY_CODE = re.compile(r"[A-Z]{2}(-[A-Z0-9]{1,12})?", re.ASCII)


class InvalidTerritoryCode(ValueError):
    """Raised for text that is not a tax territory code."""


def normalize_territory_code(raw: str) -> str:
    """`ES-CN`, `IT-LIVIGNO`, `FI-01`: the code of the TAX.1 territory, upper case."""
    code = raw.strip().upper()
    if not _TERRITORY_CODE.fullmatch(code):
        raise InvalidTerritoryCode(
            f"{raw!r} is not a territory code: two letters, optionally '-' and up to 12 "
            "letters or digits (e.g. ES-CN, IT-LIVIGNO, FI-01)."
        )
    return code


def treatment_errors(
    mode: str, surcharge_type: str, surcharge: Decimal, extra_days: int
) -> dict[str, str]:
    """What is wrong with a treatment, by field (the database constraints say the same).

    A surcharge has a type and an amount; an estimate may carry one too; the other modes show no
    cost. Extra days lengthen a delivery, so a method that does not deliver there has none.
    """
    errors: dict[str, str] = {}
    if mode not in TREATMENT_MODES:
        errors["mode"] = f"Must be one of {', '.join(TREATMENT_MODES)}."
    if surcharge_type not in (NO_SURCHARGE, *SURCHARGE_TYPES):
        errors["surcharge_type"] = f"Must be empty or one of {', '.join(SURCHARGE_TYPES)}."
    elif mode == SURCHARGE and surcharge_type == NO_SURCHARGE:
        errors["surcharge_type"] = "A surcharge needs its type: fixed, percent or per kg."
    elif surcharge_type != NO_SURCHARGE and mode in TREATMENT_MODES and mode not in PRICED_MODES:
        errors["surcharge_type"] = "Only an indicative cost or a surcharge carries a surcharge."
    if surcharge < 0:
        errors["surcharge"] = "Must not be negative."
    elif surcharge_type == NO_SURCHARGE and surcharge != 0 and "surcharge_type" not in errors:
        errors["surcharge"] = "An amount needs a surcharge type."
    if mode in NO_DAYS_MODES and extra_days:
        errors["extra_days"] = "A method that does not deliver to the area has no extra days."
    return errors


@dataclass(frozen=True)
class SpecialAreaSnap:
    """A special area as the pure functions see it: the countries it lies in, narrowed by
    postcode patterns or provinces; `territory_code` names the TAX.1 territory of an extra-VAT
    area."""

    code: str
    kind: str
    countries: frozenset[str]
    postcode_patterns: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    territory_code: str = ""
    # Parsed once when the snapshot is built (InvalidPostcodePattern otherwise).
    parsed_patterns: tuple[PostcodePattern, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        parsed = tuple(parse_postcode_pattern(raw) for raw in self.postcode_patterns)
        object.__setattr__(self, "parsed_patterns", parsed)


@dataclass(frozen=True)
class AreaTreatment:
    """What a method does for an address in a special area."""

    mode: str = DEFAULT_TREATMENT_MODE
    surcharge_type: str = NO_SURCHARGE
    surcharge: Decimal = ZERO_AMOUNT
    extra_days: int = 0


DEFAULT_AREA_TREATMENT: Final = AreaTreatment()


def surcharged_cost(base_net: Decimal, weight_kg: Decimal, treatment: AreaTreatment) -> Decimal:
    """The method's net cost with the treatment's surcharge, rounded half up to cents once.

    Fixed: plus the amount. Percent: plus that share of the method's cost. Per kg: plus the
    amount times the package weight.
    """
    extra = ZERO_AMOUNT
    if treatment.surcharge_type == FIXED:
        extra = treatment.surcharge
    elif treatment.surcharge_type == PERCENT:
        extra = base_net * treatment.surcharge / _HUNDRED
    elif treatment.surcharge_type == PER_KG:
        extra = treatment.surcharge * weight_kg
    return (base_net + extra).quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class SpecialAreaDecision:
    """The special area of an address and the treatment one method gives it, for SHP.3.

    `is_default` is true when the method sets no treatment of its own for the area.
    """

    area: SpecialAreaSnap
    treatment: AreaTreatment
    is_default: bool

    @property
    def mode(self) -> str:
        return self.treatment.mode

    @property
    def offers_rate(self) -> bool:
        """The buyer sees a cost: the estimate or the surcharged rate."""
        return self.mode in PRICED_MODES

    @property
    def is_indicative(self) -> bool:
        """The cost is an estimate (SHP.3 `IndicativeRates`)."""
        return self.mode == INDICATIVE

    @property
    def requests_quote(self) -> bool:
        """The buyer requests a quote for the exact price (SHP.9 quote flow)."""
        return self.mode in QUOTE_MODES

    @property
    def is_unavailable(self) -> bool:
        return self.mode == UNAVAILABLE

    @property
    def is_blocked(self) -> bool:
        return self.mode == BLOCKED

    def cost(self, base_net: Decimal, weight_kg: Decimal) -> Decimal | None:
        """The net cost shown for the method's `base_net` cost, or None when none is shown."""
        if not self.offers_rate:
            return None
        return surcharged_cost(base_net, weight_kg, self.treatment)

    def delivery_days(
        self, min_days: int | None, max_days: int | None
    ) -> tuple[int | None, int | None]:
        """The method's indicative delivery time with the area's extra days."""
        extra = self.treatment.extra_days
        return (
            None if min_days is None else min_days + extra,
            None if max_days is None else max_days + extra,
        )


def area_match(area: SpecialAreaSnap, dest: Destination) -> Specificity | None:
    """POSTCODE or PROVINCE when `dest` is in `area`, None otherwise (same rule as a narrowed
    zone: an address without a postcode matches only by its province)."""
    if dest.country not in area.countries:
        return None
    code = normalize_postcode(dest.postcode)
    if code and any(pattern.matches_normalized(code) for pattern in area.parsed_patterns):
        return Specificity.POSTCODE
    if dest.state and dest.state in area.states:
        return Specificity.PROVINCE
    return None


def resolve_special_area(
    areas: Iterable[SpecialAreaSnap], dest: Destination
) -> SpecialAreaSnap | None:
    """The special area `dest` is in, or None.

    When several match: a postcode match before a province match, then extra-VAT before island
    before remote, then the code, so the result never depends on the order of `areas`.
    """
    candidates = [
        (area, specificity) for area in areas if (specificity := area_match(area, dest)) is not None
    ]
    if not candidates:
        return None
    best, _ = min(
        candidates,
        key=lambda item: (
            item[1],
            AREA_KINDS.index(item[0].kind) if item[0].kind in AREA_KINDS else len(AREA_KINDS),
            item[0].code,
        ),
    )
    return best


def special_area_treatment(
    areas: Iterable[SpecialAreaSnap],
    treatments: Mapping[str, AreaTreatment],
    dest: Destination,
) -> SpecialAreaDecision | None:
    """Which special area `dest` is in and the treatment a method gives it (`treatments` by area
    code), or None when `dest` is in no special area. Without a treatment of its own, the method
    gives the default: an indicative cost subject to quote."""
    area = resolve_special_area(areas, dest)
    if area is None:
        return None
    treatment = treatments.get(area.code)
    if treatment is None:
        return SpecialAreaDecision(area, DEFAULT_AREA_TREATMENT, is_default=True)
    return SpecialAreaDecision(area, treatment, is_default=False)
