"""ISO 3166-1 alpha-2 country codes (spec 01): `GR`, not `EL`; `GB`, not `UK`.

Special VAT territories (Canary Islands, Livigno, Northern Ireland goods rules…) are modelled
by the tax app, not by inventing country codes here.
"""

from collections.abc import Iterable
from typing import Final

ISO_3166_ALPHA2: Final = frozenset(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ
    BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM
    DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS
    GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN
    KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ
    MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM
    PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV
    SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI
    VN VU WF WS YE YT ZA ZM ZW
    """.split()  # noqa: SIM905 - compact, reviewable table
)

EU_MEMBER_STATES: Final = frozenset(
    "AT BE BG CY CZ DE DK EE ES FI FR GR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK".split()  # noqa: SIM905
)

# Non-ISO spellings that show up in VAT and postal data.
ALIASES: Final = {"EL": "GR", "UK": "GB"}


class InvalidCountry(ValueError):
    """Raised for anything that is not an ISO 3166-1 alpha-2 code (after aliases)."""


def normalize_country(raw: str) -> str:
    code = raw.strip().upper()
    code = ALIASES.get(code, code)
    if code not in ISO_3166_ALPHA2:
        raise InvalidCountry(f"{raw!r} is not an ISO 3166-1 alpha-2 country code")
    return code


def is_eu(code: str) -> bool:
    return normalize_country(code) in EU_MEMBER_STATES


def normalize_countries(raw: Iterable[str]) -> list[str]:
    """A set of country codes, normalized, sorted and without duplicates."""
    return sorted({normalize_country(code) for code in raw})


def default_allowed_vendor_countries() -> list[str]:
    """Where vendors may be established until a platform owner changes it: the EU-27 (D-21,
    CMP.13). The default of `PlatformOperator.allowed_vendor_countries`."""
    return sorted(EU_MEMBER_STATES)
