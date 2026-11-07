"""Pure shipping rules (no ORM) behind the models and constraints (SHP.1, SHP.12).

Zone matching and precedence live here (spec 06 §6.2): a vendor's own zone wins over platform
zones for that vendor, then the lowest priority, then the most specific match (postcode >
province > explicit country > country group), and the rest of the world (`*`) only as a last
resort. SHP.2 checks the legacy regression cases 1-7 against these functions, normalises the
destination country once (`EL` -> `GR`, `UK` -> `GB`) and parses a zone's postcode patterns once,
when its snapshot is built. The quote calculation (SHP.3) also lives here when that task adds it.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
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
