"""CORE.18 (part 1) done-when: the matrix as data, each role gets exactly its permissions."""

from pathlib import Path

import pytest

from apps.manager import roles

# Spelled out on purpose: a change to roles.py must change this table too.
EXPECTED = {
    "config": {"manager.platform_config"},
    "catalog": {
        "manager.platform_catalog",
        "vendors.view_all_vendors",
        "vendors.revert_storefront_profile",
        "wagtailadmin.access_admin",
    },
    "finance": {"manager.platform_finance", "vendors.view_all_vendors", "core.elevate"},
    "support": {"manager.platform_support", "vendors.view_all_vendors", "core.elevate"},
    "owner": {
        "core.platform_owner",
        "vendors.view_all_vendors",
        "vendors.revert_storefront_profile",
        "wagtailadmin.access_admin",
        "core.elevate",
    },
}


@pytest.mark.parametrize("role", roles.ROLES, ids=lambda r: r.key)
def test_each_role_gets_exactly_its_permissions(role: roles.Role) -> None:
    assert set(role.permissions) == EXPECTED[role.key]
    assert len(role.permissions) == len(set(role.permissions))


def test_only_the_owner_role_holds_platform_owner() -> None:
    holders = [r.key for r in roles.ROLES if roles.PLATFORM_OWNER_PERMISSION in r.permissions]
    assert holders == ["owner"]


def test_only_owner_support_and_finance_can_open_a_passkey_window() -> None:
    """Owner decision 2026-11-12 (D-32): these three roles hold core.elevate, no other."""
    holders = {r.key for r in roles.ROLES if roles.ELEVATE_PERMISSION in r.permissions}
    assert holders == {"owner", "support", "finance"}


def test_the_configuration_role_has_no_platform_wide_vendor_read() -> None:
    assert roles.VIEW_ALL_VENDORS_PERMISSION not in roles.role("config").permissions


def test_group_names_are_unique() -> None:
    assert len(roles.group_names()) == len(roles.ROLES)


def test_finance_and_configuration_are_incompatible_in_either_order() -> None:
    assert roles.incompatible(["finance", "config"]) == [("config", "finance")]
    assert roles.incompatible(["config", "support"]) == []


CELLS = [(s, r) for s in roles.MATRIX for r in (*roles.ROLE_KEYS, roles.SUPERUSER)]


@pytest.mark.parametrize(("section", "role"), CELLS)
def test_every_matrix_cell(section: str, role: str) -> None:
    cell = roles.MATRIX[section].get(role)
    assert roles.allows(section, [role]) is (cell is not None)
    assert roles.allows(section, [role], write=True) is bool(cell and cell.writes)


def test_a_cell_without_access_is_none_and_an_unknown_section_is_refused() -> None:
    assert roles.access("platform.finance", ["support"]) is None
    assert roles.access("platform.nope", list(roles.ROLE_KEYS)) is None


def test_finance_does_not_edit_configuration_and_support_cannot_close_a_vendor() -> None:
    assert not roles.allows("platform.config", ["finance"])
    assert not roles.allows("platform.vendor_approval", ["support"])
    assert not roles.allows("platform.vendor_closure", ["catalog"])
    assert roles.allows("platform.vendor_approval", ["catalog"], write=True)


def test_the_technical_superuser_opens_only_the_break_glass_rows() -> None:
    rows = {s for s, cells in roles.MATRIX.items() if roles.SUPERUSER in cells}
    assert rows == roles.BREAK_GLASS_SECTIONS


def test_only_the_owner_manages_staff_roles() -> None:
    assert set(roles.MATRIX["platform.staff_roles"]) == {"owner"}


def test_the_generated_doc_is_current() -> None:
    doc = Path(__file__).parents[4] / "docs" / "dev" / "platform-roles.md"
    assert doc.read_text() == roles.render_matrix_doc()
