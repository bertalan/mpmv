"""Pure shipping rules (no ORM) behind the models and constraints (SHP.1, SHP.12, SHP.14).

Zone matching and precedence live here (spec 06 §6.2): a vendor's own zone wins over platform
zones for that vendor, then the lowest priority, then the most specific match (postcode >
province > explicit country > country group), and the rest of the world (`*`) only as a last
resort. SHP.2 checks the legacy regression cases 1-7 against these functions, normalises the
destination country once (`EL` -> `GR`, `UK` -> `GB`) and parses a zone's postcode patterns once,
when its snapshot is built. Special areas (SHP.14) are resolved here too: which area an address
is in and which treatment a method gives it. The quote calculation (SHP.3, `quote_shipping`)
lives here too: one vendor's package in, an outcome out (Rates, IndicativeRates, NeedsQuote or
Blocked), net of VAT; the VAT on shipping is the tax engine's (TAX.4).
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import IntEnum
from typing import Final

from apps.core.countries import normalize_country
from apps.core.postcodes import (
    InvalidPostcodePattern,
    PostcodePattern,
    normalize_postcode,
    parse_postcode_pattern,
    split_country_qualifier,
)

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


class UnqualifiedEntry(ValueError):
    """Raised for a postcode pattern or province without a `CC:` prefix in a zone that does not
    cover exactly one country."""


def normalize_zone_country(raw: str) -> str:
    """ISO 3166-1 alpha-2 code (EL → GR, UK → GB), or WORLD; raises InvalidCountry otherwise."""
    if raw.strip() == WORLD:
        return WORLD
    return normalize_country(raw)


def split_state(raw: str) -> tuple[str, str]:
    """`(country, code)` of a province entry: `IT:SS` -> ("IT", "SS"), `SS` -> ("", "SS")."""
    try:
        country, body = split_country_qualifier(raw)
    except InvalidPostcodePattern as exc:
        raise InvalidState(str(exc)) from exc
    return country, _state_code(body)


def normalize_state(raw: str) -> str:
    """A province / state code as the checkout collects it (`SS`, `CA`, `IT-SS`), upper case,
    optionally qualified by its country (`IT:SS`)."""
    country, code = split_state(raw)
    return f"{country}:{code}" if country else code


def _state_code(raw: str) -> str:
    code = raw.strip().upper()
    if not code or len(code) > STATE_MAX_LENGTH or not _STATE.fullmatch(code):
        raise InvalidState(
            f"{raw!r} is not a province or state code: letters and digits, optionally joined "
            f"by '-', at most {STATE_MAX_LENGTH} characters (e.g. SS, CA, IT-SS)."
        )
    return code


def unqualified_entries(patterns: Iterable[str], states: Iterable[str]) -> list[str]:
    """The postcode patterns and provinces that carry no `CC:` prefix, in order."""
    return [entry for entry in (*patterns, *states) if not split_country_qualifier(entry)[0]]


def single_country(countries: Iterable[str]) -> str:
    """The one country of `countries`, or "" when there are none, several, or the world."""
    found = set(countries)
    return next(iter(found)) if len(found) == 1 and WORLD not in found else ""


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
    # here (InvalidPostcodePattern). Derived from `postcode_patterns` and `states`, so not
    # compared. Each pattern and province keeps the country it was written for.
    parsed_patterns: tuple[PostcodePattern, ...] = field(init=False, repr=False, compare=False)
    parsed_states: tuple[tuple[str, str], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        parsed = tuple(parse_postcode_pattern(raw) for raw in self.postcode_patterns)
        parsed_states = tuple(split_state(raw) for raw in self.states)
        object.__setattr__(self, "parsed_patterns", parsed)
        object.__setattr__(self, "parsed_states", parsed_states)
        loose = unqualified_entries(self.postcode_patterns, self.states)
        if loose and not single_country(self.countries | self.group_countries):
            raise UnqualifiedEntry(
                f"Zone {self.code!r} covers no single country, so {', '.join(map(repr, loose))} "
                "must start with a country code (PT:9000..9999)."
            )

    @property
    def is_world(self) -> bool:
        return WORLD in self.countries


def zone_match(zone: ZoneSnap, dest: Destination) -> Specificity | None:
    """How specifically `zone` matches `dest`, or None when it does not.

    Postcode patterns and provinces narrow a zone inside its countries (spec 06 case 6): the
    address matches when its postcode matches a pattern or its province is listed. A qualified
    entry (`PT:9000..9999`) applies to its own country only (case 20). An address without a
    postcode never matches a postcode-only zone (case 5).
    """
    in_countries = dest.country in zone.countries
    in_groups = dest.country in zone.group_countries
    if not (zone.is_world or in_countries or in_groups):
        return None
    if zone.parsed_patterns or zone.states:
        code = normalize_postcode(dest.postcode)
        if code and any(
            pattern.country in ("", dest.country) and pattern.matches_normalized(code)
            for pattern in zone.parsed_patterns
        ):
            return Specificity.POSTCODE
        if dest.state and any(
            country in ("", dest.country) and state == dest.state
            for country, state in zone.parsed_states
        ):
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
    # Parsed once when the snapshot is built (InvalidPostcodePattern, InvalidState otherwise).
    # Each pattern and province keeps the country it was written for (`PT:9000..9999`, `IT:SS`).
    parsed_patterns: tuple[PostcodePattern, ...] = field(init=False, repr=False, compare=False)
    parsed_states: tuple[tuple[str, str], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        parsed = tuple(parse_postcode_pattern(raw) for raw in self.postcode_patterns)
        object.__setattr__(self, "parsed_patterns", parsed)
        object.__setattr__(self, "parsed_states", tuple(split_state(raw) for raw in self.states))


@dataclass(frozen=True)
class AreaTreatment:
    """What a method does for an address in a special area."""

    mode: str = DEFAULT_TREATMENT_MODE
    surcharge_type: str = NO_SURCHARGE
    surcharge: Decimal = ZERO_AMOUNT
    extra_days: int = 0


DEFAULT_AREA_TREATMENT: Final = AreaTreatment()
# The method's own zone lies inside the area, so its rate already includes it: a surcharge of 0.
PRICED_BY_ZONE_TREATMENT: Final = AreaTreatment(mode=SURCHARGE, surcharge_type=FIXED)


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
    """POSTCODE or PROVINCE when `dest` is in `area`, None otherwise. Same rule as a narrowed
    zone (`zone_match`): an address without a postcode matches only by its province, and a
    qualified entry (`PT:9000..9999`, `IT:SS`) applies to its own country only."""
    if dest.country not in area.countries:
        return None
    code = normalize_postcode(dest.postcode)
    if code and any(
        pattern.matches_normalized(code) for pattern in _patterns_for(area.parsed_patterns, dest)
    ):
        return Specificity.POSTCODE
    if dest.state and dest.state in _states_for(area.parsed_states, dest):
        return Specificity.PROVINCE
    return None


def _patterns_for(patterns: Iterable[PostcodePattern], dest: Destination) -> list[PostcodePattern]:
    """The patterns that apply in `dest`'s country: its own and the unqualified ones."""
    return [pattern for pattern in patterns if pattern.country in ("", dest.country)]


