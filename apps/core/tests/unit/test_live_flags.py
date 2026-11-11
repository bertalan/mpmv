"""ARC.29: demo mode and forced noindex are refused where live data is held (core.E801)."""

import pytest
from django.core import checks
from django.test import override_settings

from apps.core.checks import check_live_site_flags

FLAGS = ("DEMO_MODE", "FORCE_NOINDEX")


def _ids(**overrides: bool) -> list[str]:
    with override_settings(**overrides):
        return [m.id for m in check_live_site_flags(None)]


@pytest.mark.parametrize("flag", FLAGS)
def test_live_refuses_each_flag_and_names_the_variable(flag: str) -> None:
    other = {name: False for name in FLAGS if name != flag}
    with override_settings(LIVE_DATA=True, **{flag: True}, **other):
        messages = check_live_site_flags(None)
    assert [m.id for m in messages] == ["core.E801"]
    assert isinstance(messages[0], checks.Error)
    assert f"MPMV_{flag}" in messages[0].msg


def test_live_with_both_flags_reports_both_variables() -> None:
    with override_settings(LIVE_DATA=True, DEMO_MODE=True, FORCE_NOINDEX=True):
        messages = check_live_site_flags(None)
    assert [m.id for m in messages] == ["core.E801", "core.E801"]
    text = " ".join(m.msg for m in messages)
    assert "MPMV_DEMO_MODE" in text
    assert "MPMV_FORCE_NOINDEX" in text


def test_live_with_both_flags_off_passes() -> None:
    assert _ids(LIVE_DATA=True, DEMO_MODE=False, FORCE_NOINDEX=False) == []


def test_stage_keeps_its_flags() -> None:
    # STAGE forces noindex and may run the demo mode: no live data, no error.
    assert _ids(LIVE_DATA=False, DEMO_MODE=True, FORCE_NOINDEX=True) == []


def test_registered_under_the_deploy_tag() -> None:
    deploy_checks = checks.registry.registry.get_checks(include_deployment_checks=True)
    default_checks = checks.registry.registry.get_checks(include_deployment_checks=False)
    assert check_live_site_flags in deploy_checks
    assert check_live_site_flags not in default_checks


def test_check_deploy_fails_on_live_with_a_flag() -> None:
    with override_settings(LIVE_DATA=True, DEMO_MODE=True, FORCE_NOINDEX=False):
        found = checks.registry.run_checks(include_deployment_checks=True)
    assert "core.E801" in [m.id for m in found]
