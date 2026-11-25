"""ARC.27 (D-31): the business time zone is part of the versioned operator configuration and
`business_tz()` is its one reader; storage stays UTC.

Until ORD.41 the order triggers compute in Europe/Rome and orders refuse any other zone
(apps/orders/tests/integration/test_business_tz_orders.py). The tests that show how the code
follows another zone write that zone past the guard (`past_the_guard`), as ORD.41 will allow."""

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.core import services
from apps.core.audit import AuditContext
from apps.core.models import PlatformOperator
from apps.core.services import (
    OPERATOR_COPIED_FIELDS,
    business_tz,
    business_tz_memo,
    configure_platform_operator,
    current_operator,
)
from apps.core.tests.factories import PlatformOperatorFactory, utc

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="test:arc27", source="system")
ROME = ZoneInfo("Europe/Rome")
NEW_YEAR_EVE = dt.datetime(2030, 12, 31, 23, 30, tzinfo=dt.UTC)  # 00:30 on 1 Jan in Rome
JUST_NOW = dt.timedelta(seconds=1)


def operator_in(zone: str, *, start: dt.datetime) -> None:
    latest = current_operator()
    new = PlatformOperator(
        **{name: getattr(latest, name) for name in OPERATOR_COPIED_FIELDS}, valid_from=start
    )
    new.business_time_zone = zone
    configure_platform_operator(new, ctx=CTX)


@pytest.fixture
def rome() -> None:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2020))


@pytest.fixture
def past_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The zone guards of the apps are lifted, as ORD.41 will do for orders."""
    monkeypatch.setattr(services, "_ZONE_GUARDS", [])


def test_a_new_operator_is_in_europe_rome(rome: None) -> None:
    assert current_operator().business_time_zone == "Europe/Rome"
    assert business_tz() == ROME


def test_changing_the_field_changes_the_helper(rome: None, past_the_guard: None) -> None:
    operator_in("UTC", start=dt.datetime.now(dt.UTC) - JUST_NOW)
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


# --- F3: a document date reads the zone of its own operator version ---------------------------


def test_a_moment_before_the_first_configuration_reads_the_current_zone(rome: None) -> None:
    assert business_tz(utc(2001)) == ROME


def test_a_past_moment_reads_the_operator_version_valid_then(
    rome: None, past_the_guard: None
) -> None:
    """F3: a document date keeps the zone it was issued in after a later change."""
    operator_in("UTC", start=dt.datetime(2031, 6, 1, tzinfo=dt.UTC))
    assert business_tz(NEW_YEAR_EVE) == ROME
    assert business_tz(dt.datetime(2031, 12, 31, tzinfo=dt.UTC)) == ZoneInfo("UTC")


def test_an_unknown_configured_zone_is_a_clear_error(settings: Any) -> None:
    settings.BUSINESS_TIME_ZONE = "Europe/Atlantis"
    with pytest.raises(ImproperlyConfigured, match=r"core\.E931"):
        business_tz()


# --- F5: one lookup per request ------------------------------------------------------------------


def test_a_request_asks_the_operator_once(
    rome: None, past_the_guard: None, django_assert_num_queries: Any
) -> None:
    with business_tz_memo():
        with django_assert_num_queries(1):
            assert business_tz() == ROME
            assert business_tz() == ROME
        operator_in("Asia/Tokyo", start=dt.datetime.now(dt.UTC) - JUST_NOW)
        assert business_tz() == ZoneInfo("Asia/Tokyo")  # a saved row is not hidden


def test_outside_a_request_nothing_is_remembered(
    rome: None, django_assert_num_queries: Any
) -> None:
    with django_assert_num_queries(2):
        business_tz()
        business_tz()


# --- Scheduler and migration -------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_the_scheduler_reads_the_operator_zone_once_from_its_own_thread(
    rome: None, past_the_guard: None
) -> None:
    """ARC.27: the schedules follow the operator, not the setting; the lookup works from
    Procrastinate's async loop (a thread of its own) and is kept until the worker restarts."""
    import asyncio

    from apps.core import tasks

    operator_in("Asia/Tokyo", start=dt.datetime.now(dt.UTC) - JUST_NOW)
    tasks.forget_scheduler_zone()
    try:

        async def from_the_loop() -> ZoneInfo:
            return tasks.scheduler_tz()

        assert asyncio.run(from_the_loop()) == ZoneInfo("Asia/Tokyo")
        operator_in("UTC", start=dt.datetime.now(dt.UTC))
        assert tasks.scheduler_tz() == ZoneInfo("Asia/Tokyo")  # kept until a restart
    finally:
        tasks.forget_scheduler_zone()


BEFORE = [("core", "0034_alter_mediaimage_author_alter_mediaimage_licence")]
AFTER = [("core", "0035_platformoperator_business_time_zone")]


@pytest.mark.django_db(transaction=True)
def test_the_migration_seeds_the_configured_zone_and_never_an_unknown_one(settings: Any) -> None:
    """F6: migration 0035 gives existing operator rows the configured zone; an unknown name
    stops it instead of being written."""
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    try:
        OldOperator = executor.loader.project_state(BEFORE).apps.get_model(
            "core", "PlatformOperator"
        )
        OldOperator.objects.create(
            legal_name="Seed",
            country="AT",
            vat_number="ATU00000001",
            tax_regime="small_business",
            commission_vat_mode="none",
            dac7_authority_country="AT",
            valid_from=utc(2020),
        )
        settings.BUSINESS_TIME_ZONE = "Europe/Atlantis"
        executor.loader.build_graph()
        with pytest.raises(ImproperlyConfigured, match=r"core\.E931"):
            executor.migrate(AFTER)

        settings.BUSINESS_TIME_ZONE = "Europe/Lisbon"
        executor.loader.build_graph()
        executor.migrate(AFTER)
        NewOperator = executor.loader.project_state(AFTER).apps.get_model(
            "core", "PlatformOperator"
        )
        assert list(NewOperator.objects.values_list("business_time_zone", flat=True)) == [
            "Europe/Lisbon"
        ]
    finally:
        settings.BUSINESS_TIME_ZONE = "Europe/Rome"
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