def _states_for(states: Iterable[tuple[str, str]], dest: Destination) -> set[str]:
    """The province codes that apply in `dest`'s country: its own and the unqualified ones."""
    return {code for country, code in states if country in ("", dest.country)}


def _all_digits(*texts: str) -> bool:
    return all(text.isascii() and text.isdigit() for text in texts)


def _digit_blocks(low: str, high: str) -> list[str] | None:
    """The prefixes that cover exactly the heads LOW..HIGH of a digit range (`07000..09999` ->
    `07`, `08`, `09`), or None when a bound is not all digits."""
    if not _all_digits(low, high):
        return None
    width, start, stop = len(low), int(low), int(high)
    blocks: list[str] = []
    while start <= stop:
        size = 0
        while (
            size < width and start % 10 ** (size + 1) == 0 and start + 10 ** (size + 1) - 1 <= stop
        ):
            size += 1
        blocks.append(str(start).zfill(width)[: width - size])
        start += 10**size
    return blocks


def _prefix_within(prefix: str, outer: PostcodePattern) -> bool:
    """True when every postcode starting with `prefix` matches `outer`."""
    if outer.kind == "prefix":
        return prefix.startswith(outer.low)
    if outer.kind == "range":
        width = len(outer.low)
        if len(prefix) >= width:
            return outer.low <= prefix[:width] <= outer.high
        # A shorter digit prefix (`90` in `9000..9499`): all its digit heads, 9000..9099.
        pad = width - len(prefix)
        return (
            _all_digits(prefix, outer.low, outer.high)
            and outer.low <= prefix + "0" * pad
            and prefix + "9" * pad <= outer.high
        )
    return False  # an exact postcode never holds a whole prefix


