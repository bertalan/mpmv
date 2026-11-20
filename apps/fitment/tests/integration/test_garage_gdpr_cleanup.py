"""FIT.5 (3/3): account garage in the CMP.4 export and erasure, and the 24-month cleanup."""

import datetime as dt

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.fitment import garage_cleanup
from apps.fitment.models import GarageProfile
from apps.fitment.tests.factories import (
    FitmentLevelFactory,
    FitmentNodeFactory,
    GarageProfileFactory,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="system:test", source="system", ip="127.0.0.1")
NOTICE_TEMPLATE = "fitment.garage_inactivity_notice"


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


def test_the_notice_goes_out_thirty_days_before_the_24_month_limit() -> None:
    user = UserFactory.create(preferred_language="it")
    recent = _profile(user, _ago(100))
    due = _profile(user, _ago(700))
    changes = garage_cleanup.run_inactivity_cleanup(ctx=CTX)
    assert len(changes.changes) == 1
    mails = OutboxEmail.objects.filter(template_key=NOTICE_TEMPLATE)
    assert mails.count() == 1  # one mail per customer
    assert mails.get().recipient == user.email
    assert mails.get().language == "it"
    due.refresh_from_db()
    recent.refresh_from_db()
    assert due.inactivity_notice_at is not None
    assert recent.inactivity_notice_at is None
    assert GarageProfile.objects.filter(user=user).count() == 2  # nothing deleted yet


def test_a_second_run_sends_no_second_notice() -> None:
    user = UserFactory.create()
    _profile(user, _ago(700))
    garage_cleanup.run_inactivity_cleanup(ctx=CTX)
    assert garage_cleanup.run_inactivity_cleanup(ctx=CTX).is_empty
    assert OutboxEmail.objects.filter(template_key=NOTICE_TEMPLATE).count() == 1


def test_a_profile_is_deleted_after_the_limit_and_the_notice_period() -> None:
    user = UserFactory.create()
    used = _ago(740)
    profile = _profile(user, used, inactivity_notice_at=_ago(31))
    garage_cleanup.run_inactivity_cleanup(ctx=CTX)
    assert not GarageProfile.objects.filter(pk=profile.pk).exists()
    entry = AuditLog.objects.get(action=garage_cleanup.PROFILE_EXPIRED)
    assert entry.target_model == "fitment.garageprofile"
    assert "name" not in str(entry.before)


def test_a_fresh_notice_blocks_the_deletion() -> None:
    user = UserFactory.create()
    profile = _profile(user, _ago(740), inactivity_notice_at=_ago(5))
    garage_cleanup.run_inactivity_cleanup(ctx=CTX)
    assert GarageProfile.objects.filter(pk=profile.pk).exists()


def test_using_a_profile_after_the_notice_cancels_it() -> None:
    user = UserFactory.create()
    profile = _profile(user, _ago(5), inactivity_notice_at=_ago(40))
    assert garage_cleanup.run_inactivity_cleanup(ctx=CTX).is_empty
    assert GarageProfile.objects.filter(pk=profile.pk).exists()


def test_dry_run_writes_nothing() -> None:
    user = UserFactory.create()
    due = _profile(user, _ago(700))
    gone = _profile(user, _ago(740), inactivity_notice_at=_ago(31))
    audits = AuditLog.objects.count()
    changes = garage_cleanup.run_inactivity_cleanup(ctx=CTX, dry_run=True)
    assert len(changes.changes) == 2
    assert OutboxEmail.objects.count() == 0
    assert AuditLog.objects.count() == audits
    due.refresh_from_db()
    assert due.inactivity_notice_at is None
    assert GarageProfile.objects.filter(pk=gone.pk).exists()
