"""FIT.17: the vehicle snapshot an order keeps (pure rules, no database)."""

import pytest

from apps.orders import engine


def test_a_snapshot_keeps_make_model_and_year_labels_only() -> None:
    raw = {"make": " Acme ", "model": "Roadster", "year": "2019", "plate": "XX000XX", "id": 7}
    assert engine.clean_vehicle_snapshot(raw) == {
        "make": "Acme",
        "model": "Roadster",
        "year": "2019",
    }


def test_no_vehicle_means_no_snapshot() -> None:
    assert engine.clean_vehicle_snapshot(None) == {}
    assert engine.clean_vehicle_snapshot({}) == {}


@pytest.mark.parametrize(
    "raw",
    [
        "Acme Roadster 2019",
        {"make": "", "model": "Roadster", "year": "2019"},
        {"make": "Acme", "model": 5, "year": "2019"},
        {"make": "Acme", "model": "x" * 200, "year": "2019"},
        {"make": "Ac\nme", "model": "Roadster", "year": "2019"},
        {"make": "Acme", "model": "Roadster"},
    ],
)
def test_a_malformed_snapshot_is_refused(raw: object) -> None:
    with pytest.raises(ValueError, match="vehicle"):
        engine.clean_vehicle_snapshot(raw)


def test_the_year_may_be_absent_when_the_scheme_has_no_year() -> None:
    assert engine.clean_vehicle_snapshot({"make": "Acme", "model": "Roadster", "year": ""}) == {
        "make": "Acme",
        "model": "Roadster",
        "year": "",
    }


def test_the_vehicle_label_joins_the_parts_and_skips_the_empty_year() -> None:
    assert engine.vehicle_label({"make": "Acme", "model": "Roadster", "year": "2019"}) == (
        "Acme, Roadster, 2019"
    )
    assert engine.vehicle_label({"make": "Acme", "model": "Roadster", "year": ""}) == (
        "Acme, Roadster"
    )
    assert engine.vehicle_label({}) == ""
