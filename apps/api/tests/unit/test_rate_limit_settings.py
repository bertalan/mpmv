"""CORE.16 done-when: the API rate limit defaults to 120 calls a minute per key (owner decision
MQ-4) and an environment setting changes it.

Each test loads `mpmv/settings/base.py` afresh with a controlled environment, as a new process
of that environment would.
"""

import importlib.util
import secrets
from types import ModuleType

import pytest

from apps.api.engine import Rate, parse_rate
from mpmv.settings import base

ENV_VAR = "MPMV_API_RATE_LIMIT"


def load_base_settings() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mpmv.settings._core16_probe", base.__file__)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def required_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DJANGO_SECRET_KEY", secrets.token_urlsafe(32))
    monkeypatch.setenv("DATABASE_URL", "postgres://localhost:55432/probe")


def test_without_configuration_a_key_gets_120_calls_a_minute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert parse_rate(load_base_settings().API_RATE_LIMIT) == Rate(limit=120, period=60)


def test_an_environment_setting_changes_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "30/minute")
    assert load_base_settings().API_RATE_LIMIT == "30/minute"


def test_an_empty_environment_setting_keeps_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "")
    assert parse_rate(load_base_settings().API_RATE_LIMIT) == Rate(limit=120, period=60)
