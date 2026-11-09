"""CORE.2: the vendor member role matrix and the "a vendor keeps an owner" rule (spec 02 §2.2).

The expected matrix is written out here on purpose, independently of the engine: a change to who
may do what has to change this table too.
"""

import pytest

from apps.vendors.engine import (
    OWNER,
    ROLES,
    STAFF,
    NoOwnerLeft,
    UnknownAction,
    UnknownRole,
    VendorAction,
    check_keeps_an_owner,
    check_role,
    is_allowed,
    permissions_for,
)

# Spec 02 §2.2: owner = everything about their own vendor, financial settings included;
# staff = orders, quotes, products; no payouts, IBAN or tax regime. Vendor API keys act for the
# whole vendor, so only owners issue them (CORE.13). Both roles manage the vendor's events by
# default (spec 15 "Services", EVT.3).
EXPECTED: dict[str, dict[VendorAction, bool]] = {
    OWNER: {
        VendorAction.ORDERS: True,
        VendorAction.QUOTES: True,
        VendorAction.PRODUCTS: True,
        VendorAction.PROFILE: True,
        VendorAction.PAYOUTS: True,
        VendorAction.BANK_ACCOUNT: True,
        VendorAction.TAX_REGIME: True,
        VendorAction.MEMBERS: True,
        VendorAction.API_KEYS: True,
        VendorAction.EVENTS: True,
    },
    STAFF: {
        VendorAction.ORDERS: True,
        VendorAction.QUOTES: True,
        VendorAction.PRODUCTS: True,
        VendorAction.PROFILE: False,
        VendorAction.PAYOUTS: False,
        VendorAction.BANK_ACCOUNT: False,
        VendorAction.TAX_REGIME: False,
        VendorAction.MEMBERS: False,
        VendorAction.API_KEYS: False,
        VendorAction.EVENTS: True,
    },
}

MATRIX = [
    (role, action, allowed) for role, row in EXPECTED.items() for action, allowed in row.items()
]


def test_roles_are_owner_and_staff() -> None:
    assert ROLES == ("owner", "staff")


def test_expected_matrix_covers_every_role_and_every_action() -> None:
    assert set(EXPECTED) == set(ROLES)
    for row in EXPECTED.values():
        assert set(row) == set(VendorAction)


@pytest.mark.parametrize(("role", "action", "allowed"), MATRIX)
def test_role_matrix(role: str, action: VendorAction, allowed: bool) -> None:
    assert is_allowed(role, action) is allowed


@pytest.mark.parametrize(("role", "action", "allowed"), MATRIX)
def test_role_matrix_accepts_plain_strings(role: str, action: VendorAction, allowed: bool) -> None:
    assert is_allowed(role, action.value) is allowed


def test_permissions_for_lists_the_allowed_actions() -> None:
    assert permissions_for(STAFF) == frozenset(
        {VendorAction.ORDERS, VendorAction.QUOTES, VendorAction.PRODUCTS, VendorAction.EVENTS}
    )
    assert permissions_for(OWNER) == frozenset(VendorAction)


def test_staff_permissions_are_a_subset_of_owner_permissions() -> None:
    assert permissions_for(STAFF) < permissions_for(OWNER)


@pytest.mark.parametrize("role", ["", "admin", "Owner", "manager"])
def test_unknown_role_is_refused(role: str) -> None:
    with pytest.raises(UnknownRole):
        is_allowed(role, VendorAction.ORDERS)
    with pytest.raises(UnknownRole):
        check_role(role)
    with pytest.raises(UnknownRole):
        permissions_for(role)


@pytest.mark.parametrize("action", ["", "delete_vendor", "ORDERS", "payout"])
def test_unknown_action_is_refused(action: str) -> None:
    with pytest.raises(UnknownAction):
        is_allowed(OWNER, action)


# --- At least one owner per vendor


@pytest.mark.parametrize(
    "roles_after",
    [[OWNER], [OWNER, STAFF], [STAFF, OWNER, STAFF], [OWNER, OWNER]],
)
def test_memberships_with_an_owner_are_accepted(roles_after: list[str]) -> None:
    check_keeps_an_owner(roles_after)


@pytest.mark.parametrize(
    "roles_after",
    [
        [],  # the last member (an owner) removed
        [STAFF],  # the last owner demoted, or a vendor's first member added as staff
        [STAFF, STAFF],
    ],
)
def test_memberships_without_an_owner_are_refused(roles_after: list[str]) -> None:
    with pytest.raises(NoOwnerLeft):
        check_keeps_an_owner(roles_after)
