"""The session policy: the passkey window of D-32 (CORE.25) and the session lifetimes (CORE.24).

Pure functions, no ORM. The policy lives on the `PlatformOperator` configuration as a sparse
JSON mapping over the defaults, so a timer or a list is tuned without code (trial, review
after STAGE). Shape of the mapping (every key optional)::

    {"idle_minutes": 15, "cap_minutes": 120, "single_seconds": 120,
     "actions": {"everyday": [...], "sensitive": [...], "critical": [...]},
     "customer_days": 14, "staff_idle_minutes": 30, "staff_absolute_hours": 12}

`idle_minutes` and `cap_minutes` time the passkey window. The last three are the role-based
session lifetimes (CORE.24 part 2, spec 02 §Session lifetimes and control): a customer's session
is rolling (it ends `customer_days` after its last use); a staff session (staff, platform staff,
vendor members, editors) ends `staff_idle_minutes` after its last use and `staff_absolute_hours`
after its sign-in, whatever its activity. This module is the one place these defaults live.

A sensitive action is always sensitive: the passkey window grants no extra power, it only lets one
fresh passkey cover several sensitive actions for a limited time. Groups: 1 everyday (free),
2 sensitive (a passkey once, or an open window), 3 critical (a fresh passkey each time, even with
the window open). An action named in no list fails closed to sensitive. The floor actions (payout
and bank details, the owner-role grant, MFA disable, `bootstrap_platform_owner`, and ending a
user's sessions or every session, CORE.24) are critical
whatever the lists say (CORE.26 reuses this floor for the vendor layer).

Policies stored before the wording change use the list keys `restricted`, `operational` and
`passkey`; `parse_policy` reads them as `everyday`, `sensitive` and `critical`.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

NO_WINDOW: Final = "no_window"  # the normal state has no name in the UI
PASSKEY_WINDOW: Final = "passkey_window"

GROUP_EVERYDAY: Final = 1
GROUP_SENSITIVE: Final = 2
GROUP_CRITICAL: Final = 3

DEFAULT_IDLE_MINUTES: Final = 15
DEFAULT_CAP_MINUTES: Final = 120
DEFAULT_SINGLE_SECONDS: Final = 120
MAX_SINGLE_SECONDS: Final = 600

# CORE.24 part 2: the session lifetimes (owner note 2026-11-10).
DEFAULT_CUSTOMER_DAYS: Final = 14
DEFAULT_STAFF_IDLE_MINUTES: Final = 30
DEFAULT_STAFF_ABSOLUTE_HOURS: Final = 12
MAX_CUSTOMER_DAYS: Final = 365
MAX_STAFF_ABSOLUTE_HOURS: Final = 72
CUSTOMER_LIFETIME: Final = "customer"
STAFF_LIFETIME: Final = "staff"
# A stamp later than "now" by more than this is not trusted (clock skew between workers).
STAMP_SKEW_SECONDS: Final = 60

DEFAULT_EVERYDAY_ACTIONS: Final = frozenset(
    {"catalogue_edit", "orders_in_progress", "content_edit"}
)
DEFAULT_SENSITIVE_ACTIONS: Final = frozenset(
    {
        "export",
        "backup",
        "role_grant",
        "api_key",
        "vat_regime",
        "commission_change",
        "no_tin_country",
        "tax_in_clear",
        "audit_in_clear",
        "bulk_edit",
        "impersonation",
        "termination_notice",  # CMP.5 part 4: give or withdraw a vendor's notice of termination
        "operator_config",
        "profile_revert",  # CORE.9 3/3: revert an owner's storefront text or logo change
        "notice_decision",  # CMP.5: review, decide, restrict, tell the authorities
        "sales_terms_publish",  # VEN.35: a seller publishes a new version of its sales terms
    }
)
DEFAULT_CRITICAL_ACTIONS: Final = frozenset(
    {
        "payout_details",
        "owner_role_grant",
        "mfa_disable",
        "bootstrap_platform_owner",
        "sessions_end_user",
        "sessions_end_all",
        # ORD.20 (spec 07, Roles and the passkey): an amendment's address change, an amendment
        # refund by the shop (the PAY.7 grant) and a staff refund or package cancellation.
        "order_amend_address",
        "vendor_refund",
        "order_amend_cancel",
    }
)
FLOOR_CRITICAL_ACTIONS: Final = frozenset(
    {
        "payout_details",
        "owner_role_grant",
        "mfa_disable",
        "bootstrap_platform_owner",
        "sessions_end_user",
        "sessions_end_all",
        # ORD.20 (spec 07, Roles and the passkey): an amendment's address change, an amendment
        # refund by the shop (the PAY.7 grant) and a staff refund or package cancellation.
        "order_amend_address",
        "vendor_refund",
        "order_amend_cancel",
    }
)

_LISTS = ("everyday", "sensitive", "critical")
# List keys of policies stored before the passkey window wording (read, never written).
_LEGACY_LIST_KEYS: Final = {
    "restricted": "everyday",
    "operational": "sensitive",
    "passkey": "critical",
}
_KEYS = frozenset(
    {
        "idle_minutes",
        "cap_minutes",
        "single_seconds",
        "actions",
        "customer_days",
        "staff_idle_minutes",
        "staff_absolute_hours",
    }
)


@dataclass(frozen=True)
class SessionPolicy:
    idle_minutes: int
    cap_minutes: int
    single_seconds: int
    everyday: frozenset[str]
    sensitive: frozenset[str]
    critical: frozenset[str]
    # An unreadable stored policy fails closed: every action is critical until it is fixed.
    fail_closed: bool = False
    # CORE.24 part 2: the session lifetimes (an unreadable policy keeps these defaults).
    customer_days: int = DEFAULT_CUSTOMER_DAYS
    staff_idle_minutes: int = DEFAULT_STAFF_IDLE_MINUTES
    staff_absolute_hours: int = DEFAULT_STAFF_ABSOLUTE_HOURS

    def group_of(self, action: str) -> int:
        if self.fail_closed or action in FLOOR_CRITICAL_ACTIONS or action in self.critical:
            return GROUP_CRITICAL
        if action in self.everyday:
            return GROUP_EVERYDAY
        return GROUP_SENSITIVE  # listed or not: an unknown action fails closed


def _minutes(raw: Mapping[str, Any], key: str, default: int, upper: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
        raise ValueError(f"{key} must be a whole number from 1 to {upper}")
    return value


def _current_list_keys(actions: object) -> Mapping[str, Any]:
    """`actions` with any legacy list key renamed; `ValueError` if it is not a valid mapping."""
    if not isinstance(actions, Mapping):
        raise ValueError(f"actions must be a mapping with the lists {list(_LISTS)}")
    renamed: dict[Any, Any] = {
        _LEGACY_LIST_KEYS.get(key, key): value for key, value in actions.items()
    }
    if len(renamed) != len(actions) or set(renamed) - set(_LISTS):
        raise ValueError(f"actions must be a mapping with the lists {list(_LISTS)}")
    return renamed


def parse_policy(raw: object) -> SessionPolicy:
    """The policy for a stored mapping (`{}` is the D-32 defaults); `ValueError` if invalid."""
    if not isinstance(raw, Mapping):
        raise ValueError("the session policy must be a mapping")
    unknown = set(raw) - _KEYS
    if unknown:
        raise ValueError(f"unknown session policy keys: {sorted(unknown)}")
    idle = _minutes(raw, "idle_minutes", DEFAULT_IDLE_MINUTES, 24 * 60)
    cap = _minutes(raw, "cap_minutes", DEFAULT_CAP_MINUTES, 24 * 60)
    single = _minutes(raw, "single_seconds", DEFAULT_SINGLE_SECONDS, MAX_SINGLE_SECONDS)
    if idle > cap:
        raise ValueError("idle_minutes cannot exceed cap_minutes")
    customer_days = _minutes(raw, "customer_days", DEFAULT_CUSTOMER_DAYS, MAX_CUSTOMER_DAYS)
    staff_idle = _minutes(raw, "staff_idle_minutes", DEFAULT_STAFF_IDLE_MINUTES, 24 * 60)
    staff_absolute = _minutes(
        raw, "staff_absolute_hours", DEFAULT_STAFF_ABSOLUTE_HOURS, MAX_STAFF_ABSOLUTE_HOURS
    )
    if staff_idle > staff_absolute * 60:
        raise ValueError("staff_idle_minutes cannot exceed staff_absolute_hours")
    actions = _current_list_keys(raw.get("actions", {}))
    defaults = {
        "everyday": DEFAULT_EVERYDAY_ACTIONS,
        "sensitive": DEFAULT_SENSITIVE_ACTIONS,
        "critical": DEFAULT_CRITICAL_ACTIONS,
    }
    lists: dict[str, frozenset[str]] = {}
    for name in _LISTS:
        value = actions.get(name)
        if value is None:
            lists[name] = defaults[name]
        elif isinstance(value, list | tuple) and all(isinstance(a, str) and a for a in value):
            lists[name] = frozenset(value)
        else:
            raise ValueError(f"actions.{name} must be a list of action names")
    if "actions" in raw:
        # A list that is overridden replaces its default, so the others lose what moved.
        for name in _LISTS:
            if name in actions:
                continue
            moved = set().union(*(lists[o] for o in _LISTS if o != name and o in actions))
            lists[name] = lists[name] - moved
    seen: set[str] = set()
    for name in _LISTS:
        if seen & lists[name]:
            raise ValueError("an action can be in one list only")
        seen |= lists[name]
    if (lists["everyday"] | lists["sensitive"]) & FLOOR_CRITICAL_ACTIONS:
        raise ValueError("payout details, the owner-role grant and MFA disable cannot be relaxed")
    return SessionPolicy(
        idle_minutes=idle,
        cap_minutes=cap,
        single_seconds=single,
        everyday=lists["everyday"],
        sensitive=lists["sensitive"],
        critical=lists["critical"],
        customer_days=customer_days,
        staff_idle_minutes=staff_idle,
        staff_absolute_hours=staff_absolute,
    )


DEFAULT_POLICY: Final = parse_policy({})
FAIL_CLOSED_POLICY: Final = SessionPolicy(
    idle_minutes=DEFAULT_IDLE_MINUTES,
    cap_minutes=DEFAULT_CAP_MINUTES,
    single_seconds=DEFAULT_SINGLE_SECONDS,
    everyday=frozenset(),
    sensitive=frozenset(),
    critical=frozenset(),
    fail_closed=True,
)


def expiry(
    policy: SessionPolicy, *, started_at: float | None, last_activity: float | None, now: float
) -> str | None:
    """Why a passkey window is over at `now`: "idle", "cap", "invalid"; None if it runs."""
    if started_at is None or last_activity is None:
        return "invalid"
    if now - started_at >= policy.cap_minutes * 60:
        return "cap"
    if now - last_activity >= policy.idle_minutes * 60:
        return "idle"
    return None


# --- CORE.24 part 2: the session lifetimes


@dataclass(frozen=True)
class Lifetime:
    """How long a session lives: `idle_seconds` after its last use and, when set,
    `absolute_seconds` after its sign-in (never moved by activity)."""

    kind: str
    idle_seconds: int
    absolute_seconds: int | None


def lifetime_of(policy: SessionPolicy, *, strict: bool) -> Lifetime:
    """The staff lifetime when `strict`, else the customer's rolling one."""
    if strict:
        return Lifetime(
            kind=STAFF_LIFETIME,
            idle_seconds=policy.staff_idle_minutes * 60,
            absolute_seconds=policy.staff_absolute_hours * 3600,
        )
    return Lifetime(
        kind=CUSTOMER_LIFETIME, idle_seconds=policy.customer_days * 24 * 3600, absolute_seconds=None
    )