def _pattern_within(inner: PostcodePattern, outers: Sequence[PostcodePattern]) -> bool:
    """True when every postcode `inner` matches is matched by one of `outers`."""
    if inner.kind == "exact":
        return any(outer.matches_normalized(inner.low) for outer in outers)
    if inner.kind == "prefix":
        return any(_prefix_within(inner.low, outer) for outer in outers)
    blocks = _digit_blocks(inner.low, inner.high)
    if blocks is not None:
        return all(any(_prefix_within(block, outer) for outer in outers) for block in blocks)
    # A range with letters: proven only when one outer pattern holds both bounds' heads.
    return any(
        (
            outer.kind == "prefix"
            and inner.low.startswith(outer.low)
            and inner.high.startswith(outer.low)
        )
        or (
            outer.kind == "range"
            and len(outer.low) <= len(inner.low)
            and outer.low <= inner.low[: len(outer.low)]
            and inner.high[: len(outer.low)] <= outer.high
        )
        for outer in outers
    )


def zone_within_area(zone: ZoneSnap, area: SpecialAreaSnap, dest: Destination) -> bool:
    """True when the entries through which `zone` matches `dest` all lie inside `area`.

    The zone then prices the area itself (spec 06 cases 2 and 4: a vendor's own islands zone with
    weight bands). A zone that is not narrowed, or that reaches `dest` through a province or a
    prefix wider than the area (a regional zone by province around a postcode-defined island, the
    whole country through `0*`..`9*`), proves nothing: the area keeps its default.
    """
    code = normalize_postcode(dest.postcode)
    matched_patterns = [
        pattern
        for pattern in _patterns_for(zone.parsed_patterns, dest)
        if code and pattern.matches_normalized(code)
    ]
    matched_states = (
        {dest.state} & _states_for(zone.parsed_states, dest) if dest.state else set[str]()
    )
    if not matched_patterns and not matched_states:
        return False
    area_patterns = _patterns_for(area.parsed_patterns, dest)
    return all(_pattern_within(pattern, area_patterns) for pattern in matched_patterns) and (
        matched_states <= _states_for(area.parsed_states, dest)
    )


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
    zone: ZoneSnap | None = None,
) -> SpecialAreaDecision | None:
    """Which special area `dest` is in and the treatment a method gives it (`treatments` by area
    code), or None when `dest` is in no special area. Without a treatment of its own, the method
    gives the default: an indicative cost subject to quote.

    `zone` is the method's zone. When it reaches `dest` only through entries inside the area
    (`zone_within_area`), it already prices the area, so without a treatment of its own the
    method keeps its normal rate: no surcharge, not indicative, no quote. An extra-VAT territory
    always keeps the default: a method that prices one sets a treatment of its own.
    """
    area = resolve_special_area(areas, dest)
    if area is None:
        return None
    treatment = treatments.get(area.code)
    if treatment is not None:
        return SpecialAreaDecision(area, treatment, is_default=False)
    if zone is not None and area.kind != EXTRA_VAT and zone_within_area(zone, area, dest):
        return SpecialAreaDecision(area, PRICED_BY_ZONE_TREATMENT, is_default=False)
    return SpecialAreaDecision(area, DEFAULT_AREA_TREATMENT, is_default=True)


