"""A seller's sales terms: the pure rules (VEN.35, owner decision D-40, counsel L-1 to L-6).

No ORM. The consumer rules are fixed by law and never depend on what a seller writes:

- the consumer's court is always the court of the consumer's residence (art. 66-bis Codice del
  consumo; for EU consumers artt. 17-19 Reg. (EU) 1215/2012), so the seller's B2B court is
  never given for a consumer;
- the consumer bears the direct cost of returning goods only if the seller's terms say so and
  the pre-contract information shows it (artt. 56-57); otherwise the seller pays;
- there is no fee for handling a refund or a return, so no such value exists here.
"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

SELLER: Final = "seller"
CONSUMER: Final = "consumer"
#: The values of the return-cost setting; empty means "not set", which is the seller.
RETURN_COST_BEARERS: Final = (SELLER, CONSUMER)

#: The fields a version is made of (and the only ones that exist).
FIELDS: Final = (
    "body",
    "clauses",
    "governing_law",
    "b2b_court",
    "delivery_days",
    "return_cost_bearer",
    "return_cost_info",
)

BODY_MAX: Final = 60_000
CLAUSES_MAX: Final = 60
ARTICLE_MAX: Final = 40
CLAUSE_TITLE_MAX: Final = 200
COURT_MAX: Final = 120
INFO_MAX: Final = 1_000
DELIVERY_DAYS_MAX: Final = 365

#: What the consumer-court rule is, as the recap states it.
CONSUMER_COURT_RULE: Final = "residence"


def consumer_bears_return_cost(bearer: str, info: str) -> bool:
    """True only when the terms say the consumer pays AND the pre-contract information shows it.

    An empty setting, a missing information text or any other value means the seller pays.
    """
    return bearer == CONSUMER and bool(info.strip())


def competent_court(b2b_court: str, registered_city: str, *, buyer_is_business: bool) -> str | None:
    """The court named for a business buyer; None for a consumer (the residence rule applies).

    An empty seller choice falls back to the seller's registered office.
    """
    if not buyer_is_business:
        return None
    return b2b_court.strip() or registered_city.strip() or None


_CLAUSE_LINE = re.compile(r"^\s*(?P<article>[^|:\-]+?)\s*[|:\-]\s*(?P<title>.+?)\s*$")


def parse_clauses(text: str) -> list[dict[str, str]]:
    """One clause per line, "article | title" (a dash or a colon also separates them)."""
    clauses = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _CLAUSE_LINE.match(line)
        if match is None:
            clauses.append({"article": line.strip(), "title": ""})
        else:
            clauses.append({"article": match["article"], "title": match["title"]})
    return clauses


def clauses_text(clauses: Sequence[Mapping[str, str]]) -> str:
    return "\n".join(f"{c['article']} | {c['title']}" for c in clauses)


def terms_errors(values: Mapping[str, Any]) -> dict[str, str]:
    """{field: reason} of a version that cannot be published; empty when it is complete."""
    errors: dict[str, str] = {}
    body = str(values.get("body") or "")
    if not body.strip():
        errors["body"] = "The text of the terms is required."
    elif len(body) > BODY_MAX:
        errors["body"] = f"The text is longer than {BODY_MAX} characters."
    clauses = values.get("clauses")
    if not isinstance(clauses, Sequence) or isinstance(clauses, str) or not clauses:
        errors["clauses"] = "List the onerous clauses (art. 1341(2) c.c.), one per line."
    else:
        rows = [c for c in clauses if isinstance(c, Mapping)]
        if len(rows) != len(clauses) or len(rows) > CLAUSES_MAX:
            errors["clauses"] = f"At most {CLAUSES_MAX} clauses, each with an article and a title."
        elif any(
            not str(c.get("article", "")).strip()
            or not str(c.get("title", "")).strip()
            or len(str(c["article"])) > ARTICLE_MAX
            or len(str(c["title"])) > CLAUSE_TITLE_MAX
            for c in rows
        ):
            errors["clauses"] = "Every clause needs an article number and a title."
    if not str(values.get("governing_law") or "").strip():
        errors["governing_law"] = "Choose the country whose law governs the terms."
    days = values.get("delivery_days")
    if not isinstance(days, int) or isinstance(days, bool) or not 0 <= days <= DELIVERY_DAYS_MAX:
        errors["delivery_days"] = f"Give the delivery time in days, 0 to {DELIVERY_DAYS_MAX}."
    bearer = str(values.get("return_cost_bearer") or "")
    if bearer and bearer not in RETURN_COST_BEARERS:
        errors["return_cost_bearer"] = "Choose the seller, the consumer, or leave it empty."
    if len(str(values.get("b2b_court") or "")) > COURT_MAX:
        errors["b2b_court"] = f"At most {COURT_MAX} characters."
    if len(str(values.get("return_cost_info") or "")) > INFO_MAX:
        errors["return_cost_info"] = f"At most {INFO_MAX} characters."
    return errors


def content_hash(values: Mapping[str, Any]) -> str:
    """SHA-256 over every field of a version, so what a buyer relied on stays provable."""
    payload = {name: values.get(name) for name in FIELDS}
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode()).hexdigest()
