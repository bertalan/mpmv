"""L-3: the privacy pepper is declared, and LIVE and STAGE refuse a missing or reused one."""

import secrets
from pathlib import Path

import pytest
from django.core import checks
from django.test import override_settings

from apps.core.checks import check_privacy_pepper

ROOT = Path(__file__).parents[4]


def _run(**overrides: object) -> list[checks.CheckMessage]:
    with override_settings(**overrides):
        return check_privacy_pepper(None)


def test_the_setting_is_declared() -> None:
    from django.conf import settings

    assert hasattr(settings, "PRIVACY_PEPPER")


@pytest.mark.parametrize("pepper", ["", "   "])
def test_strict_environments_refuse_a_missing_pepper(pepper: str) -> None:
    messages = _run(PRIVACY_PEPPER=pepper, LIVE_DATA=True)
    assert [m.id for m in messages] == ["core.E901"]
    assert isinstance(messages[0], checks.Error)
    assert "MPMV_PRIVACY_PEPPER" in messages[0].msg


def test_strict_environments_refuse_a_pepper_equal_to_the_secret_key() -> None:
    shared = secrets.token_urlsafe(32)
    messages = _run(PRIVACY_PEPPER=shared, SECRET_KEY=shared, LIVE_DATA=True)
    assert [m.id for m in messages] == ["core.E901"]


def test_strict_environments_accept_their_own_pepper() -> None:
    assert _run(PRIVACY_PEPPER=secrets.token_urlsafe(32), LIVE_DATA=True) == []


def test_development_and_tests_only_get_a_warning() -> None:
    messages = _run(PRIVACY_PEPPER="", LIVE_DATA=False, STAGE=False)
    assert [m.id for m in messages] == ["core.W901"]
    assert isinstance(messages[0], checks.Warning)


def test_development_with_a_pepper_is_silent() -> None:
    assert _run(PRIVACY_PEPPER=secrets.token_urlsafe(32), LIVE_DATA=False, STAGE=False) == []


def test_stage_is_strict_even_though_it_holds_no_live_data() -> None:
    messages = _run(PRIVACY_PEPPER="", LIVE_DATA=False, STAGE=True)
    assert [m.id for m in messages] == ["core.E901"]
    assert _run(PRIVACY_PEPPER=secrets.token_urlsafe(32), LIVE_DATA=False, STAGE=True) == []


def test_the_stage_and_live_modules_set_their_flags() -> None:
    assert "LIVE_DATA = True" in (ROOT / "mpmv/settings/live.py").read_text()
    assert "STAGE = True" in (ROOT / "mpmv/settings/stage.py").read_text()


def test_the_env_examples_declare_the_variable_without_a_value() -> None:
    for name in ("deploy/server/mpmv.env.example", ".env.example"):
        lines = (ROOT / name).read_text().splitlines()
        assert "MPMV_PRIVACY_PEPPER=" in lines, name