# --- The quote of one vendor's package (SHP.3)

_THOUSANDTH: Final = Decimal("0.001")

# Kinds of rate.
METHOD_RATE: Final = "method"
PICKUP_RATE: Final = "pickup"

# Why a package gets no rate (`NeedsQuote.reason`, `Blocked.reason`).
COUNTRY_NOT_COVERED: Final = "country_not_covered"
OVER_MAX_WEIGHT: Final = "over_max_weight"
NO_WEIGHT: Final = "no_weight"
NO_BAND: Final = "no_band"
AREA_QUOTE_ONLY: Final = "area_quote_only"
AREA_BLOCKED: Final = "area_blocked"
QUOTES_DISABLED: Final = "quotes_disabled"

_MESSAGES: Final = {
    COUNTRY_NOT_COVERED: "This vendor has no shipping rate for the destination.",
    OVER_MAX_WEIGHT: "The package is heavier than the vendor's shipping rates cover.",
    NO_WEIGHT: "The package has no weight, so no shipping rate can be computed.",
    NO_BAND: "The vendor has no shipping rate for the weight of the package.",
    AREA_QUOTE_ONLY: "Shipping to this area is priced on request.",
    AREA_BLOCKED: "The vendor does not ship to this area.",
    QUOTES_DISABLED: "The destination needs a quote, and quotes are not available.",
}


@dataclass(frozen=True)
class PackageLine:
    """A line of a package: unit weight in kg, quantity and the line's net goods value."""

    weight_kg: Decimal
    quantity: int
    net_goods: Decimal


@dataclass(frozen=True)
class VendorPackage:
    """The lines of ONE vendor (the cart is split into one package per vendor, spec rule 1)."""

    vendor_id: int
    lines: tuple[PackageLine, ...]

    @property
    def weight_kg(self) -> Decimal:
        """Sum of weight x quantity, kg to 3 decimals, half up."""
        total = sum((line.weight_kg * line.quantity for line in self.lines), Decimal(0))
        return total.quantize(_THOUSANDTH, rounding=ROUND_HALF_UP)

    @property
    def goods_net(self) -> Decimal:
        """Net goods value, the base of the free-shipping threshold."""
        total = sum((line.net_goods for line in self.lines), ZERO_AMOUNT)
        return total.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class BandSnap:
    """A weight band: (from_kg, to_kg], `to_kg` None meaning unlimited."""

    from_kg: Decimal
    to_kg: Decimal | None
    cost_net: Decimal
    surcharge_net: Decimal = ZERO_AMOUNT

    def holds(self, weight_kg: Decimal) -> bool:
        return self.from_kg < weight_kg and (self.to_kg is None or weight_kg <= self.to_kg)

    @property
    def price(self) -> Decimal:
        return self.cost_net + self.surcharge_net


@dataclass(frozen=True)
class MethodSnap:
    """A shipping method as the quote sees it. `treatments` are its special-area treatments by
    area code; `over_max_weight` empty means the platform default."""

    key: str
    name: str
    vendor_id: int
    zone: ZoneSnap
    carrier_code: str
    bands: tuple[BandSnap, ...]
    carrier_active: bool = True
    service_level: str = STANDARD
    min_delivery_days: int | None = None
    max_delivery_days: int | None = None
    is_default: bool = False
    free_shipping_threshold: Decimal | None = None
    tax_treatment: str = DEFAULT_TAX_TREATMENT
    over_max_weight: str = PLATFORM_DEFAULT
    treatments: Mapping[str, AreaTreatment] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class PickupSnap:
    vendor_id: int
    key: str
    label: str
    cost_net: Decimal
    address: str = ""


@dataclass(frozen=True)
class ShippingConfigSnap:
    """Everything `quote_shipping` reads. `default_over_max_weight` is the platform policy for
    methods that set none: D-06 is open and its proposal is a quote."""

    methods: tuple[MethodSnap, ...]
    pickups: tuple[PickupSnap, ...] = ()
    areas: tuple[SpecialAreaSnap, ...] = ()
    quotes_enabled: bool = True
    default_over_max_weight: str = QUOTE


