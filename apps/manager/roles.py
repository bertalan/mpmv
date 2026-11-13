"""Platform staff roles and the role-permission matrix (CORE.18, spec 02 §Role-permission matrix).

The single source of truth, as data, with no ORM: the groups and permissions of every platform
role, the incompatible role pairs and the matrix of platform sections by role. The menu, the
section checks, the data migration that seeds the groups, the drift system check, the generated
`docs/dev/platform-roles.md` and the parametric test of every cell all read it.

Section checks ask whether the actor is a member of a role the cell allows (`allows`); they never
call `has_perm`, which is true for every active superuser. The technical superuser is a
break-glass account: it appears only in the two break-glass rows and holds no role by itself.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

PLATFORM_OWNER_PERMISSION: Final = "core.platform_owner"
VIEW_ALL_VENDORS_PERMISSION: Final = "vendors.view_all_vendors"  # Q-34
REVERT_PROFILE_PERMISSION: Final = "vendors.revert_storefront_profile"  # CORE.9, Q-16
# Wagtail's access to the editors' admin: the roles that edit or read the help texts (ARC.32,
# lane-a on #86) hold it, so the Help item and the "Help texts" snippet are reachable.
EDITORS_ADMIN_PERMISSION: Final = "wagtailadmin.access_admin"
# Opens a passkey window (CORE.25). Owner decision 2026-11-12 (D-32): owner, support and finance.
ELEVATE_PERMISSION: Final = "core.elevate"
REVIEW_NOTICES_PERMISSION: Final = "compliance.review_notices"  # CMP.5, Catalog and moderation


class Access(StrEnum):
    """What a role may do in a section. A missing cell means no access (404)."""

    READ = "R"
    EDIT = "E"
    ACTION = "A"  # audited support actions
    READ_ACTION = "R+A"
    IMPERSONATE = "I"  # within the impersonation policy (CORE.10)

    @property
    def reads(self) -> bool:
        return True

    @property
    def writes(self) -> bool:
        return self in (Access.EDIT, Access.ACTION, Access.READ_ACTION)


@dataclass(frozen=True)
class Role:
    key: str
    group: str  # the Django group name, unique
    label: str
    permissions: tuple[str, ...]  # "app_label.codename", exactly these and no others


CONFIG: Final = "config"
CATALOG: Final = "catalog"
FINANCE: Final = "finance"
SUPPORT: Final = "support"
OWNER: Final = "owner"
SUPERUSER: Final = "superuser"  # not a group: break-glass columns of the matrix only

ROLES: Final[tuple[Role, ...]] = (
    Role(
        CONFIG,
        "Platform: configuration and tax",
        "Configuration and tax",
        ("manager.platform_config",),
    ),
    Role(
        CATALOG,
        "Platform: catalog and moderation",
        "Catalog and moderation",
        (
            "manager.platform_catalog",
            VIEW_ALL_VENDORS_PERMISSION,
            REVIEW_NOTICES_PERMISSION,
            REVERT_PROFILE_PERMISSION,
            EDITORS_ADMIN_PERMISSION,
        ),
    ),
    Role(
        FINANCE,
        "Platform: finance and payments",
        "Finance and payments",
        ("manager.platform_finance", VIEW_ALL_VENDORS_PERMISSION, ELEVATE_PERMISSION),
    ),
    Role(
        SUPPORT,
        "Platform: support",
        "Support",
        ("manager.platform_support", VIEW_ALL_VENDORS_PERMISSION, ELEVATE_PERMISSION),
    ),
    Role(
        OWNER,
        "Platform: owner",
        "Platform owner",
        (
            PLATFORM_OWNER_PERMISSION,
            VIEW_ALL_VENDORS_PERMISSION,
            REVIEW_NOTICES_PERMISSION,
            REVERT_PROFILE_PERMISSION,
            EDITORS_ADMIN_PERMISSION,
            ELEVATE_PERMISSION,
        ),
    ),
)

ROLE_KEYS: Final = tuple(role.key for role in ROLES)
_BY_KEY: Final = {role.key: role for role in ROLES}

# Pairs one person may not hold (separation of duties) unless the audited one-person-team
# setting (BO-13) is on. Unordered.
INCOMPATIBLE_PAIRS: Final[tuple[frozenset[str], ...]] = (frozenset({FINANCE, CONFIG}),)

E, R, A, RA = Access.EDIT, Access.READ, Access.ACTION, Access.READ_ACTION

# Section key -> {role key: access}. Keys are the Manager registry's section keys.
MATRIX: Final[Mapping[str, Mapping[str, Access]]] = {
    "platform.dashboard": {CONFIG: R, CATALOG: R, FINANCE: R, SUPPORT: R, OWNER: R},
    "platform.config": {CONFIG: E, OWNER: R},
    "platform.owner_settings": {CONFIG: R, OWNER: E},
    "platform.catalog": {CATALOG: E, SUPPORT: R, OWNER: R},
    "platform.moderation": {CATALOG: E, OWNER: R},
    "platform.notices": {CATALOG: E, OWNER: R},  # CMP.5; still gated by compliance.review_notices
    "platform.profile_changes": {CATALOG: E, OWNER: E},  # CORE.9: history and revert
    "platform.vendors": {CONFIG: R, CATALOG: R, FINANCE: R, SUPPORT: R, OWNER: E},
    "platform.vendor_approval": {CATALOG: E, OWNER: E},
    "platform.vendor_closure": {OWNER: E},
    "platform.orders_customers": {FINANCE: R, SUPPORT: RA, OWNER: R},
    "platform.support_actions": {SUPPORT: A, OWNER: A},
    "platform.activation": {SUPPORT: A, OWNER: E},
    "platform.impersonation_start": {SUPPORT: A},  # config: A only if the policy enables it
    "platform.impersonation_policy": {CONFIG: R, SUPPORT: R, OWNER: R, SUPERUSER: E},
    "platform.one_person_team": {OWNER: R, SUPERUSER: E},
    "platform.finance": {FINANCE: E, OWNER: R},
    "platform.tax_reports": {FINANCE: E, OWNER: R},
    "platform.staff_roles": {OWNER: E},
}

SECTION_LABELS: Final[Mapping[str, str]] = {
    "platform.dashboard": "Platform: dashboard",
    "platform.config": "Platform: configuration and tax (ARC.24)",
    "platform.owner_settings": "Platform: owner-only settings (CMP.13, CMP.11 approvals)",
    "platform.catalog": "Platform: catalog, categories, attributes, taxonomies, imports",
    "platform.moderation": "Platform: moderation (vendor events, storefront changes, content)",
    "platform.notices": "Platform: notices of illegal content (CMP.5)",
    "platform.profile_changes": "Platform: storefront profile changes, history and revert (CORE.9)",
    "platform.vendors": "Platform: vendors (applications, plans)",
    "platform.vendor_approval": "Platform: approve or suspend a vendor (step-up, reason)",
    "platform.vendor_closure": "Platform: close a vendor definitively",
    "platform.orders_customers": "Platform: orders and customers",
    "platform.support_actions": "Platform: support actions (unlock, resend link)",
    "platform.activation": "Platform: account activation and service messages (CORE.19)",
    "platform.impersonation_start": "Platform: start an impersonation session (CORE.10)",
    "platform.impersonation_policy": "Platform: impersonation policy and full-access grants",
    "platform.one_person_team": "Platform: one-person-team setting (BO-13)",
    "platform.finance": "Platform: finance (payouts, balances, refunds, reconciliation)",
    "platform.tax_reports": "Platform: tax and DAC7 reports",
    "platform.staff_roles": "Platform: staff and roles",
}

# The only rows the technical superuser opens without holding a role.
BREAK_GLASS_SECTIONS: Final = frozenset(
    {"platform.impersonation_policy", "platform.one_person_team"}
)


def role(key: str) -> Role:
    return _BY_KEY[key]


def group_names() -> frozenset[str]:
    return frozenset(r.group for r in ROLES)


def role_for_group(name: str) -> str | None:
    return next((r.key for r in ROLES if r.group == name), None)


def access(section: str, role_keys: Iterable[str]) -> Access | None:
    """The strongest access the given roles have in `section` (None: 404). Unknown section: None."""
    cells = MATRIX.get(section, {})
    found = [cells[key] for key in role_keys if key in cells]
    if not found:
        return None
    for level in (E, RA, A, Access.IMPERSONATE, R):
        if level in found:
            return level
    return None  # pragma: no cover


def allows(section: str, role_keys: Iterable[str], *, write: bool = False) -> bool:
    level = access(section, role_keys)
    return level is not None and (level.writes or not write)


def incompatible(role_keys: Iterable[str]) -> list[tuple[str, str]]:
    """The incompatible pairs held together, sorted for stable messages."""
    held = set(role_keys)
    return sorted(tuple(sorted(pair)) for pair in INCOMPATIBLE_PAIRS if pair <= held)  # type: ignore[misc]


def render_matrix_doc() -> str:
    """`docs/dev/platform-roles.md`, generated: `tools/build_roles_doc.py` (make docs)."""
    columns = [*ROLES, Role(SUPERUSER, "", "Tech. superuser", ())]
    lines = [
        "# Platform roles and the role-permission matrix (CORE.18)",
        "",
        "<!-- Generated from apps/manager/roles.py by `make docs`; do not edit by hand. -->",
        "",
        "Every change to the permissions of a role needs a new `manager` data migration that "
        "calls `sync_role_groups`: the drift check (manager.E004) skips only while a manager "
        "migration is pending, so without one `migrate` on a deployed database fails.",
        "",
        "## Roles",
        "",
        "| Role | Group | Permissions |",
        "|---|---|---|",
    ]
    for r in ROLES:
        perms = ", ".join(f"`{p}`" for p in r.permissions)
        lines.append(f"| {r.label} | {r.group} | {perms} |")
    pairs = "; ".join(" + ".join(_BY_KEY[k].label for k in sorted(p)) for p in INCOMPATIBLE_PAIRS)
    lines += [
        "",
        f"Incompatible pairs (refused unless the one-person-team setting is on): {pairs}.",
        "",
        "## Matrix",
        "",
        "E edit, R read only, A audited support action, I impersonation within the policy,"
        " empty means no access (404).",
        "",
        "| Section | " + " | ".join(c.label for c in columns) + " |",
        "|---|" + "---|" * len(columns),
    ]
    for key, cells in MATRIX.items():
        row = " | ".join(str(cells[c.key]) if c.key in cells else "" for c in columns)
        lines.append(f"| {SECTION_LABELS[key]} | {row} |")
    return "\n".join(lines) + "\n"