def lifetime_expiry(
    lifetime: Lifetime, *, signed_in_at: float | None, last_seen: float | None, now: float
) -> str | None:
    """Why a session is over at `now`: "absolute", "idle", "invalid"; None if it lives.

    Stamps that are missing, not finite (NaN, infinity), in the future (beyond a small clock
    skew) or out of order are "invalid": the session fails closed.
    """
    if signed_in_at is None or last_seen is None:
        return "invalid"
    if not (math.isfinite(signed_in_at) and math.isfinite(last_seen)):
        return "invalid"  # NaN compares false to everything: it would never expire
    if max(signed_in_at, last_seen) > now + STAMP_SKEW_SECONDS or last_seen < signed_in_at:
        return "invalid"
    if lifetime.absolute_seconds is not None and now - signed_in_at >= lifetime.absolute_seconds:
        return "absolute"
    if now - last_seen >= lifetime.idle_seconds:
        return "idle"
    return None


def lifetime_deadline(lifetime: Lifetime, *, signed_in_at: float, last_seen: float) -> float:
    """When the session ends if it is not used again: the nearer of idle and absolute."""
    deadline = last_seen + lifetime.idle_seconds
    if lifetime.absolute_seconds is not None:
        deadline = min(deadline, signed_in_at + lifetime.absolute_seconds)
    return deadline
