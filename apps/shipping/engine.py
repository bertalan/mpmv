"""Pure shipping rules (no ORM) behind the SHP.1 models and constraints.

Zone resolution and postcode matching (SHP.2) and the quote calculation (SHP.3) also live here
when those tasks add them.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Final

from apps.core.countries import normalize_country

# "Rest of the world" in `ShippingZone.countries`: only alone, and only as a last resort.
WORLD: Final = "*"

# Weight above the last band of a rate (spec 06, D-06). D-06 is open: SHP.1 stores an optional
# per-rate policy and chooses no default. SHP.3 applies the policy, falling back to the
# platform-wide default that D-06 decides when a rate has none (`over_max_weight` is empty).
PLATFORM_DEFAULT: Final = ""
LAST_BAND: Final = "last_band"
QUOTE: Final = "quote"
BLOCK: Final = "block"
OVER_MAX_WEIGHT_POLICIES: Final = (LAST_BAND, QUOTE, BLOCK)

# A weight band as (from_kg, to_kg); to_kg None means unlimited. Bounds are right-inclusive,
# (from_kg, to_kg]: 5.000 kg falls in 0-5 and 5.001 kg in 5-10 (spec 06 rule 3).
Band = tuple[Decimal, Decimal | None]


def normalize_zone_country(raw: str) -> str:
    """ISO 3166-1 alpha-2 code (EL → GR, UK → GB), or WORLD; raises InvalidCountry otherwise."""
    if raw.strip() == WORLD:
        return WORLD
    return normalize_country(raw)


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
