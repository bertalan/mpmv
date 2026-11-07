"""Pure vendor rules (no ORM): status state machine, slug generation, address shape."""

from collections.abc import Container, Mapping
from typing import Final

from django.utils.text import slugify

from apps.core.countries import InvalidCountry, normalize_country

# --- Status state machine (spec 04 §4.2)

APPLICANT: Final = "applicant"
ONBOARDING: Final = "onboarding"
ACTIVE: Final = "active"
SUSPENDED: Final = "suspended"
CLOSED: Final = "closed"

STATUSES: Final = (APPLICANT, ONBOARDING, ACTIVE, SUSPENDED, CLOSED)

# Forward along the lifecycle, reinstatement after a suspension, and closure from any open
# status (a rejected application is closed too). Closed is terminal.
TRANSITIONS: Final[Mapping[str, frozenset[str]]] = {
    APPLICANT: frozenset({ONBOARDING, CLOSED}),
    ONBOARDING: frozenset({ACTIVE, CLOSED}),
    ACTIVE: frozenset({SUSPENDED, CLOSED}),
    SUSPENDED: frozenset({ACTIVE, CLOSED}),
    CLOSED: frozenset(),
}


class InvalidTransition(ValueError):
    """Raised when a vendor status change is not allowed by the state machine."""


def allowed_transitions(status: str) -> frozenset[str]:
    if status not in TRANSITIONS:
        raise InvalidTransition(f"unknown vendor status {status!r}")
    return TRANSITIONS[status]


def check_transition(current: str, target: str) -> None:
    if target not in TRANSITIONS:
        raise InvalidTransition(f"unknown vendor status {target!r}")
    if target not in allowed_transitions(current):
        raise InvalidTransition(f"vendor status change {current} → {target} is not allowed")


# --- Slug

SLUG_MAX_LENGTH: Final = 80
SLUG_FALLBACK: Final = "vendor"


def unique_slug(display_name: str, taken: Container[str], max_length: int = SLUG_MAX_LENGTH) -> str:
    """ASCII slug of the display name; `-2`, `-3`… when taken, always within `max_length`."""
    base = slugify(display_name)[:max_length].strip("-") or SLUG_FALLBACK
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = f"-{n}"
        candidate = base[: max_length - len(suffix)].rstrip("-") + suffix
        if candidate not in taken:
            return candidate
        n += 1


# --- Registered address (structured JSON)

ADDRESS_FIELDS: Final = ("line1", "line2", "postal_code", "city", "region", "country")


def address_errors(value: object) -> list[str]:
    """Problems with a registered address; an empty list means it is valid."""
    if not isinstance(value, Mapping):
        return ["The address must be an object."]
    errors: list[str] = []
    for key, item in value.items():
        if key not in ADDRESS_FIELDS:
            errors.append(f"Unknown address field: {key}.")
        elif not isinstance(item, str):
            errors.append(f"Address field {key} must be text.")
    country = value.get("country")
    if isinstance(country, str) and country:
        try:
            normalize_country(country)
        except InvalidCountry:
            errors.append(f"Address country: {country!r} is not an ISO code.")
    return errors
