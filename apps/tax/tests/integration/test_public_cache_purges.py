"""SEO.6 2b: a tax rate or vendor tax profile change purges the public pages."""

from collections.abc import Callable
from typing import Any

import pytest

from apps.core import public_cache
from apps.tax.tests.factories import TaxRateFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def purges(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(public_cache, "_listeners", [seen.append])
    return seen


Commit = Callable[..., Any]


def test_a_tax_rate_change_purges(
    purges: list[str], django_capture_on_commit_callbacks: Commit
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        rate = TaxRateFactory.create(open_ended=True)
    assert "tax rate" in purges
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        rate.delete()
    assert purges == ["tax rate"]