@dataclass(frozen=True)
class ShippingRate:
    """One choice offered to the buyer, net of VAT. `free_shipping` means the band price was
    waived by the method's threshold (a special-area surcharge may still be charged). Besides the
    price it carries what the tax engine needs (`tax_treatment`, `zone_code`, `territory_code`
    of an extra-VAT area)."""

    key: str
    kind: str
    label: str
    net: Decimal
    vendor_id: int
    is_default: bool = False
    is_indicative: bool = False
    free_shipping: bool = False
    min_days: int | None = None
    max_days: int | None = None
    tax_treatment: str = DEFAULT_TAX_TREATMENT
    zone_code: str = ""
    carrier_code: str = ""
    service_level: str = ""
    area_code: str = ""
    territory_code: str = ""
    address: str = ""


@dataclass(frozen=True)
class Rates:
    """Exact rates: the buyer picks one (the default is preselected)."""

    rates: tuple[ShippingRate, ...]


@dataclass(frozen=True)
class IndicativeRates:
    """Estimates subject to quote (special areas, SHP.14). At checkout it behaves like
    NeedsQuote; the quote request starts from the estimate. A pickup rate stays exact."""

    rates: tuple[ShippingRate, ...]


@dataclass(frozen=True)
class NeedsQuote:
    """No rate: the vendor quotes the price (SHP.9). `pickups` stay available."""

    reason: str
    message: str
    pickups: tuple[ShippingRate, ...] = ()


@dataclass(frozen=True)
class Blocked:
    """No rate and no quote: the package cannot be shipped, with a message for the buyer."""

    reason: str
    message: str


type ShippingOutcome = Rates | IndicativeRates | NeedsQuote | Blocked


@dataclass(frozen=True)
class _Priced:
    rate: ShippingRate | None = None
    quote: str = ""
    blocked: str = ""


def _band_for(method: MethodSnap, weight: Decimal, default_policy: str) -> BandSnap | str:
    """The band that prices `weight`, or the reason there is none (OVER_MAX_WEIGHT with the
    policy applied by the caller, NO_BAND for a gap)."""
    for band in method.bands:
        if band.holds(weight):
            return band
    bounded = [band for band in method.bands if band.to_kg is not None]
    if bounded and len(bounded) == len(method.bands):
        last = max(bounded, key=lambda band: band.to_kg or ZERO_AMOUNT)
        if last.to_kg is not None and weight > last.to_kg:
            policy = method.over_max_weight or default_policy
            if policy == LAST_BAND:
                return last
            return BLOCK if policy == BLOCK else QUOTE
    return NO_BAND


def _price_method(
    method: MethodSnap, package: VendorPackage, dest: Destination, cfg: ShippingConfigSnap
) -> _Priced:
    weight = package.weight_kg
    decision = special_area_treatment(cfg.areas, method.treatments, dest, method.zone)
    if decision is not None:
        if decision.is_blocked:
            return _Priced(blocked=AREA_BLOCKED)
        if decision.is_unavailable:
            return _Priced()
        if decision.requests_quote and not decision.offers_rate:
            return _Priced(quote=AREA_QUOTE_ONLY)
    band = _band_for(method, weight, cfg.default_over_max_weight)
    if isinstance(band, str):
        if band == BLOCK:
            return _Priced(blocked=OVER_MAX_WEIGHT)
        return _Priced(quote=OVER_MAX_WEIGHT if band == QUOTE else NO_BAND)
    threshold = method.free_shipping_threshold
    free = threshold is not None and package.goods_net >= threshold
    base = ZERO_AMOUNT if free else band.price
    min_days, max_days = method.min_delivery_days, method.max_delivery_days
    indicative = False
    area_code = territory = ""
    net = base.quantize(_CENT, rounding=ROUND_HALF_UP)
    if decision is not None:
        cost = decision.cost(base, weight)
        net = cost if cost is not None else net
        min_days, max_days = decision.delivery_days(min_days, max_days)
        indicative = decision.is_indicative
        area_code, territory = decision.area.code, decision.area.territory_code
    return _Priced(
        rate=ShippingRate(
            key=method.key,
            kind=METHOD_RATE,
            label=method.name,
            net=net,
            vendor_id=method.vendor_id,
            is_default=method.is_default,
            is_indicative=indicative,
            free_shipping=free,
            min_days=min_days,
            max_days=max_days,
            tax_treatment=method.tax_treatment,
            zone_code=method.zone.code,
            carrier_code=method.carrier_code,
            service_level=method.service_level,
            area_code=area_code,
            territory_code=territory,
        )
    )


