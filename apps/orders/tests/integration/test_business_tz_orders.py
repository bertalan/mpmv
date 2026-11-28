"""ARC.27 until ORD.41: the order triggers compute in `TRIGGER_TIME_ZONE` (Europe/Rome), frozen
in their SQL, so orders fail closed on any other business time zone: the operator configuration
refuses it, and orders.E101 stops `migrate` (and `check --database`). The order year and the tax
dates follow the operator version valid at their own moment (`business_tz(at)`)."""

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError

from apps.core import services
from apps.core.audit import AuditContext
from apps.core.models import PlatformOperator
from apps.core.services import (
    OPERATOR_COPIED_FIELDS,
    business_tz,
    business_zone_refusals,
    configure_platform_operator,
    current_operator,
)
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.orders.checks import check_trigger_time_zone
from apps.orders.services import peek_order_number
from apps.tax.services import business_date

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
    """The zone guards are lifted, as ORD.41 will do: the triggers then follow the operator."""
    monkeypatch.setattr(services, "_ZONE_GUARDS", [])


def error_ids() -> list[str]:
    return [m.id or "" for m in check_trigger_time_zone(None, databases=["default"])]


# --- B1: fail closed until ORD.41 ---------------------------------------------------------------


def test_the_operator_cannot_leave_the_trigger_zone_before_ord_41(rome: None) -> None:
    assert business_zone_refusals("Europe/Rome") == []
    with pytest.raises(ValidationError) as caught:
        operator_in("UTC", start=dt.datetime.now(dt.UTC) - JUST_NOW)
    assert "ORD.41" in " ".join(caught.value.message_dict["business_time_zone"])
    assert business_tz() == ROME  # nothing was written


def test_orders_stop_the_release_when_the_zone_leaves_the_trigger_zone(
    rome: None, past_the_guard: None
) -> None:
    assert error_ids() == []
    operator_in("UTC", start=dt.datetime.now(dt.UTC) - JUST_NOW)
    messages = check_trigger_time_zone(None, databases=["default"])
    assert [(m.id, m.is_serious()) for m in messages] == [("orders.E101", True)]
    assert "ORD.41" in (messages[0].hint or "")


def test_a_deploy_without_the_configured_zone_stops_before_it_seeds_utc(settings: Any) -> None:
    """F2: no operator row to read (as before migration 0035) and MPMV_BUSINESS_TIME_ZONE unset,
    so UTC: `migrate` runs this database check first and refuses to seed UTC."""
    settings.BUSINESS_TIME_ZONE = "UTC"
    assert error_ids() == ["orders.E101"]
    settings.BUSINESS_TIME_ZONE = "Europe/Rome"
    assert error_ids() == []


def test_the_orders_check_leaves_an_unknown_configured_zone_to_core(settings: Any) -> None:
    settings.BUSINESS_TIME_ZONE = "Europe/Atlantis"
    with pytest.raises(ImproperlyConfigured, match=r"core\.E931"):
        business_tz()
    assert error_ids() == []  # core.E931 reports it; the check itself does not crash


def test_a_past_date_keeps_the_zone_of_the_version_valid_then(
    rome: None, past_the_guard: None
) -> None:
    assert business_date(NEW_YEAR_EVE) == dt.date(2031, 1, 1)
    operator_in("UTC", start=dt.datetime(2031, 6, 1, tzinfo=dt.UTC))
    assert business_tz(NEW_YEAR_EVE) == ROME
    assert business_date(NEW_YEAR_EVE) == dt.date(2031, 1, 1)  # still the Rome invoice day
    later = dt.datetime(2031, 12, 31, 23, 30, tzinfo=dt.UTC)
    assert business_date(later) == dt.date(2031, 12, 31)  # the UTC version's day


def test_the_order_year_turns_at_midnight_in_the_business_zone(
    rome: None, past_the_guard: None
) -> None:
    assert peek_order_number(now=NEW_YEAR_EVE).startswith("2031-")
    operator_in("UTC", start=dt.datetime(2030, 6, 1, tzinfo=dt.UTC))
    assert peek_order_number(now=NEW_YEAR_EVE).startswith("2030-")
