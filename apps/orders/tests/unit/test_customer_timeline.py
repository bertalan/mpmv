"""ORD.7: the customer's status timeline of one vendor part and the tracking link (pure rules)."""

import datetime as dt

import pytest

from apps.orders import engine

PLACED = dt.datetime(2027, 3, 1, 9, 0, tzinfo=dt.UTC)
PAID = dt.datetime(2027, 3, 1, 9, 5, tzinfo=dt.UTC)
SHIPPED = dt.datetime(2027, 3, 3, 9, 0, tzinfo=dt.UTC)
DELIVERED = dt.datetime(2027, 3, 5, 9, 0, tzinfo=dt.UTC)


def steps(state: str, **times: dt.datetime | None) -> list[tuple[str, str]]:
    values = {"placed_at": PLACED, "paid_at": None, "shipped_at": None, "delivered_at": None}
    values.update(times)
    return [(s.key, s.status) for s in engine.vendor_timeline(state, **values)]  # type: ignore[arg-type]


def test_an_unpaid_pending_part_has_only_the_first_step_done() -> None:
    assert steps("pending") == [
        ("placed", "current"),
        ("paid", "upcoming"),
        ("accepted", "upcoming"),
        ("shipped", "upcoming"),
        ("delivered", "upcoming"),
    ]


def test_a_paid_pending_part_is_waiting_for_the_vendor() -> None:
    assert steps("pending", paid_at=PAID)[:3] == [
        ("placed", "done"),
        ("paid", "current"),
        ("accepted", "upcoming"),
    ]


def test_a_shipped_part_shows_its_time() -> None:
    result = engine.vendor_timeline(
        "shipped", placed_at=PLACED, paid_at=PAID, shipped_at=SHIPPED, delivered_at=None
    )
    by_key = {s.key: s for s in result}
    assert by_key["shipped"].status == "current"
    assert by_key["shipped"].at == SHIPPED
    assert by_key["accepted"].status == "done"
    assert by_key["delivered"].status == "upcoming"


@pytest.mark.parametrize("state", ["delivered", "completed"])
def test_a_delivered_part_is_complete(state: str) -> None:
    result = steps(state, paid_at=PAID, shipped_at=SHIPPED, delivered_at=DELIVERED)
    assert [status for _, status in result] == ["done", "done", "done", "done", "current"]


def test_a_cancelled_part_ends_in_a_cancelled_step() -> None:
    assert steps("cancelled", paid_at=PAID) == [
        ("placed", "done"),
        ("paid", "done"),
        ("cancelled", "current"),
    ]


def test_a_refunded_part_keeps_the_steps_it_reached() -> None:
    assert steps("refunded", paid_at=PAID, shipped_at=SHIPPED) == [
        ("placed", "done"),
        ("paid", "done"),
        ("accepted", "done"),
        ("shipped", "done"),
        ("refunded", "current"),
    ]


def test_tracking_link_fills_the_number_and_escapes_it() -> None:
    link = engine.tracking_link("https://t.example.com/{tracking_number}", "AB 12/3")
    assert link == "https://t.example.com/AB%2012%2F3"


@pytest.mark.parametrize(
    ("template", "number"),
    [
        ("", "AB123"),
        ("https://t.example.com/{tracking_number}", ""),
        ("https://t.example.com/", "AB123"),
        ("javascript:alert({tracking_number})", "AB123"),
        ("http://t.example.com/{tracking_number}", "AB123"),
        ("//t.example.com/{tracking_number}", "AB123"),
    ],
)
def test_tracking_link_is_empty_unless_it_is_a_safe_https_link(template: str, number: str) -> None:
    assert engine.tracking_link(template, number) == ""
