"""CORE.20: the refused-key limit per client IP, pure part (settings, seconds left, check)."""

import datetime as dt
import importlib.util
import secrets
from types import ModuleType

import pytest
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.api.checks import check_refused_key_settings
from apps.api.engine import InvalidRateLimit, Rate, refused_key_policy, seconds_left
from mpmv.settings import base

NOON = dt.datetime(2026, 11, 7, 12, 0, tzinfo=dt.UTC)


def test_the_policy_reads_a_rate_and_a_block() -> None:
    policy = refused_key_policy("30/minute", 300)
    assert policy.rate == Rate(limit=30, period=60)
    assert policy.block == 300


@pytest.mark.parametrize("block", [0, -1, 1.5, "300", True, None])
def test_the_block_must_be_a_positive_whole_number_of_seconds(block: object) -> None:
    with pytest.raises(InvalidRateLimit):
        refused_key_policy("30/minute", block)


def test_an_unreadable_rate_is_refused() -> None:
    with pytest.raises(InvalidRateLimit):
        refused_key_policy("lots", 300)


def test_seconds_left_rounds_up_and_stops_at_zero() -> None:
    assert seconds_left(NOON + dt.timedelta(seconds=10.2), NOON) == 11
    assert seconds_left(NOON, NOON) == 0
    assert seconds_left(NOON, NOON + dt.timedelta(seconds=5)) == 0


def test_the_system_check_accepts_the_shipped_settings() -> None:
    assert check_refused_key_settings() == []


@pytest.mark.parametrize(("rate", "block"), [("lots", 300), ("30/minute", 0), ("0/minute", 300)])
def test_the_system_check_reports_invalid_settings(
    settings: SettingsWrapper, rate: str, block: int
) -> None:
    settings.API_REFUSED_KEY_RATE_LIMIT = rate
    settings.API_REFUSED_KEY_BLOCK_SECONDS = block
    errors = check_refused_key_settings()
    assert [e.id for e in errors] == ["api.E004"]


# --- Defaults and environment, as a fresh process would load `mpmv/settings/base.py`


def _load_base_settings() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mpmv.settings._core20_probe", base.__file__)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("DJANGO_SECRET_KEY", secrets.token_urlsafe(32))
    monkeypatch.setenv("DATABASE_URL", "postgres://localhost:55432/probe")
    monkeypatch.delenv("MPMV_API_REFUSED_KEY_RATE_LIMIT", raising=False)
    monkeypatch.delenv("MPMV_API_REFUSED_KEY_BLOCK_SECONDS", raising=False)
    return monkeypatch


def test_without_configuration_30_refusals_a_minute_block_for_5_minutes(
    environment: pytest.MonkeyPatch,
) -> None:
    loaded = _load_base_settings()
    policy = refused_key_policy(
        loaded.API_REFUSED_KEY_RATE_LIMIT, loaded.API_REFUSED_KEY_BLOCK_SECONDS
    )
    assert (policy.rate, policy.block) == (Rate(limit=30, period=60), 300)


def test_the_environment_changes_both_numbers(environment: pytest.MonkeyPatch) -> None:
    environment.setenv("MPMV_API_REFUSED_KEY_RATE_LIMIT", "10/minute")
    environment.setenv("MPMV_API_REFUSED_KEY_BLOCK_SECONDS", "900")
    loaded = _load_base_settings()
    assert loaded.API_REFUSED_KEY_RATE_LIMIT == "10/minute"
    assert loaded.API_REFUSED_KEY_BLOCK_SECONDS == 900
