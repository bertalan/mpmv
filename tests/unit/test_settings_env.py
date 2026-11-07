"""Environment helpers used by the settings modules."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from mpmv.settings.env import env_bool, env_list, env_str


def test_env_str_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MPMV_TEST_VALUE", "abc")
    assert env_str("MPMV_TEST_VALUE") == "abc"


def test_env_str_uses_default_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MPMV_TEST_VALUE", raising=False)
    assert env_str("MPMV_TEST_VALUE", "fallback") == "fallback"


def test_env_str_without_default_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MPMV_TEST_VALUE", raising=False)
    with pytest.raises(ImproperlyConfigured, match="MPMV_TEST_VALUE"):
        env_str("MPMV_TEST_VALUE")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), ("true", True), ("YES", True), (" on ", True), ("0", False), ("off", False)],
)
def test_env_bool_parses_common_spellings(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("MPMV_TEST_FLAG", raw)
    assert env_bool("MPMV_TEST_FLAG") is expected


def test_env_bool_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MPMV_TEST_FLAG", "maybe")
    with pytest.raises(ImproperlyConfigured):
        env_bool("MPMV_TEST_FLAG")


def test_env_bool_default_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MPMV_TEST_FLAG", raising=False)
    assert env_bool("MPMV_TEST_FLAG", default=True) is True


def test_env_list_splits_and_trims(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MPMV_TEST_LIST", " a, b ,,c ")
    assert env_list("MPMV_TEST_LIST") == ["a", "b", "c"]
