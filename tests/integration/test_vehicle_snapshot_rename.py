"""FIT.17: a later rename or retirement of a fitment node does not change an order's vehicle line.

It lives here, not in the orders app tests, because `orders` never imports `fitment` (spec 17).
The order keeps plain labels taken from the garage when it was placed.
"""

import pytest

from apps.fitment import services as fitment_services
from apps.fitment.tests.factories import FitmentNodeFactory
from apps.orders import engine
from apps.orders.tests.integration.test_services import place

pytestmark = pytest.mark.django_db


def test_the_order_line_does_not_follow_the_node() -> None:
    make = FitmentNodeFactory.create(name="Acme")
    FitmentNodeFactory.create(name="Roadster", parent=make)
    vehicle = fitment_services.vehicle_from_labels("Acme", "Roadster")
    order = place(vehicle=vehicle.as_dict())

    make.name = "Acme Renamed"
    make.status = "retired"
    make.save()

    order.refresh_from_db()
    assert order.vehicle_snapshot == {"make": "Acme", "model": "Roadster", "year": ""}
    assert "Acme, Roadster" in engine.vehicle_line(order.vehicle_snapshot, "en")
