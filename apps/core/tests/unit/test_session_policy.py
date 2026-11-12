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
