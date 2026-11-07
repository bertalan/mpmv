"""Environment helpers used by the settings modules."""

import stat
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.core.crypto import parse_keys
from mpmv.settings.env import (
    ensure_key_file,
    env_bool,
    env_flags,
    env_list,
    env_secret_list,
    env_str,
)


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


# --- SEC.3: field encryption keys come from the environment or from a file outside the database


def test_env_secret_list_reads_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MPMV_TEST_KEYS_FILE", raising=False)
    monkeypatch.setenv("MPMV_TEST_KEYS", "a:1, b:2")
    assert env_secret_list("MPMV_TEST_KEYS") == ["a:1", "b:2"]


def test_env_secret_list_prefers_the_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    keys.write_text("# newest first\nb:2\n\n  a:1  \n")
    monkeypatch.setenv("MPMV_TEST_KEYS", "ignored:0")
    monkeypatch.setenv("MPMV_TEST_KEYS_FILE", str(keys))
    assert env_secret_list("MPMV_TEST_KEYS") == ["b:2", "a:1"]


def test_env_secret_list_with_an_unreadable_file_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MPMV_TEST_KEYS_FILE", str(tmp_path / "missing"))
    with pytest.raises(ImproperlyConfigured, match="MPMV_TEST_KEYS_FILE"):
        env_secret_list("MPMV_TEST_KEYS")


def test_ensure_key_file_creates_a_private_key_once(tmp_path: Path) -> None:
    path = tmp_path / "config" / "field-keys"
    ensure_key_file(path, key_id="dev")
    first = path.read_text()
    assert parse_keys(env_lines(first)).primary_id == "dev"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    ensure_key_file(path, key_id="dev")
    assert path.read_text() == first  # never replaced: that would lose the local data


# --- ARC.8: per-environment feature flag defaults


def test_env_flags_reads_names_and_on_off_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MPMV_TEST_FLAGS", " checkout=off, beta_search=ON ,import=0")
    assert env_flags("MPMV_TEST_FLAGS") == {
        "checkout": False,
        "beta_search": True,
        "import": False,
    }


def test_env_flags_is_empty_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MPMV_TEST_FLAGS", raising=False)
    assert env_flags("MPMV_TEST_FLAGS") == {}


@pytest.mark.parametrize("raw", ["checkout", "checkout=", "=on", "checkout=maybe"])
def test_env_flags_rejects_entries_without_a_clear_value(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("MPMV_TEST_FLAGS", raw)
    with pytest.raises(ImproperlyConfigured, match="MPMV_TEST_FLAGS"):
        env_flags("MPMV_TEST_FLAGS")


def env_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line and not line.startswith("#")]
