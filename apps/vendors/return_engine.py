"""A vendor's return policy: the pure rules (VEN.40, follow-up of the SEO.1 review).

No ORM. The 14-day floor is the EU withdrawal period for distance and off-premises sales to
consumers (Directive 2011/83/EU art. 9, art. 52 Codice del consumo), so a window under it is
refused. A vendor that has set nothing is on the floor with the shop paying the return
(owner decision, assumed, to confirm: without a vendor statement the consumer was never told
that they pay, so the trader bears it, CRD art. 6(1)(i) and 14(1)); SEO.14 states exactly the
values this module hands out, so the structured data and the product page cannot disagree.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

from apps.core.countries import ISO_3166_ALPHA2

MIN_WINDOW_DAYS: Final = 14
MAX_WINDOW_DAYS: Final = 3650
URL_MAX: Final = 500

CUSTOMER: Final = "customer"
VENDOR: Final = "vendor"
PAYERS: Final = (CUSTOMER, VENDOR)

POST: Final = "post"
DROP_OFF: Final = "drop_off"
IN_STORE: Final = "in_store"
METHODS: Final = (POST, DROP_OFF, IN_STORE)

#: The fields a proposal is made of (and the only ones that exist).
FIELDS: Final = (
    "return_window_days",
    "return_shipping_paid_by",
    "return_methods",
    "returns_page_url",
    "return_country",
)


@dataclass(frozen=True)
class ReturnPolicy:
    """The values SEO.14 and the product page state. `is_default` marks the fallback."""

    return_window_days: int
    return_shipping_paid_by: str
    return_methods: tuple[str, ...]
    returns_page_url: str
    return_country: str
    is_default: bool = False


def default_policy(country: str) -> ReturnPolicy:
    """The policy of a vendor that has no approved data: the floor, the shop pays."""
    return ReturnPolicy(
        return_window_days=MIN_WINDOW_DAYS,
        return_shipping_paid_by=VENDOR,
        return_methods=(),
        returns_page_url="",
        return_country=country,
        is_default=True,
    )


def url_error(value: str) -> str | None:
    """Why `value` is not an acceptable returns page address (https only), or None."""
    if len(value) > URL_MAX:
        return f"The address is longer than {URL_MAX} characters."
    if any(c.isspace() or ord(c) < 32 for c in value):
        return "The address must not contain spaces."
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        return "This is not a valid address."
    if parts.scheme != "https" or not host:
        return "The address must start with https://."
    if "@" in parts.netloc:
        return "The address must not contain a user name or a password."
    return None


def policy_errors(values: Mapping[str, Any]) -> dict[str, str]:
    """One message per invalid field; an empty mapping means the proposal is acceptable."""
    errors: dict[str, str] = {}
    days = values.get("return_window_days")
    if isinstance(days, bool) or not isinstance(days, int):
        errors["return_window_days"] = "The return window must be a whole number of days."
    elif days < MIN_WINDOW_DAYS:
        errors["return_window_days"] = (
            f"The return window cannot be shorter than {MIN_WINDOW_DAYS} days."
        )
    elif days > MAX_WINDOW_DAYS:
        errors["return_window_days"] = (
            f"The return window cannot be longer than {MAX_WINDOW_DAYS} days."
        )
    if values.get("return_shipping_paid_by") not in PAYERS:
        errors["return_shipping_paid_by"] = "Choose who pays the return shipping."
    methods = values.get("return_methods")
    if (
        not isinstance(methods, Sequence)
        or isinstance(methods, str)
        or not methods
        or any(m not in METHODS for m in methods)
    ):
        errors["return_methods"] = "Choose at least one return method from the list."
    url = values.get("returns_page_url") or ""
    if url and (reason := url_error(str(url))):
        errors["returns_page_url"] = reason
    if str(values.get("return_country") or "") not in ISO_3166_ALPHA2:
        errors["return_country"] = "Choose the country returns are sent to."
    return errors
