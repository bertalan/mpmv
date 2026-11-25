"""SEO.16: a system check warns when the platform operator has no logo, because the operator
`Organization` of every page then has none (Google uses it for the logo in results)."""

from typing import Any

import pytest

from apps.core.models import PlatformOperator
from apps.core.tests.factories import MediaImageFactory, PlatformOperatorFactory, utc
from apps.seo.checks import check_operator_logo

pytestmark = pytest.mark.django_db


def ids(**kwargs: Any) -> list[str]:
    return [m.id for m in check_operator_logo(None, databases=["default"], **kwargs) if m.id]


def test_no_operator_logo_warns() -> None:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2020))
    assert ids() == ["seo.W002"]


def test_an_operator_logo_silences_the_warning() -> None:
    operator = PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2020))
    PlatformOperator.objects.filter(pk=operator.pk).update(logo=MediaImageFactory.create())
    assert ids() == []


def test_no_operator_yet_is_not_this_checks_business() -> None:
    assert ids() == []


def test_without_a_database_the_check_is_silent() -> None:
    assert [m.id for m in check_operator_logo(None, databases=None)] == []


def test_overlapping_operators_do_not_crash_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #1100: `check` and `migrate` must not crash on an overlap. The database's
    exclusion constraint already refuses one, so the lookup error is simulated."""
    from apps.core import services

    def overlap(*args: Any) -> None:
        raise services.OverlappingPlatformOperators("two rows")

    monkeypatch.setattr(services, "current_operator", overlap)
    assert ids() == []
