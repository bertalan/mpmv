"""The session policy of D-32 (CORE.25): the passkey window timers and the three action lists.

Pure functions, no ORM. The policy lives on the `PlatformOperator` configuration as a sparse
JSON mapping over the D-32 defaults, so a timer or a list is tuned without code (trial, review
after STAGE). Shape of the mapping (every key optional)::

    {"idle_minutes": 15, "cap_minutes": 120, "single_seconds": 120,
     "actions": {"everyday": [...], "sensitive": [...], "critical": [...]}}

A sensitive action is always sensitive: the passkey window grants no extra power, it only lets one
fresh passkey cover several sensitive actions for a limited time. Groups: 1 everyday (free),
2 sensitive (a passkey once, or an open window), 3 critical (a fresh passkey each time, even with
the window open). An action named in no list fails closed to sensitive. The floor actions (payout
and bank details, the owner-role grant, MFA disable, `bootstrap_platform_owner`) are critical
whatever the lists say (CORE.26 reuses this floor for the vendor layer).

Policies stored before the wording change use the list keys `restricted`, `operational` and
`passkey`; `parse_policy` reads them as `everyday`, `sensitive` and `critical`.
"""

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
    }
)
DEFAULT_CRITICAL_ACTIONS: Final = frozenset(
    {"payout_details", "owner_role_grant", "mfa_disable", "bootstrap_platform_owner"}
)
FLOOR_CRITICAL_ACTIONS: Final = frozenset(
    {"payout_details", "owner_role_grant", "mfa_disable", "bootstrap_platform_owner"}
)

_LISTS = ("everyday", "sensitive", "critical")
# List keys of policies stored before the passkey window wording (read, never written).
_LEGACY_LIST_KEYS: Final = {
    "restricted": "everyday",
    "operational": "sensitive",
    "passkey": "critical",
}
_KEYS = frozenset({"idle_minutes", "cap_minutes", "single_seconds", "actions"})


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
