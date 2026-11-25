"""ARC.27 (owner, 2026-11-25): the business time zone comes from the configuration; when it is
not configured, the fallback is UTC, never a hard-coded local zone."""

import importlib
import sys

import pytest


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
