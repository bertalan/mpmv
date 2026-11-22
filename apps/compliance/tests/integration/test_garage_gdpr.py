"""FIT.5 (3/3): the account garage in the CMP.4 export and erasure."""

import datetime as dt

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.compliance import gdpr
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.fitment.models import GarageProfile
from apps.fitment.tests.factories import (
    FitmentLevelFactory,
    FitmentNodeFactory,
    GarageProfileFactory,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="system:test", source="system", ip="127.0.0.1")


@pytest.fixture(autouse=True)
def levels() -> None:
    for position, code in enumerate(["make", "model"], start=1):
        FitmentLevelFactory.create(position=position, code=code)


def _profile(user, last_used: dt.datetime, **kwargs):  # type: ignore[no-untyped-def]
    make = FitmentNodeFactory.create(name="Make One")
    node = FitmentNodeFactory.create(name="Model A", parent=make)
    return GarageProfileFactory.create(user=user, node=node, last_used_at=last_used, **kwargs)


def _ago(days: int) -> dt.datetime:
    return timezone.now() - dt.timedelta(days=days)


def test_the_export_lists_the_profiles() -> None:
    user = UserFactory.create()
    profile = _profile(user, _ago(5), name="Daily", year=2019, is_active=True)
    _profile(UserFactory.create(), _ago(5), name="Someone else")
    data = gdpr.export_customer_data(user, ctx=CTX)
    assert data["garage_profiles"] == [
        {
            "name": "Daily",
            "vehicle": ["Make One", "Model A"],
            "year": 2019,
            "is_active": True,
            "last_used_at": profile.last_used_at.isoformat(),
        }
    ]
    row = AuditLog.objects.get(action=gdpr.ACTION_EXPORT)
    assert row.after is not None
    assert row.after["garage_profiles"] == 1  # a count, never the names


def test_erasure_removes_the_profiles_and_keeps_other_users() -> None:
    user = UserFactory.create()
    other = _profile(UserFactory.create(), _ago(5))
    _profile(user, _ago(5), name="Daily")
    gdpr.anonymise_customer(user, ctx=CTX)
    assert not GarageProfile.objects.filter(user=user).exists()
    assert GarageProfile.objects.filter(pk=other.pk).exists()
    entry = AuditLog.objects.filter(
        action=gdpr.ACTION_ERASURE, target_model="fitment.garageprofile"
    )
    assert entry.count() == 1
    first = entry.first()
    assert first is not None
    assert "Daily" not in str(first.before)


def test_a_dry_run_erasure_keeps_the_profiles() -> None:
    user = UserFactory.create()
    _profile(user, _ago(5))
    gdpr.anonymise_customer(user, ctx=CTX, dry_run=True)
    assert GarageProfile.objects.filter(user=user).count() == 1
