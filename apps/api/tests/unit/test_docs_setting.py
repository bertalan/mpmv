"""CORE.29 (Q-13): the API documentation setting fails closed and LIVE cannot open it."""

import importlib

from pytest_django.fixtures import Settings as SettingsWrapper

from apps.api.checks import check_docs_not_public_on_live


def test_the_base_default_is_closed() -> None:
    assert importlib.import_module("mpmv.settings.base").API_DOCS_PUBLIC is False


def test_the_check_accepts_local_and_stage_style_settings(settings: SettingsWrapper) -> None:
    settings.LIVE_DATA = False
    settings.API_DOCS_PUBLIC = True
    assert check_docs_not_public_on_live() == []


def test_the_check_accepts_live_with_the_docs_closed(settings: SettingsWrapper) -> None:
    settings.LIVE_DATA = True
    settings.API_DOCS_PUBLIC = False
    assert check_docs_not_public_on_live() == []


def test_the_check_errors_when_live_has_the_docs_public(settings: SettingsWrapper) -> None:
    settings.LIVE_DATA = True
    settings.API_DOCS_PUBLIC = True
    assert [e.id for e in check_docs_not_public_on_live()] == ["api.E005"]
