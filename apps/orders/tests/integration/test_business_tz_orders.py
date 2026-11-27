"""ARC.27 and ORD.41: the order year, the tax retention date and the scrub gates follow the
business time zone of the operator version valid at their own moment, in Python
(`business_tz(at)`) and in the order triggers (`business_time_zone_at(ts)`) alike, so the two
never put an order's retention end in different years. Any zone may be configured."""

import datetime as dt
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.core.audit import AuditContext
from apps.core.models import PlatformOperator
from apps.core.services import (
    OPERATOR_COPIED_FIELDS,
    business_tz,
    configure_platform_operator,
    current_operator,
)
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.orders import engine, services
from apps.orders.models import Order
from apps.orders.services import LineInput, ScrubNotDue, VendorOrderInput, peek_order_number
from apps.tax.services import business_date
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="test:arc27", source="system")
ROME = ZoneInfo("Europe/Rome")
NEW_YEAR_EVE = dt.datetime(2030, 12, 31, 23, 30, tzinfo=dt.UTC)  # 00:30 on 1 Jan in Rome
JUST_NOW = dt.timedelta(seconds=1)
COMMISSION = {
    "rule_id": "default",
    "version": 1,
    "percent": "10.00",
    "base": "goods",
    "fee_percent": "0",
    "fee_fixed": "0",
    "fee_base": "gross",
}


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


def legacy_order(placed_at: dt.datetime, number: int) -> Order:
    """A legacy order keeps its own date, so the trigger computes the retention from it."""
    sub = VendorOrderInput(
        vendor=VendorFactory.create(),
        lines=[
            LineInput(
                product=None,
                sku="S1",
                title="Cup",
                unit_price_net=Decimal("10.00"),
                quantity=1,
                tax_rate=Decimal("22"),
                tax_reason="domestic",
            )
        ],
        commission_snapshot=COMMISSION,
    )
    order, _ = services.create_order(
        email="buyer@example.com",
        language="it",
        billing_address={"line1": "1 Via Test"},
        billing_country="IT",
        shipping_address={"line1": "2 Via Test"},
        shipping_country="IT",
        vendor_orders=[sub],
        ctx=CTX,
        state="closed",
        legacy_wp_order_id=number,
        placed_at=placed_at,
    )
    assert order is not None
    return order


def python_until(order: Order) -> dt.date:
    return engine.tax_retention_until(order.created_at, business_tz(order.created_at))


def raw_anonymise(order: Order) -> None:
    """The trigger's own gate, without the Python check of the scrub service."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('mpmv.order_scrub', 'anonymise', true)")
        cursor.execute(
            "UPDATE orders_order SET privacy_status = 'anonymized' WHERE id = %s", [order.pk]
        )


# --- ORD.41: any zone may be configured ---------------------------------------------------------


def test_the_operator_may_leave_europe_rome(rome: None) -> None:
    operator_in("UTC", start=dt.datetime.now(dt.UTC) - JUST_NOW)
    assert business_tz() == ZoneInfo("UTC")


def test_the_current_order_guard_names_no_zone() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT prosrc FROM pg_proc WHERE proname = 'orders_order_guard'")
        [(source,)] = cursor.fetchall()
    assert "Europe/Rome" not in source
    assert "AT TIME ZONE '" not in source
    assert source.count("business_time_zone_at(") == 3


# --- ORD.41: Python and the trigger agree --------------------------------------------------------


def test_in_rome_a_new_year_eve_order_belongs_to_the_next_year(rome: None) -> None:
    order = legacy_order(NEW_YEAR_EVE, 4101)
    assert order.tax_retention_until == python_until(order) == dt.date(2041, 12, 31)


def test_in_utc_a_new_year_eve_order_keeps_the_utc_year() -> None:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2020), business_time_zone="UTC")
    order = legacy_order(NEW_YEAR_EVE, 4102)
    assert order.tax_retention_until == python_until(order) == dt.date(2040, 12, 31)


def test_each_order_reads_the_version_valid_when_it_was_placed(rome: None) -> None:
    operator_in("UTC", start=utc(2031, 6))
    in_rome = legacy_order(NEW_YEAR_EVE, 4103)
    in_utc = legacy_order(dt.datetime(2031, 12, 31, 23, 30, tzinfo=dt.UTC), 4104)
    assert in_rome.tax_retention_until == python_until(in_rome) == dt.date(2041, 12, 31)
    assert in_utc.tax_retention_until == python_until(in_utc) == dt.date(2041, 12, 31)


def test_in_utc_the_trigger_refuses_a_scrub_before_the_utc_retention_end() -> None:
    """Relative to this year Y: an order of 31 Dec Y-10, 23:30 UTC, is kept until 31 Dec Y in
    UTC, so it cannot be anonymised yet; one of 31 Dec Y-11 is past its end in UTC, although in
    Rome it would belong to Y-10 and still be kept."""
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2000), business_time_zone="UTC")
    year = timezone.now().year
    kept = legacy_order(dt.datetime(year - 10, 12, 31, 23, 30, tzinfo=dt.UTC), 4105)
    assert kept.tax_retention_until == python_until(kept) == dt.date(year, 12, 31)
    with pytest.raises(IntegrityError, match="due after the retention date"):
        raw_anonymise(kept)
    with pytest.raises(ScrubNotDue):
        services.anonymise_order(kept, ctx=CTX)

    past = legacy_order(dt.datetime(year - 11, 12, 31, 23, 30, tzinfo=dt.UTC), 4106)
    assert past.tax_retention_until == python_until(past) == dt.date(year - 1, 12, 31)
    services.anonymise_order(past, ctx=CTX)  # the trigger lets the scrub service through
    past.refresh_from_db()
    assert past.privacy_status == "anonymized"


# --- ARC.27: documents keep the zone of their date ----------------------------------------------


def test_a_past_date_keeps_the_zone_of_the_version_valid_then(rome: None) -> None:
    assert business_date(NEW_YEAR_EVE) == dt.date(2031, 1, 1)
    operator_in("UTC", start=dt.datetime(2031, 6, 1, tzinfo=dt.UTC))
    assert business_tz(NEW_YEAR_EVE) == ROME
    assert business_date(NEW_YEAR_EVE) == dt.date(2031, 1, 1)  # still the Rome invoice day
    later = dt.datetime(2031, 12, 31, 23, 30, tzinfo=dt.UTC)
    assert business_date(later) == dt.date(2031, 12, 31)  # the UTC version's day


def test_the_order_year_turns_at_midnight_in_the_business_zone(rome: None) -> None:
    assert peek_order_number(now=NEW_YEAR_EVE).startswith("2031-")
    operator_in("UTC", start=dt.datetime(2030, 6, 1, tzinfo=dt.UTC))
    assert peek_order_number(now=NEW_YEAR_EVE).startswith("2030-")


def test_the_trigger_and_python_read_the_same_fallback_without_an_operator(settings: Any) -> None:
    settings.BUSINESS_TIME_ZONE = "UTC"
    order = legacy_order(NEW_YEAR_EVE, 4107)
    assert order.tax_retention_until == python_until(order) == dt.date(2040, 12, 31)
