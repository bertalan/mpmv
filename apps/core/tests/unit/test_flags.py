"""ARC.8: feature flags and kill switches, the pure part (registry, resolution, cache)."""

import pytest
from django.core import checks
from django.test import override_settings

from apps.core import flags
from apps.core.checks import check_feature_flag_settings
from apps.core.tests.flags import registered

FEATURE = flags.FlagSpec("test_feature", "A feature behind a flag.", default=False)
KILL = flags.FlagSpec("test_kill", "A kill switch.", default=True, kill_switch=True)


# --- Registry


def test_checkout_import_and_payments_are_kill_switches_that_start_enabled() -> None:
    for name in (flags.CHECKOUT, flags.IMPORT, flags.PAYMENTS):
        spec = flags.spec_of(name)
        assert spec.kill_switch
        assert spec.default is True
        assert spec.description


def test_an_unknown_flag_name_is_an_error() -> None:
    with pytest.raises(flags.UnknownFlag, match="no_such_flag"):
        flags.spec_of("no_such_flag")


def test_a_flag_name_cannot_be_registered_twice() -> None:
    with registered(FEATURE), pytest.raises(ValueError, match="test_feature"):
        flags.register(FEATURE)


def test_flag_names_are_lowercase_identifiers() -> None:
    for bad in ("Checkout", "with space", "", "9lives", "x" * 65):
        with pytest.raises(ValueError, match="name"):
            flags.FlagSpec(bad, "Bad name.")


def test_a_registered_test_flag_is_gone_afterwards() -> None:
    with registered(FEATURE):
        assert flags.spec_of(FEATURE.name) is FEATURE
        assert FEATURE in flags.registered_flags()
    with pytest.raises(flags.UnknownFlag):
        flags.spec_of(FEATURE.name)


# --- Resolution: code default < environment default < staff switch < vendor override


@pytest.mark.parametrize(
    ("environment", "state", "expected"),
    [
        (None, flags.DEFAULT, False),  # the code default
        (True, flags.DEFAULT, True),  # the environment's default
        (True, flags.OFF, False),  # staff switched it off
        (False, flags.ON, True),  # staff switched it on
        (None, flags.ON, True),
    ],
)
def test_the_platform_value(environment: bool | None, state: str, expected: bool) -> None:
    assert flags.resolve(FEATURE, environment=environment, state=state, vendor=None) is expected


@pytest.mark.parametrize(("state", "vendor"), [(flags.OFF, True), (flags.ON, False)])
def test_a_vendor_override_wins_over_the_platform_value_of_a_feature(
    state: str, vendor: bool
) -> None:
    assert flags.resolve(FEATURE, environment=None, state=state, vendor=vendor) is vendor


def test_a_vendor_override_cannot_revive_a_killed_switch() -> None:
    assert flags.resolve(KILL, environment=None, state=flags.OFF, vendor=True) is False
    assert flags.resolve(KILL, environment=False, state=flags.DEFAULT, vendor=True) is False


def test_a_vendor_override_can_kill_a_switch_for_that_vendor_only() -> None:
    assert flags.resolve(KILL, environment=None, state=flags.DEFAULT, vendor=False) is False
    assert flags.resolve(KILL, environment=None, state=flags.DEFAULT, vendor=None) is True


def test_an_unknown_state_is_an_error() -> None:
    with pytest.raises(ValueError, match="state"):
        flags.resolve(FEATURE, environment=None, state="maybe", vendor=None)


def test_the_snapshot_applies_the_rules_per_vendor() -> None:
    snapshot = flags.FlagSnapshot(
        states={KILL.name: flags.DEFAULT},
        vendor_overrides={(KILL.name, 1): False},
    )
    assert snapshot.enabled(KILL, environment=None, vendor_id=None) is True
    assert snapshot.enabled(KILL, environment=None, vendor_id=1) is False
    assert snapshot.enabled(KILL, environment=None, vendor_id=2) is True
    # No row at all: the environment, then the code default.
    assert snapshot.enabled(FEATURE, environment=None, vendor_id=1) is False
    assert snapshot.enabled(FEATURE, environment=True, vendor_id=1) is True


# --- Cache: a kill switch takes effect within the TTL in every process


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_cache_reloads_once_the_ttl_has_passed() -> None:
    clock = FakeClock()
    cache: flags.TtlCache[int] = flags.TtlCache(clock=clock)
    loads: list[int] = []

    def load() -> int:
        loads.append(1)
        return len(loads)

    assert cache.get(load, ttl=5) == 1
    clock.now += 4.9
    assert cache.get(load, ttl=5) == 1
    clock.now += 0.1
    assert cache.get(load, ttl=5) == 2


def test_a_zero_ttl_never_caches() -> None:
    cache: flags.TtlCache[int] = flags.TtlCache(clock=FakeClock())
    values = iter(range(3))
    assert [cache.get(lambda: next(values), ttl=0) for _ in range(3)] == [0, 1, 2]


def test_clearing_the_cache_forces_a_reload() -> None:
    cache: flags.TtlCache[str] = flags.TtlCache(clock=FakeClock())
    assert cache.get(lambda: "old", ttl=60) == "old"
    assert cache.get(lambda: "new", ttl=60) == "old"
    cache.clear()
    assert cache.get(lambda: "new", ttl=60) == "new"


# --- System check: environment defaults name real flags


def test_environment_defaults_of_registered_flags_pass_the_check() -> None:
    with override_settings(FEATURE_FLAGS={flags.CHECKOUT: False}):
        assert check_feature_flag_settings(None) == []


def test_an_environment_default_for_an_unknown_flag_fails_the_check() -> None:
    with override_settings(FEATURE_FLAGS={"chekout": False}):
        messages = check_feature_flag_settings(None)
    assert [m.id for m in messages] == ["core.E401"]
    assert isinstance(messages[0], checks.Error)
    assert "chekout" in messages[0].msg
