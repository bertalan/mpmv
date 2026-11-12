"""ORD.10: the alert rule for a succeeded payment without a transfer."""

import datetime as dt

from apps.payments.engine import ALERT_KEY, PaidPayment, payment_without_transfer_alert
from apps.payments.engine import payments_without_transfer as late_payments

NOW = dt.datetime(2027, 3, 1, 12, 0, tzinfo=dt.UTC)
GRACE = dt.timedelta(minutes=60)


def paid(reference: str, minutes_ago: int, *, transfer: bool = False) -> PaidPayment:
    return PaidPayment(reference, NOW - dt.timedelta(minutes=minutes_ago), transfer)


def test_a_payment_inside_the_grace_period_is_not_late() -> None:
    assert late_payments([paid("p1", 59), paid("p2", 60)], NOW, grace=GRACE) == []


def test_a_payment_past_the_grace_period_without_a_transfer_is_late() -> None:
    assert [p.reference for p in late_payments([paid("p1", 61)], NOW, grace=GRACE)] == ["p1"]


def test_a_payment_with_a_transfer_is_never_late() -> None:
    assert late_payments([paid("p1", 600, transfer=True)], NOW, grace=GRACE) == []


def test_late_payments_come_oldest_first_and_the_alert_names_them() -> None:
    alert = payment_without_transfer_alert(
        [paid("new", 90), paid("old", 500), paid("fine", 500, transfer=True)], NOW, grace=GRACE
    )
    assert alert is not None
    assert alert.key == ALERT_KEY
    assert alert.details["count"] == "2"
    assert alert.details["references"] == "old, new"
    assert alert.details["grace_minutes"] == "60"


def test_no_late_payment_no_alert() -> None:
    assert payment_without_transfer_alert([], NOW, grace=GRACE) is None


def test_the_alert_lists_at_most_ten_references() -> None:
    alert = payment_without_transfer_alert(
        [paid(f"p{i:02d}", 100 + i) for i in range(15)], NOW, grace=GRACE
    )
    assert alert is not None
    assert alert.details["count"] == "15"
    assert len(alert.details["references"].split(", ")) == 10
