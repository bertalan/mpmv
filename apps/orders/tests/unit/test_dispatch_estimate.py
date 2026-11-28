"""VEN.20: after a closure the delivery estimate counts from the dispatch date."""

import datetime as dt

import pytest
from django.utils import translation

from apps.orders.ui.present import _delivery

pytestmark = pytest.mark.unit


def test_without_a_shifted_dispatch_the_estimate_stays_in_working_days() -> None:
    with translation.override("en"):
        assert _delivery(2, 4) == "2 to 4 working days"


def test_a_shifted_dispatch_gives_the_delivery_dates_from_that_day() -> None:
    with translation.override("en"):
        assert _delivery(2, 4, dt.date(2027, 9, 1)) == (
            "estimated delivery from Sept. 3, 2027 to Sept. 5, 2027"
        )
        assert _delivery(None, 3, dt.date(2027, 9, 1)) == "estimated delivery on Sept. 4, 2027"
