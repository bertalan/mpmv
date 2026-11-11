"""CORE.25 (D-32): the friction report counts prompts and elevations per user per day."""

import io

import pytest
from django.core.management import call_command

from apps.accounts import levels
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext, record
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def _ctx(user_id: int, label: str) -> AuditContext:
    return AuditContext(actor_label=label, source=AuditLog.Source.ADMIN, actor_id=user_id)


def test_the_report_counts_prompts_per_user_per_day() -> None:
    first, second = UserFactory.create(staff=True), UserFactory.create(staff=True)
    for user, count in ((first, 3), (second, 1)):
        for _ in range(count):
            record(
                _ctx(user.pk, user.email),
                levels.PROMPT,
                "accounts.user",
                "k",
                None,
                {"action": "export", "group": 2},
            )
    record(
        _ctx(first.pk, first.email),
        levels.SESSION_STARTED,
        "accounts.user",
        "k",
        None,
        {"kind": "session"},
    )
    out = io.StringIO()
    call_command("session_friction_report", stdout=out)
    lines = out.getvalue().splitlines()
    row_first = next(line for line in lines if first.email in line).split()
    row_second = next(line for line in lines if second.email in line).split()
    # columns: day, user, prompts, elevations
    assert row_first[-2:] == ["3", "1"]
    assert row_second[-2:] == ["1", "0"]
