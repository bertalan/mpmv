"""ARC.27 (owner, 2026-11-25): the business time zone comes from the configuration; when it is
not configured, the fallback is UTC, never a hard-coded local zone."""

import importlib
import sys
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured


def test_an_unset_zone_falls_back_to_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MPMV_BUSINESS_TIME_ZONE", raising=False)
    sys.modules.pop("mpmv.settings.base", None)
    base = importlib.import_module("mpmv.settings.base")
    try:
        assert base.BUSINESS_TIME_ZONE == "UTC"
    finally:
        sys.modules.pop("mpmv.settings.base", None)


def test_the_configured_zone_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MPMV_BUSINESS_TIME_ZONE", "Europe/Rome")
    sys.modules.pop("mpmv.settings.base", None)
    base = importlib.import_module("mpmv.settings.base")
    try:
        assert base.BUSINESS_TIME_ZONE == "Europe/Rome"
    finally:
        sys.modules.pop("mpmv.settings.base", None)


# --- B2: an unknown configured zone is refused cleanly ------------------------------------------


@pytest.mark.parametrize("name", ["Europe/Atlantis", "europe/rome", "", "../../etc/passwd"])
def test_an_unknown_configured_zone_fails_the_startup_check(name: str, settings: Any) -> None:
    from apps.core.checks import check_business_time_zone

    settings.BUSINESS_TIME_ZONE = name
    assert [m.id for m in check_business_time_zone(None)] == ["core.E931"]


@pytest.mark.parametrize("name", ["UTC", "Europe/Rome", "America/New_York"])
def test_a_known_zone_passes_the_startup_check(name: str, settings: Any) -> None:
    from apps.core.checks import check_business_time_zone

    settings.BUSINESS_TIME_ZONE = name
    assert check_business_time_zone(None) == []


def test_an_unknown_zone_is_never_written_as_a_default(settings: Any) -> None:
    from apps.core.models import default_business_time_zone
    from apps.core.services import configured_tz

    settings.BUSINESS_TIME_ZONE = "Europe/Atlantis"
    with pytest.raises(ImproperlyConfigured, match=r"core\.E931"):
        default_business_time_zone()
    with pytest.raises(ImproperlyConfigured, match=r"core\.E931"):
        configured_tz()


def test_the_scheduler_never_guesses_a_zone_for_an_unknown_name(settings: Any) -> None:
    from apps.core import tasks

    settings.BUSINESS_TIME_ZONE = "Europe/Atlantis"
    tasks.forget_scheduler_zone()
    try:
        with pytest.raises(ImproperlyConfigured, match=r"core\.E931"):
            tasks.scheduler_tz()
    finally:
        tasks.forget_scheduler_zone()
