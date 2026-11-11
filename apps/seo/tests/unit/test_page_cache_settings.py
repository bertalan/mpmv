"""SEO.6: the page cache is off by default in LIVE and STAGE and on with MPMV_PAGE_CACHE=1."""

import importlib
import sys
from collections.abc import Iterator

import pytest

MODULES = ("mpmv.settings.live", "mpmv.settings.stage")


@pytest.fixture(autouse=True)
def restore_modules() -> Iterator[None]:
    saved = {name: sys.modules.get(name) for name in MODULES}
    yield
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def load(name: str, monkeypatch: pytest.MonkeyPatch, value: str | None) -> bool:
    if value is None:
        monkeypatch.delenv("MPMV_PAGE_CACHE", raising=False)
    else:
        monkeypatch.setenv("MPMV_PAGE_CACHE", value)
    sys.modules.pop(name, None)
    return bool(importlib.import_module(name).PAGE_CACHE_ENABLED)


@pytest.mark.parametrize("name", MODULES)
def test_off_by_default_in_live_and_stage(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert load(name, monkeypatch, None) is False


@pytest.mark.parametrize("name", MODULES)
def test_on_with_the_environment_switch(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert load(name, monkeypatch, "1") is True


@pytest.mark.parametrize("name", MODULES)
def test_off_with_an_explicit_zero(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert load(name, monkeypatch, "0") is False
