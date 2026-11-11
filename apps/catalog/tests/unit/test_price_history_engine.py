"""CMP.6 (1/2): the Omnibus reference price (lowest price of the 30 days before a reduction).

Pure rules, no database. Business time zone: Europe/Rome (D-31).
"""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

from apps.catalog import engine
from apps.catalog.engine import PricePoint

ROME = ZoneInfo("Europe/Rome")


def day(n: int, hour: int = 12) -> dt.datetime:
    """Day `n` of a fixed month-long timeline starting 2027-01-01, in UTC."""
    return dt.datetime(2027, 1, 1, hour, tzinfo=dt.UTC) + dt.timedelta(days=n - 1)


def point(
    n: int,
    price: str | None,
    sale: str | None = None,
    sale_from: dt.datetime | None = None,
    sale_to: dt.datetime | None = None,
    hour: int = 12,
) -> PricePoint:
    return PricePoint(
        recorded_at=day(n, hour),
        price_net=None if price is None else Decimal(price),
        sale_price_net=None if sale is None else Decimal(sale),
        sale_from=sale_from,
        sale_to=sale_to,
    )


def ref(points: list[PricePoint], at: dt.datetime) -> engine.ReferencePrice | None:
    return engine.reference_price(points, at=at, tz=ROME)


def test_no_history_no_reference() -> None:
    assert ref([], day(40)) is None


def test_no_sale_no_reference() -> None:
    assert ref([point(1, "100.00")], day(40)) is None


def test_reference_is_lowest_price_before_the_reduction() -> None:
    points = [point(1, "100.00"), point(20, "90.00"), point(40, "100.00", "70.00")]
    found = ref(points, day(41))
    assert found is not None
    assert found.price_net == Decimal("90.00")
    assert found.current_net == Decimal("70.00")
    assert found.reduction_started_at == day(40)


def test_the_reduced_price_itself_is_excluded() -> None:
    points = [point(1, "100.00"), point(40, "100.00", "70.00")]
    found = ref(points, day(45))
    assert found is not None
    assert found.price_net == Decimal("100.00")


def test_prices_older_than_30_days_are_ignored() -> None:
    # 80.00 until day 5, 100.00 from day 5; the reduction starts on day 50.
    points = [point(1, "80.00"), point(5, "100.00"), point(50, "100.00", "70.00")]
    found = ref(points, day(51))
    assert found is not None
    assert found.price_net == Decimal("100.00")


def test_the_price_in_force_when_the_window_opens_counts() -> None:
    # 80.00 set on day 1 and never changed: still the price 30 days before day 40.
    points = [point(1, "80.00"), point(40, "80.00", "70.00")]
    found = ref(points, day(41))
    assert found is not None
    assert found.price_net == Decimal("80.00")


def test_price_increase_before_the_sale_does_not_raise_the_reference() -> None:
    points = [point(1, "100.00"), point(30, "150.00"), point(40, "150.00", "120.00")]
    found = ref(points, day(41))
    assert found is not None
    assert found.price_net == Decimal("100.00")


def test_a_reduction_that_is_not_below_the_lowest_prior_price_shows_nothing() -> None:
    points = [point(1, "60.00"), point(20, "100.00"), point(40, "100.00", "70.00")]
    assert ref(points, day(41)) is None


def test_several_changes_in_one_day_all_count() -> None:
    points = [
        point(1, "100.00"),
        point(30, "80.00", hour=8),
        point(30, "100.00", hour=9),
        point(40, "100.00", "90.00"),
    ]
    found = ref(points, day(41))
    assert found is not None
    assert found.price_net == Decimal("80.00")


def test_a_price_replaced_at_the_same_instant_never_applied() -> None:
    points = [
        point(1, "100.00"),
        point(30, "10.00"),
        point(30, "100.00"),
        point(40, "100.00", "90.00"),
    ]
    found = ref(points, day(41))
    assert found is not None
    assert found.price_net == Decimal("100.00")


def test_scheduled_sale_starts_at_sale_from_not_when_it_was_recorded() -> None:
    points = [point(1, "100.00"), point(2, "100.00", "70.00", sale_from=day(40), sale_to=day(50))]
    assert ref(points, day(10)) is None
    found = ref(points, day(41))
    assert found is not None
    assert found.reduction_started_at == day(40)
    assert found.price_net == Decimal("100.00")
    assert ref(points, day(55)) is None  # the sale has ended


def test_successive_reductions_keep_the_price_before_the_first() -> None:
    points = [point(1, "100.00"), point(40, "100.00", "80.00"), point(45, "100.00", "60.00")]
    found = ref(points, day(46))
    assert found is not None
    assert found.price_net == Decimal("100.00")
    assert found.current_net == Decimal("60.00")
    assert found.reduction_started_at == day(40)


def test_window_edge_uses_business_calendar_days() -> None:
    # Reduction at 2027-02-10 00:30 Rome = 2027-02-09 23:30 UTC. The window opens at 2027-01-11
    # 00:00 Rome (2027-01-10 23:00 UTC): a price in force only until then does not count.
    start = dt.datetime(2027, 2, 9, 23, 30, tzinfo=dt.UTC)
    cheap_until = dt.datetime(2027, 1, 10, 22, 59, tzinfo=dt.UTC)
    points = [
        PricePoint(day(1), Decimal("50.00"), None, None, None),
        PricePoint(cheap_until, Decimal("100.00"), None, None, None),
        PricePoint(start, Decimal("100.00"), Decimal("80.00"), None, None),
    ]
    found = engine.reference_price(points, at=start + dt.timedelta(hours=1), tz=ROME)
    assert found is not None
    assert found.price_net == Decimal("100.00")


def test_a_cleared_price_is_not_a_price() -> None:
    points = [point(1, "100.00"), point(20, None), point(40, "100.00", "90.00")]
    found = ref(points, day(41))
    assert found is not None
    assert found.price_net == Decimal("100.00")
