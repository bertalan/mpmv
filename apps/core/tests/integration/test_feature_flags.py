"""ARC.8: `flag_enabled`, the platform-wide switches and their audit trail."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.core import flags, services
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, FeatureFlag
from apps.core.services import FLAG_CACHE, flag_enabled, save_flag
from apps.core.tests.factories import FeatureFlagFactory
from apps.core.tests.flags import registered

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:ops@example.com", source=AuditLog.Source.ADMIN, actor_id=7)
FEATURE = flags.FlagSpec("test_feature", "A feature behind a flag.", default=False)

type CaptureOnCommit = Callable[..., AbstractContextManager[list[Callable[[], Any]]]]


@pytest.fixture(autouse=True)
def _fresh_flag_cache() -> Iterator[None]:
    """The cache is per process: no test sees what another one cached."""
    FLAG_CACHE.clear()
    yield
    FLAG_CACHE.clear()


@pytest.fixture
def feature() -> Iterator[flags.FlagSpec]:
    with registered(FEATURE) as spec:
        yield spec


def kill_switch(name: str) -> FeatureFlag:
    return FeatureFlag.objects.get(name=name)


def test_the_kill_switches_exist_from_the_first_migrate() -> None:
    """Rows are seeded, so staff can flip one in an emergency without creating it first."""
    names = set(FeatureFlag.objects.values_list("name", flat=True))
    assert {flags.CHECKOUT, flags.IMPORT, flags.PAYMENTS} <= names
    assert {kill_switch(n).state for n in (flags.CHECKOUT, flags.IMPORT, flags.PAYMENTS)} == {
        flags.DEFAULT
    }


def test_kill_switches_are_on_until_someone_pulls_them() -> None:
    assert flag_enabled(flags.CHECKOUT) is True
    assert flag_enabled(flags.IMPORT) is True
    assert flag_enabled(flags.PAYMENTS) is True


def test_a_flag_without_a_row_follows_the_code_default(feature: flags.FlagSpec) -> None:
    assert flag_enabled(feature.name) is False


def test_the_environment_default_comes_from_settings(
    feature: flags.FlagSpec, settings: SettingsWrapper
) -> None:
    settings.FEATURE_FLAGS = {feature.name: True, flags.PAYMENTS: False}
    assert flag_enabled(feature.name) is True
    assert flag_enabled(flags.PAYMENTS) is False


def test_the_staff_switch_overrides_the_environment(
    feature: flags.FlagSpec, settings: SettingsWrapper
) -> None:
    settings.FEATURE_FLAGS = {feature.name: True}
    FeatureFlagFactory.create(name=feature.name, state=flags.OFF)
    assert flag_enabled(feature.name) is False


def test_an_unknown_name_is_an_error_not_a_silent_false() -> None:
    with pytest.raises(flags.UnknownFlag):
        flag_enabled("chekout")


def test_only_a_vendor_can_be_passed_as_the_vendor() -> None:
    """Another model's primary key would silently pick up some vendor's override."""
    with pytest.raises(TypeError, match=r"vendors\.vendor"):
        flag_enabled(flags.CHECKOUT, vendor=kill_switch(flags.CHECKOUT))


def test_pulling_a_kill_switch_is_audited_with_before_and_after() -> None:
    switch = kill_switch(flags.CHECKOUT)
    switch.state = flags.OFF
    switch.note = "Payment provider incident"
    save_flag(switch, ctx=CTX)

    assert flag_enabled(flags.CHECKOUT) is False
    entry = AuditLog.objects.get(action="core.feature_flag.updated")
    assert entry.target_model == "core.featureflag"
    assert entry.target_key == str(switch.public_id)
    assert entry.before == {"name": "checkout", "state": "default", "note": ""}
    assert entry.after == {"name": "checkout", "state": "off", "note": "Payment provider incident"}
    assert entry.actor_label == CTX.actor_label
    assert entry.vendor_key == ""


def test_creating_a_flag_row_is_audited(feature: flags.FlagSpec) -> None:
    changes = save_flag(FeatureFlag(name=feature.name, state=flags.ON), ctx=CTX)
    assert changes.summary()["create"] == 1
    assert flag_enabled(feature.name) is True
    entry = AuditLog.objects.get(action="core.feature_flag.created")
    assert entry.after == {"name": "test_feature", "state": "on", "note": ""}


def test_a_dry_run_changes_nothing() -> None:
    switch = kill_switch(flags.PAYMENTS)
    switch.state = flags.OFF
    changes = save_flag(switch, ctx=CTX, dry_run=True)
    assert changes.changes[0].fields == {"state": ("default", "off")}
    assert kill_switch(flags.PAYMENTS).state == flags.DEFAULT
    assert flag_enabled(flags.PAYMENTS) is True
    assert not AuditLog.objects.filter(action__startswith="core.feature_flag.").exists()


def test_only_registered_flags_can_be_saved() -> None:
    with pytest.raises(ValidationError, match="not a registered"):
        save_flag(FeatureFlag(name="no_such_flag"), ctx=CTX)
    assert not FeatureFlag.objects.filter(name="no_such_flag").exists()


def test_a_flag_row_left_by_removed_code_is_ignored(feature: flags.FlagSpec) -> None:
    FeatureFlagFactory.create(name="retired_feature", state=flags.ON)
    assert flag_enabled(feature.name) is False


# --- Cache: the kill switch takes effect within seconds


def test_with_a_cache_a_saved_change_is_seen_at_once_in_this_process(
    settings: SettingsWrapper, django_capture_on_commit_callbacks: CaptureOnCommit
) -> None:
    settings.FEATURE_FLAGS_CACHE_SECONDS = 60
    assert flag_enabled(flags.IMPORT) is True  # cached now
    switch = kill_switch(flags.IMPORT)
    switch.state = flags.OFF
    with django_capture_on_commit_callbacks(execute=True):
        save_flag(switch, ctx=CTX)
    assert flag_enabled(flags.IMPORT) is False


class FrozenClock:
    """A monotonic clock that moves only when the test says so."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_with_a_cache_other_processes_see_a_change_after_the_ttl(
    settings: SettingsWrapper, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A change made elsewhere (another worker) is seen once the TTL has passed, not before."""
    clock = FrozenClock()
    monkeypatch.setattr(services, "FLAG_CACHE", flags.TtlCache(clock=clock))
    settings.FEATURE_FLAGS_CACHE_SECONDS = 60
    assert flag_enabled(flags.IMPORT) is True  # cached now
    FeatureFlag.objects.filter(name=flags.IMPORT).update(state=flags.OFF)
    clock.now += 59.9
    assert flag_enabled(flags.IMPORT) is True  # still cached here
    clock.now += 0.1
    assert flag_enabled(flags.IMPORT) is False
