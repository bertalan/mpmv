"""What the checkout remembers between its steps (ORD.5): the buyer's details, the VIES answer
and the shipping choices, in the visitor's session. Nothing is saved to the database before
`place_order`; the session is the only store, and it is cleared when the order is placed.

`commission_snapshots` is the one seam to the vendor commission rule (VEN.2, decision D-04 is
open): until a rule exists it returns nothing, and the review step says that ordering is not
open yet instead of placing an order with an invented commission.
"""

from collections.abc import Iterable
from typing import Any

from django.http import HttpRequest

SESSION_KEY = "checkout_state"
CONFIRMATION_KEY = "checkout_confirmation"


def load(request: HttpRequest) -> dict[str, Any]:
    value = request.session.get(SESSION_KEY)
    return dict(value) if isinstance(value, dict) else {}


def save(request: HttpRequest, **updates: Any) -> dict[str, Any]:
    data = load(request)
    data.update(updates)
    request.session[SESSION_KEY] = data
    return data


def clear(request: HttpRequest) -> None:
    request.session.pop(SESSION_KEY, None)


def shipping_choices(data: dict[str, Any]) -> dict[int, str]:
    raw = data.get("shipping")
    if not isinstance(raw, dict):
        return {}
    return {int(vendor_id): str(key) for vendor_id, key in raw.items() if str(vendor_id).isdigit()}


def commission_snapshots(vendor_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """The commission rule snapshot of each vendor, by vendor id (VEN.2 supplies it)."""
    return {}
