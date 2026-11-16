"""CORE.25 (D-32): the session policy is plain data with the D-32 defaults, pure functions only."""

import pytest

from apps.core import session_policy as sp


def test_the_d32_defaults() -> None:
    policy = sp.parse_policy({})
    assert (policy.idle_minutes, policy.cap_minutes, policy.single_seconds) == (15, 120, 120)
    wanted = {"export", "backup", "role_grant", "api_key", "vat_regime", "profile_revert"}
    assert wanted <= policy.sensitive
    assert {"payout_details", "owner_role_grant", "mfa_disable"} <= policy.critical
    assert policy.group_of("catalogue_edit") == sp.GROUP_EVERYDAY
    assert policy.group_of("export") == sp.GROUP_SENSITIVE
    assert policy.group_of("payout_details") == sp.GROUP_CRITICAL


def test_an_action_in_no_list_fails_closed_to_sensitive() -> None:
    assert sp.parse_policy({}).group_of("brand_new_action") == sp.GROUP_SENSITIVE


def test_sparse_overrides_keep_the_other_defaults() -> None:
    policy = sp.parse_policy({"idle_minutes": 5, "actions": {"everyday": ["export"]}})
    assert policy.idle_minutes == 5
    assert policy.cap_minutes == 120
    assert policy.group_of("export") == sp.GROUP_EVERYDAY
    assert policy.group_of("backup") == sp.GROUP_SENSITIVE


def test_a_policy_stored_with_the_old_list_keys_reads_the_same() -> None:
    """Stored `PlatformOperator` policies written before the passkey window wording."""
    old = sp.parse_policy(
        {"actions": {"restricted": ["export"], "operational": ["catalogue_edit"], "passkey": ["x"]}}
    )
    new = sp.parse_policy(
        {"actions": {"everyday": ["export"], "sensitive": ["catalogue_edit"], "critical": ["x"]}}
    )
    assert old == new
    assert old.group_of("export") == sp.GROUP_EVERYDAY
    assert old.group_of("catalogue_edit") == sp.GROUP_SENSITIVE
    assert old.group_of("x") == sp.GROUP_CRITICAL


@pytest.mark.parametrize(
    "raw",
    [
        {"idle_minutes": 0},
        {"idle_minutes": 200, "cap_minutes": 120},  # idle longer than the cap
        {"single_seconds": 10_000},
        {"idle_minutes": "soon"},
        {"actions": {"sensitive": ["export"], "everyday": ["export"]}},  # not disjoint
        {"actions": {"everyday": ["payout_details"]}},  # below the floor
        {"actions": {"sensitive": ["owner_role_grant"]}},  # below the floor
        {"actions": {"operational": ["owner_role_grant"]}},  # below the floor, old key
        {"actions": {"operational": ["export"], "restricted": ["export"]}},  # not disjoint, old
        {"actions": {"sensitive": ["export"], "operational": ["backup"]}},  # old and new key
        {"actions": {"unknown_list": ["x"]}},
        "not a mapping",
    ],
)
def test_a_bad_policy_is_refused(raw: object) -> None:
    with pytest.raises(ValueError, match=r"."):
        sp.parse_policy(raw)


def test_the_floor_actions_stay_critical_whatever_the_list_says() -> None:
    policy = sp.parse_policy({"actions": {"critical": ["something_else"]}})
    for action in sp.FLOOR_CRITICAL_ACTIONS:
        assert policy.group_of(action) == sp.GROUP_CRITICAL


def test_expiry_decisions() -> None:
    policy = sp.parse_policy({})
    start = 1000.0
    assert sp.expiry(policy, started_at=start, last_activity=start, now=start + 60) is None
    assert sp.expiry(policy, started_at=start, last_activity=start, now=start + 16 * 60) == "idle"
    assert (
        sp.expiry(policy, started_at=start, last_activity=start + 7199, now=start + 7201) == "cap"
    )
    assert sp.expiry(policy, started_at=None, last_activity=None, now=start) == "invalid"


# --- CORE.24 part 2: the role-based session lifetimes

DAY = 24 * 3600


