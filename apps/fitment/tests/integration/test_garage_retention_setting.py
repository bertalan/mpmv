"""FIT.5 follow-up: the garage inactivity retention is a scheme setting (spec 17 §17.4.5)."""

import datetime as dt

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.fitment import garage_cleanup, services
from apps.fitment.models import GarageProfile
from apps.fitment.tests.factories import (
    FitmentLevelFactory,
    FitmentNodeFactory,
    GarageProfileFactory,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="system:test", source="system", ip="127.0.0.1")


def _profile(days_ago: int) -> GarageProfile:
    for position, code in enumerate(["make", "model"], start=1):
        FitmentLevelFactory.create(position=position, code=code)
    make = FitmentNodeFactory.create(name="Make One")
    node = FitmentNodeFactory.create(name="Model A", parent=make)
    return GarageProfileFactory.create(
        user=UserFactory.create(),
        node=node,
        last_used_at=timezone.now() - dt.timedelta(days=days_ago),
    )


def test_the_defaults_are_24_months_and_30_days() -> None:
    scheme = services.current_scheme()
    assert (scheme.garage_inactivity_months, scheme.garage_notice_days) == (24, 30)


def test_the_cleanup_honours_a_shorter_retention() -> None:
    profile = _profile(days_ago=400)  # under 24 months, over 12
    assert garage_cleanup.run_inactivity_cleanup(ctx=CTX).changes == []
    services.save_scheme({"garage_inactivity_months": 12, "garage_notice_days": 10}, ctx=CTX)
    changes = garage_cleanup.run_inactivity_cleanup(ctx=CTX)
    assert len(changes.changes) == 1
    profile.refresh_from_db()
    assert profile.inactivity_notice_at is not None


def test_the_cleanup_honours_a_longer_retention() -> None:
    _profile(days_ago=700)  # in the notice window of 24 months
    services.save_scheme({"garage_inactivity_months": 36}, ctx=CTX)
    assert garage_cleanup.run_inactivity_cleanup(ctx=CTX).changes == []


def test_the_change_is_audited() -> None:
    services.save_scheme({"garage_inactivity_months": 18}, ctx=CTX)
    assert AuditLog.objects.filter(action=services.SCHEME_UPDATED).exists()


@pytest.mark.parametrize(
    "bad",
    [
        {"garage_inactivity_months": 0},
        {"garage_inactivity_months": 121},
        {"garage_notice_days": -1},
        {"garage_inactivity_months": 1, "garage_notice_days": 30},  # notice not under the period
        {"garage_inactivity_months": "24"},
        {"garage_notice_days": True},
    ],
)
def test_invalid_retention_values_are_refused(bad: dict[str, object]) -> None:
    with pytest.raises(services.FitmentError):
        services.save_scheme(bad, ctx=CTX)
    scheme = services.current_scheme()
    assert (scheme.garage_inactivity_months, scheme.garage_notice_days) == (24, 30)
