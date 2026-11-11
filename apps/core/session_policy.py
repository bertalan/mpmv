"""The session policy of D-32 (CORE.25): timers and the three action lists, as plain data.

Pure functions, no ORM. The policy lives on the `PlatformOperator` configuration as a sparse
JSON mapping over the D-32 defaults, so a timer or a list is tuned without code (trial, review
after STAGE). Shape of the mapping (every key optional)::

    {"idle_minutes": 15, "cap_minutes": 120, "single_seconds": 120,
     "actions": {"restricted": [...], "operational": [...], "passkey": [...]}}

Groups: 1 free in restricted mode, 2 operational mode required, 3 a per-action fresh passkey.
An action named in no list fails closed to group 2. The floor actions (payout and bank details,
the owner-role grant, MFA disable, `bootstrap_platform_owner`) are group 3 whatever the lists
say (CORE.26 reuses this floor for the vendor layer).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

RESTRICTED: Final = "restricted"
OPERATIONAL: Final = "operational"

GROUP_FREE: Final = 1
GROUP_OPERATIONAL: Final = 2
GROUP_PASSKEY: Final = 3

DEFAULT_IDLE_MINUTES: Final = 15
DEFAULT_CAP_MINUTES: Final = 120
DEFAULT_SINGLE_SECONDS: Final = 120
MAX_SINGLE_SECONDS: Final = 600

DEFAULT_RESTRICTED_ACTIONS: Final = frozenset(
    {"catalogue_edit", "orders_in_progress", "content_edit"}
)
DEFAULT_OPERATIONAL_ACTIONS: Final = frozenset(
    {
        "export",
        "backup",
        "role_grant",
        "api_key",
        "vat_regime",
        "tax_in_clear",
        "audit_in_clear",
        "bulk_edit",
        "impersonation",
    }
)
DEFAULT_PASSKEY_ACTIONS: Final = frozenset(
    {"payout_details", "owner_role_grant", "mfa_disable", "bootstrap_platform_owner"}
)
FLOOR_PASSKEY_ACTIONS: Final = frozenset(
    {"payout_details", "owner_role_grant", "mfa_disable", "bootstrap_platform_owner"}
)

_LISTS = ("restricted", "operational", "passkey")
_KEYS = frozenset({"idle_minutes", "cap_minutes", "single_seconds", "actions"})


@dataclass(frozen=True)
class SessionPolicy:
    idle_minutes: int
    cap_minutes: int
    single_seconds: int
    restricted: frozenset[str]
    operational: frozenset[str]
    passkey: frozenset[str]
    # An unreadable stored policy fails closed: every action is group 3 until it is fixed.
    fail_closed: bool = False

    def group_of(self, action: str) -> int:
        if self.fail_closed or action in FLOOR_PASSKEY_ACTIONS or action in self.passkey:
            return GROUP_PASSKEY
        if action in self.restricted:
            return GROUP_FREE
        return GROUP_OPERATIONAL  # listed or not: an unknown action fails closed


def _minutes(raw: Mapping[str, Any], key: str, default: int, upper: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
        raise ValueError(f"{key} must be a whole number from 1 to {upper}")
    return value


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
    actions = raw.get("actions", {})
    if not isinstance(actions, Mapping) or set(actions) - set(_LISTS):
        raise ValueError(f"actions must be a mapping with the lists {list(_LISTS)}")
    defaults = {
        "restricted": DEFAULT_RESTRICTED_ACTIONS,
        "operational": DEFAULT_OPERATIONAL_ACTIONS,
        "passkey": DEFAULT_PASSKEY_ACTIONS,
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
    if (lists["restricted"] | lists["operational"]) & FLOOR_PASSKEY_ACTIONS:
        raise ValueError("payout details, the owner-role grant and MFA disable cannot be relaxed")
    return SessionPolicy(
        idle_minutes=idle,
        cap_minutes=cap,
        single_seconds=single,
        restricted=lists["restricted"],
        operational=lists["operational"],
        passkey=lists["passkey"],
    )


DEFAULT_POLICY: Final = parse_policy({})
FAIL_CLOSED_POLICY: Final = SessionPolicy(
    idle_minutes=DEFAULT_IDLE_MINUTES,
    cap_minutes=DEFAULT_CAP_MINUTES,
    single_seconds=DEFAULT_SINGLE_SECONDS,
    restricted=frozenset(),
    operational=frozenset(),
    passkey=frozenset(),
    fail_closed=True,
)


def expiry(
    policy: SessionPolicy, *, started_at: float | None, last_activity: float | None, now: float
) -> str | None:
    """Why an operational session is over at `now`: "idle", "cap", "invalid"; None if it runs."""
    if started_at is None or last_activity is None:
        return "invalid"
    if now - started_at >= policy.cap_minutes * 60:
        return "cap"
    if now - last_activity >= policy.idle_minutes * 60:
        return "idle"
    return None