def test_the_lifetime_defaults() -> None:
    policy = sp.parse_policy({})
    assert (policy.customer_days, policy.staff_idle_minutes, policy.staff_absolute_hours) == (
        14,
        30,
        12,
    )
    assert sp.lifetime_of(policy, strict=False) == sp.Lifetime(
        kind=sp.CUSTOMER_LIFETIME, idle_seconds=14 * DAY, absolute_seconds=None
    )
    assert sp.lifetime_of(policy, strict=True) == sp.Lifetime(
        kind=sp.STAFF_LIFETIME, idle_seconds=30 * 60, absolute_seconds=12 * 3600
    )


def test_the_fail_closed_policy_keeps_the_default_lifetimes() -> None:
    policy = sp.FAIL_CLOSED_POLICY
    assert sp.lifetime_of(policy, strict=True) == sp.lifetime_of(sp.DEFAULT_POLICY, strict=True)


def test_sparse_lifetime_overrides_keep_the_other_values() -> None:
    policy = sp.parse_policy({"staff_idle_minutes": 10})
    assert (policy.customer_days, policy.staff_idle_minutes, policy.staff_absolute_hours) == (
        14,
        10,
        12,
    )
    assert policy.idle_minutes == 15  # the passkey window timers are separate


@pytest.mark.parametrize(
    "raw",
    [
        {"customer_days": 0},
        {"customer_days": 366},
        {"customer_days": "14"},
        {"customer_days": True},
        {"staff_idle_minutes": 0},
        {"staff_absolute_hours": 0},
        {"staff_absolute_hours": 73},
        {"staff_absolute_hours": 1.5},
        {"staff_idle_minutes": 61, "staff_absolute_hours": 1},  # idle longer than the lifetime
    ],
)
def test_a_bad_lifetime_is_refused(raw: object) -> None:
    with pytest.raises(ValueError, match=r"."):
        sp.parse_policy(raw)


def test_a_customer_lifetime_is_rolling_with_no_absolute_limit() -> None:
    life = sp.lifetime_of(sp.DEFAULT_POLICY, strict=False)
    start = 1_000_000.0
    seen = start + 100 * DAY  # active for 100 days: still fine
    assert sp.lifetime_expiry(life, signed_in_at=start, last_seen=seen, now=seen + 13 * DAY) is None
    assert (
        sp.lifetime_expiry(life, signed_in_at=start, last_seen=seen, now=seen + 14 * DAY) == "idle"
    )


def test_a_staff_lifetime_ends_on_idle_and_on_the_absolute_limit() -> None:
    life = sp.lifetime_of(sp.DEFAULT_POLICY, strict=True)
    start = 1_000_000.0
    soon = start + 29 * 60
    assert sp.lifetime_expiry(life, signed_in_at=start, last_seen=start, now=soon) is None
    assert (
        sp.lifetime_expiry(life, signed_in_at=start, last_seen=start, now=start + 31 * 60) == "idle"
    )
    late = start + 12 * 3600
    assert sp.lifetime_expiry(life, signed_in_at=start, last_seen=late - 1, now=late) == "absolute"
    assert sp.lifetime_expiry(life, signed_in_at=start, last_seen=late - 60, now=late - 1) is None


@pytest.mark.parametrize(
    ("signed_in_at", "last_seen"),
    [
        (None, 1000.0),
        (1000.0, None),
        (1000.0 + 3600, 1000.0 + 3600),  # signed in an hour in the future
        (1000.0, 1000.0 + 3600),  # seen an hour in the future
        (1000.0, 900.0),  # seen before the sign-in
    ],
)
def test_an_unreadable_lifetime_stamp_is_invalid(
    signed_in_at: float | None, last_seen: float | None
) -> None:
    life = sp.lifetime_of(sp.DEFAULT_POLICY, strict=False)
    assert (
        sp.lifetime_expiry(life, signed_in_at=signed_in_at, last_seen=last_seen, now=1000.0)
        == "invalid"
    )


def test_the_deadline_is_the_nearer_of_idle_and_absolute() -> None:
    life = sp.lifetime_of(sp.DEFAULT_POLICY, strict=True)
    start = 1_000_000.0
    assert sp.lifetime_deadline(life, signed_in_at=start, last_seen=start) == start + 30 * 60
    late = start + 12 * 3600 - 60
    assert sp.lifetime_deadline(life, signed_in_at=start, last_seen=late) == start + 12 * 3600
    customer = sp.lifetime_of(sp.DEFAULT_POLICY, strict=False)
    assert sp.lifetime_deadline(customer, signed_in_at=start, last_seen=late) == late + 14 * DAY
