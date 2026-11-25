"""ARC.27 (D-31): the business time zone is part of the versioned operator configuration and
`business_tz()` is its one reader; storage stays UTC."""

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError

from apps.core.audit import AuditContext
from apps.core.services import business_tz, configure_platform_operator, current_operator
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.orders.services import peek_order_number

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="test:arc27", source="system")
ROME = ZoneInfo("Europe/Rome")
NEW_YEAR_EVE = dt.datetime(2030, 12, 31, 23, 30, tzinfo=dt.UTC)  # 00:30 on 1 Jan in Rome


def operator_in(zone: str, *, start: dt.datetime) -> None:
    from apps.core.models import PlatformOperator
    from apps.core.services import OPERATOR_COPIED_FIELDS

    latest = current_operator()
    new = PlatformOperator(
        **{name: getattr(latest, name) for name in OPERATOR_COPIED_FIELDS}, valid_from=start
    )
    new.business_time_zone = zone
    configure_platform_operator(new, ctx=CTX)


@pytest.fixture
def rome() -> None:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2020))


def test_a_new_operator_is_in_europe_rome(rome: None) -> None:
    assert current_operator().business_time_zone == "Europe/Rome"
    assert business_tz() == ROME


def test_changing_the_field_changes_the_helper(rome: None) -> None:
    operator_in("UTC", start=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1))
    assert business_tz() == ZoneInfo("UTC")


def test_without_an_operator_the_configured_setting_is_used(settings: Any) -> None:
    settings.BUSINESS_TIME_ZONE = "America/New_York"
    assert business_tz() == ZoneInfo("America/New_York")


def test_a_new_operator_starts_in_the_configured_zone(settings: Any) -> None:
    settings.BUSINESS_TIME_ZONE = "Europe/Lisbon"
    assert PlatformOperatorFactory.build().business_time_zone == "Europe/Lisbon"


def test_an_unknown_zone_is_refused(rome: None) -> None:
    operator = current_operator()
    operator.business_time_zone = "Europe/Atlantis"
    with pytest.raises(ValidationError) as caught:
        operator.full_clean(validate_constraints=False)
    assert "business_time_zone" in caught.value.message_dict


def test_the_order_year_turns_at_midnight_in_the_business_zone(rome: None) -> None:
    assert peek_order_number(now=NEW_YEAR_EVE).startswith("2031-")
    operator_in("UTC", start=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1))
    assert peek_order_number(now=NEW_YEAR_EVE).startswith("2030-")


@pytest.mark.django_db(transaction=True)
def test_the_scheduler_reads_the_operator_zone_once_from_its_own_thread(rome: None) -> None:
    """ARC.27: the schedules follow the operator, not the setting; the lookup works from
    Procrastinate's async loop (a thread of its own) and is kept until the worker restarts."""
    import asyncio

    from apps.core import tasks

    operator_in("Asia/Tokyo", start=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1))
    tasks.forget_scheduler_zone()
    try:

        async def from_the_loop() -> ZoneInfo:
            return tasks.scheduler_tz()

        assert asyncio.run(from_the_loop()) == ZoneInfo("Asia/Tokyo")
        operator_in("UTC", start=dt.datetime.now(dt.UTC))
        assert tasks.scheduler_tz() == ZoneInfo("Asia/Tokyo")  # kept until a restart
    finally:
        tasks.forget_scheduler_zone()


def test_orders_warn_when_the_zone_leaves_the_trigger_zone(rome: None) -> None:
    from apps.orders.checks import check_trigger_time_zone

    assert check_trigger_time_zone(None, databases=["default"]) == []
    operator_in("UTC", start=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1))
    assert [m.id for m in check_trigger_time_zone(None, databases=["default"])] == ["orders.W101"]