def _pickup_rates(vendor_id: int, cfg: ShippingConfigSnap) -> tuple[ShippingRate, ...]:
    return tuple(
        ShippingRate(
            key=pickup.key,
            kind=PICKUP_RATE,
            label=pickup.label,
            net=pickup.cost_net.quantize(_CENT, rounding=ROUND_HALF_UP),
            vendor_id=vendor_id,
            address=pickup.address,
        )
        for pickup in sorted(cfg.pickups, key=lambda item: item.key)
        if pickup.vendor_id == vendor_id
    )


def _no_rate(reason: str, cfg: ShippingConfigSnap) -> NeedsQuote | Blocked:
    """A package nobody can price: a quote when quotes are enabled, otherwise blocked."""
    if cfg.quotes_enabled:
        return NeedsQuote(reason, _MESSAGES[reason])
    return Blocked(reason, _MESSAGES[reason])


def quote_shipping(
    package: VendorPackage, dest: Destination, cfg: ShippingConfigSnap
) -> ShippingOutcome:
    """The shipping of ONE vendor's package to `dest`, net of VAT (spec 06 §6.2).

    Rates: one per method of the resolved zone (default first), then pickups.
    IndicativeRates: every priced method is a special-area estimate subject to quote.
    NeedsQuote: no zone, weight 0 or beyond the bands with the "quote" policy, a quote-only area.
    Blocked: quotes are disabled, or the weight policy or the special area blocks the package.
    Never a silent empty list. A pickup is offered only in a covered zone (case 13) and never
    turns a quote into Rates, except when quotes are disabled and it is all that is left.
    """
    own = [m for m in cfg.methods if m.vendor_id == package.vendor_id and m.carrier_active]
    zones = {(m.zone.vendor_id, m.zone.code): m.zone for m in own}
    zone = resolve_zone(zones.values(), package.vendor_id, dest)
    if zone is None:
        return _no_rate(COUNTRY_NOT_COVERED, cfg)
    if package.weight_kg <= 0:
        return _no_rate(NO_WEIGHT, cfg)
    in_zone = [m for m in own if (m.zone.vendor_id, m.zone.code) == (zone.vendor_id, zone.code)]
    priced = [_price_method(m, package, dest, cfg) for m in in_zone]
    rates = [p.rate for p in priced if p.rate is not None]
    pickups = _pickup_rates(package.vendor_id, cfg)

    def order(rate: ShippingRate) -> tuple[bool, Decimal, str]:
        return (not rate.is_default, rate.net, rate.key)

    exact = sorted((r for r in rates if not r.is_indicative), key=order)
    estimates = sorted((r for r in rates if r.is_indicative), key=order)
    if exact:
        return Rates((*exact, *pickups))
    quote = next((p.quote for p in priced if p.quote), "")
    if estimates and cfg.quotes_enabled:
        return IndicativeRates((*estimates, *pickups))
    if not cfg.quotes_enabled and pickups:
        return Rates(pickups)
    if estimates or quote:
        reason = quote or QUOTES_DISABLED
        if cfg.quotes_enabled:
            return NeedsQuote(reason, _MESSAGES[reason], pickups)
        return Blocked(reason if quote else QUOTES_DISABLED, _MESSAGES[reason])
    blocked = next((p.blocked for p in priced if p.blocked), "")
    if blocked:
        if pickups:
            return Rates(pickups)
        return Blocked(blocked, _MESSAGES[blocked])
    return _no_rate(COUNTRY_NOT_COVERED, cfg)
