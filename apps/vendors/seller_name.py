"""The public seller name of a vendor (SEO.15), for every public surface.

A vendor's public name is its shop or trading name, never the bare legal name of a natural
person. The rule is pure (`public_seller_name`); `public_name(vendor)` adapts a `Vendor`. It lives
in the vendors layer so that the catalog, CMS, events and search can use it as well as the
structured data (`apps.seo.engine` re-exports it)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VendorNameFacts:
    """What decides the public seller name. `natural_person` is true for a sole trader (or an
    vendor whose legal form is not filled in yet: when in doubt, a person)."""

    display_name: str
    legal_name: str = ""
    first_name: str = ""
    last_name: str = ""
    natural_person: bool = False


def normalize_name(name: str) -> str:
    """A name compared without case, accent, punctuation or spacing differences."""
    decomposed = unicodedata.normalize("NFKD", name)
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", bare.casefold()).split())


def reveals_person(name: str, person_names: Collection[str]) -> bool:
    """True when `name` is a natural person's name: equal to it after normalisation (accents,
    case, punctuation, spacing), or equal to it with the spaces removed ('Mariorossi'). A trading
    name that merely contains the person's name ('Ceramiche Mario Rossi') is the vendor's chosen
    shop name and is kept (SEO.15)."""
    shown = normalize_name(name)
    if not shown:
        return False
    squeezed = shown.replace(" ", "")
    return any(squeezed == person.replace(" ", "") for person in person_names if person)


def natural_person_names(facts: VendorNameFacts) -> frozenset[str]:
    """The normalised names a natural person is known by: the legal name and the first and last
    name in both orders. Empty for a vendor that is not a natural person."""
    if not facts.natural_person:
        return frozenset()
    names = {facts.legal_name}
    if facts.first_name and facts.last_name:
        names |= {f"{facts.first_name} {facts.last_name}", f"{facts.last_name} {facts.first_name}"}
    return frozenset(normalize_name(name) for name in names if name.strip())


def public_seller_name(facts: VendorNameFacts) -> str | None:
    """The name structured data may show for the vendor: its public shop or trading name, never
    the legal name of a natural person. None when that is the only name there is (the seller
    node and every reference to it are then left out, no error). One rule for SEO.1 and SEO.7."""
    name = " ".join(facts.display_name.split())
    if not name or reveals_person(name, natural_person_names(facts)):
        return None
    return name


def name_facts(vendor: Any) -> VendorNameFacts:
    sole_trader = vendor.legal_form == vendor.LegalForm.SOLE_PROPRIETORSHIP
    unspecified_individual = not vendor.legal_form  # when in doubt, a person
    return VendorNameFacts(
        display_name=str(vendor.display_name),
        legal_name=str(vendor.legal_name),
        first_name=str(vendor.first_name),
        last_name=str(vendor.last_name),
        natural_person=sole_trader or unspecified_individual,
    )


def public_name(vendor: Any) -> str | None:
    """The name a public page may show for `vendor`; None when the only name is a person's."""
    return public_seller_name(name_facts(vendor))
