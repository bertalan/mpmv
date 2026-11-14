"""Pure rules of the checkout legal step (ORD.17, owner decision D-40, counsel variant A). No ORM.

Box 1 (the marketplace terms and each seller's terms, the privacy notice only given as a link) is
for every buyer. Box 2 (the specific approval of the onerous clauses, artt. 1341-1342 c.c.) is for
a business buyer only, and only with an Italian party: the operator established in Italy, or a
seller established in Italy or whose terms choose Italian law. The VIES answer does not matter:
the buyer declared to act as a business and gave a VAT number.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from apps.vendors.engine import ITALY

#: The form field names of the two boxes.
BOX1: str = "accept_terms"
BOX2: str = "accept_onerous"

#: The clause list of one party: (article number, title).
Clauses = Sequence[tuple[str, str]]


@dataclass(frozen=True)
class Party:
    """One party whose onerous clauses may be listed: the platform terms or one seller."""

    kind: str  # "platform" | "seller"
    label: str
    established_country: str
    governing_law: str  # "" for the platform terms (the operator's country decides)
    clauses: Clauses


def buyer_is_business(*, chose_business: bool, vat_id: str) -> bool:
    """A business buyer chose to buy as a business and gave a VAT number (VIES is irrelevant)."""
    return bool(chose_business) and bool(vat_id.strip())


def party_is_italian(party: Party, *, operator_country: str) -> bool:
    """The platform terms are Italian when the operator is established in Italy (read from
    `current_operator()`); a seller when it is established in Italy or its terms choose
    Italian law."""
    if party.kind == "platform":
        return operator_country == ITALY
    return party.established_country == ITALY or party.governing_law == ITALY


def box2_parties(parties: Iterable[Party], *, business: bool, operator_country: str) -> list[Party]:
    """The parties whose clauses box 2 lists; empty for a consumer (box 2 is then not shown)."""
    if not business:
        return []
    return [p for p in parties if party_is_italian(p, operator_country=operator_country)]


def box2_required(parties: Iterable[Party], *, business: bool, operator_country: str) -> bool:
    return bool(box2_parties(parties, business=business, operator_country=operator_country))


def acceptance_errors(*, box1: bool, box2: bool, box2_shown: bool) -> list[str]:
    """Codes of what is missing; the view words them. A box not shown is never required."""
    errors = []
    if not box1:
        errors.append("box1")
    if box2_shown and not box2:
        errors.append("box2")
    return errors
