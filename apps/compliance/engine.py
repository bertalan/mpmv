"""Pure compliance rules (no ORM). GPSR product data (CMP.3, spec 11).

GPSR (regulation (EU) 2023/988) article 19: an offer online shows the manufacturer's name and
postal and electronic address; when the manufacturer is not established in the EU, the name and
addresses of the responsible person in the EU (article 16); and information that identifies the
product. The product picture and the warnings in the destination language are checked by the
product page part of CMP.3.

Per-vendor defaults fill what the product leaves empty one whole block at a time (the
manufacturer block, the EU responsible person block), never field by field, so a product never
shows one manufacturer's name with another's address.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from apps.core.countries import EU_MEMBER_STATES, InvalidCountry, normalize_country

MANUFACTURER_FIELDS: Final = (
    "manufacturer_name",
    "manufacturer_address",
    "manufacturer_contact",
    "manufacturer_country",
)
EU_RESPONSIBLE_FIELDS: Final = (
    "eu_responsible_name",
    "eu_responsible_address",
    "eu_responsible_contact",
)
PARTY_FIELDS: Final = (*MANUFACTURER_FIELDS, *EU_RESPONSIBLE_FIELDS)
CONTACT_FIELDS: Final = ("manufacturer_contact", "eu_responsible_contact")

# Missing-data codes, in the order they are reported.
PRODUCT_IDENTIFIER: Final = "product_identifier"

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_WEB = re.compile(r"^https?://[^\s/]+\.[^\s/]+\S*$", re.IGNORECASE)


@dataclass(frozen=True)
class GpsrParties:
    """Who answers for a product: the manufacturer and, when needed, the EU responsible person."""

    manufacturer_name: str = ""
    manufacturer_address: str = ""
    manufacturer_contact: str = ""
    manufacturer_country: str = ""
    eu_responsible_name: str = ""
    eu_responsible_address: str = ""
    eu_responsible_contact: str = ""


@dataclass(frozen=True)
class GpsrFacts:
    """The data a product would be published with: parties after defaults, and the values of
    its identifiers (GTIN, MPN, model number, batch), empty ones included."""

    parties: GpsrParties
    identifiers: Sequence[str] = ()


def _filled(value: str) -> bool:
    return bool(value.strip())


def _has_block(parties: GpsrParties, block: Sequence[str]) -> bool:
    return any(_filled(getattr(parties, name)) for name in block)


def merge_parties(own: GpsrParties | None, defaults: GpsrParties | None) -> GpsrParties:
    """`own` with each empty block taken whole from `defaults`."""
    if own is None:
        return defaults if defaults is not None else GpsrParties()
    if defaults is None:
        return own
    merged = own
    for block in (MANUFACTURER_FIELDS, EU_RESPONSIBLE_FIELDS):
        if not _has_block(own, block):
            merged = replace(merged, **{name: getattr(defaults, name) for name in block})
    return merged


def needs_eu_responsible(country: str) -> bool:
    """Whether a manufacturer in `country` needs an EU responsible person (GPSR art. 16).
    An empty or unknown country is not decided here: it is reported as missing."""
    if not _filled(country):
        return False
    try:
        return normalize_country(country) not in EU_MEMBER_STATES
    except InvalidCountry:
        return False


def gpsr_missing(facts: GpsrFacts) -> tuple[str, ...]:
    """Codes of the mandatory GPSR data that `facts` lacks; empty when it can be published."""
    parties = facts.parties
    missing = [name for name in MANUFACTURER_FIELDS if not _filled(getattr(parties, name))]
    if not any(_filled(value) for value in facts.identifiers):
        missing.append(PRODUCT_IDENTIFIER)
    if needs_eu_responsible(parties.manufacturer_country):
        missing.extend(
            name for name in EU_RESPONSIBLE_FIELDS if not _filled(getattr(parties, name))
        )
    return tuple(missing)


def contact_error(value: str) -> str | None:
    """Why `value` is not an electronic address (an email or an http(s) web address)."""
    value = value.strip()
    if not value or _EMAIL.match(value) or _WEB.match(value):
        return None
    return "Enter an email address or a web address starting with https://."
