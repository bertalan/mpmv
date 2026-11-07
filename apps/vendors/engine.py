"""Pure vendor rules (no ORM): status state machine, slug generation, address shape, member
roles and their permissions."""

from collections.abc import Collection, Container, Mapping
from enum import StrEnum
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


# --- Member roles and permissions (spec 02 §2.2, CORE.2)

OWNER: Final = "owner"
STAFF: Final = "staff"

ROLES: Final = (OWNER, STAFF)


class VendorAction(StrEnum):
    """What a vendor member can do on their own vendor, in the vendor area, API and MCP."""

    ORDERS = "orders"
    QUOTES = "quotes"
    PRODUCTS = "products"
    PROFILE = "profile"  # storefront texts, policies, logo
    PAYOUTS = "payouts"
    BANK_ACCOUNT = "bank_account"  # IBAN / payout account
    TAX_REGIME = "tax_regime"  # tax regime change request
    MEMBERS = "members"  # add and remove members, change roles


# The role matrix. Owner: everything about their own vendor, financial settings included.
# Staff: day-to-day operations only; no payouts, IBAN, tax regime, profile or members.
ROLE_PERMISSIONS: Final[Mapping[str, frozenset[VendorAction]]] = {
    OWNER: frozenset(VendorAction),
    STAFF: frozenset({VendorAction.ORDERS, VendorAction.QUOTES, VendorAction.PRODUCTS}),
}


class MembershipError(ValueError):
    """A vendor membership change that the rules refuse."""


class UnknownRole(MembershipError):
    """The role is not one of `ROLES`."""


class UnknownAction(ValueError):
    """The action is not a `VendorAction`."""


class AlreadyMember(MembershipError):
    """The user is already a member of the vendor."""


class NoOwnerLeft(MembershipError):
    """The change would leave the vendor without an owner."""


def check_role(role: str) -> None:
    if role not in ROLE_PERMISSIONS:
        raise UnknownRole(f"unknown vendor member role {role!r}")


def permissions_for(role: str) -> frozenset[VendorAction]:
    check_role(role)
    return ROLE_PERMISSIONS[role]


def to_action(action: str) -> VendorAction:
    try:
        return VendorAction(action)
    except ValueError:
        raise UnknownAction(f"unknown vendor action {action!r}") from None


def is_allowed(role: str, action: str) -> bool:
    """Whether a member with `role` may perform `action` on their own vendor.

    Unknown roles or actions raise instead of answering False, so a typo fails loudly.
    """
    return to_action(action) in permissions_for(role)


def check_keeps_an_owner(roles_after: Collection[str]) -> None:
    """Every vendor with members keeps at least one owner after a membership change.

    `roles_after` holds the roles of the vendor's members once the change is applied; it also
    means the first member of a vendor must be an owner.
    """
    if OWNER not in roles_after:
        raise NoOwnerLeft("a vendor must keep at least one owner")
