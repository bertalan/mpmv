"""FIT.6: the anonymous garage cookie value (pure rules, no database)."""

import base64
import json

import pytest

from apps.fitment import garage
from apps.fitment.garage import Garage, Vehicle

A = Vehicle("Acme", "Roadster", "2019")
B = Vehicle("Acme", "Tourer", "2021")
C = Vehicle("Bolt", "Mini", "")
D = Vehicle("Bolt", "Maxi", "2020")


def encode(payload: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def test_a_garage_round_trips_through_its_cookie_value() -> None:
    saved = Garage((A, B), active=1)
    assert garage.parse(garage.dumps(saved)) == saved


def test_the_value_holds_only_make_model_and_year() -> None:
    value = garage.dumps(Garage((A,), active=0))
    payload = json.loads(base64.urlsafe_b64decode(value))
    assert set(payload["vehicles"][0]) == {"make", "model", "year"}
    assert set(payload) == {"v", "vehicles", "active"}


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "not base64 !!",
        encode("a string"),
        encode({"v": 2, "vehicles": []}),
        encode({"v": 1, "vehicles": "x"}),
        encode({"v": 1, "vehicles": [{"make": "Acme"}]}),
        encode({"v": 1, "vehicles": [{"make": "<script>", "model": "x", "year": "1"}]}),
        encode({"v": 1, "vehicles": [{"make": "A" * 500, "model": "x", "year": "1"}]}),
        "A" * 5000,
    ],
)
def test_a_hostile_or_broken_value_is_an_empty_garage(raw: str | None) -> None:
    assert garage.parse(raw) == Garage((), active=None)


def test_extra_keys_and_extra_vehicles_are_dropped() -> None:
    payload = {
        "v": 1,
        "active": 0,
        "vehicles": [
            {"make": "Acme", "model": "Roadster", "year": "2019", "plate": "XX000XX", "id": 3},
            {"make": "Acme", "model": "Tourer", "year": "2021"},
            {"make": "Bolt", "model": "Mini", "year": ""},
            {"make": "Bolt", "model": "Maxi", "year": "2020"},
        ],
    }
    parsed = garage.parse(encode(payload))
    assert parsed.vehicles == (A, B, C)
    assert parsed.active == 0


def test_an_out_of_range_active_index_means_the_first_vehicle() -> None:
    parsed = garage.parse(encode({"v": 1, "active": 9, "vehicles": [A.as_dict()]}))
    assert parsed.active == 0


def test_save_makes_the_new_vehicle_active_and_repeats_are_not_added() -> None:
    saved = garage.save(Garage((), None), A)
    assert (saved.vehicles, saved.active) == ((A,), 0)
    again = garage.save(saved, B)
    assert (again.vehicles, again.active) == ((A, B), 1)
    assert garage.save(again, A) == Garage((A, B), active=0)


def test_a_fourth_vehicle_is_refused() -> None:
    full = Garage((A, B, C), active=0)
    with pytest.raises(garage.GarageFull):
        garage.save(full, D)


def test_remove_drops_one_vehicle_at_once_and_keeps_an_active_one() -> None:
    saved = Garage((A, B, C), active=2)
    removed = garage.remove(saved, 0)
    assert removed.vehicles == (B, C)
    assert removed.active == 1
    assert garage.remove(removed, 1).active == 0
    assert garage.remove(Garage((A,), 0), 0) == Garage((), None)
    assert garage.remove(saved, 7) == saved  # an unknown index changes nothing


def test_the_active_vehicle_snapshot_is_make_model_and_year() -> None:
    assert Garage((A, B), active=1).active_snapshot() == B.as_dict()
    assert Garage((), None).active_snapshot() is None


@pytest.mark.parametrize("days", [1, 180, 365])
def test_a_lifetime_up_to_twelve_months_is_accepted(days: int) -> None:
    assert garage.cookie_max_age(days) == days * 86400


@pytest.mark.parametrize("days", [0, -1, 366, 730])
def test_a_longer_lifetime_is_refused(days: int) -> None:
    with pytest.raises(ValueError, match="12 months"):
        garage.cookie_max_age(days)


def test_the_label_text_is_the_one_the_spec_gives() -> None:
    assert garage.SAVE_LABEL == (
        "Save this vehicle on this browser for your next visits (you can remove it at any time)"
    )
