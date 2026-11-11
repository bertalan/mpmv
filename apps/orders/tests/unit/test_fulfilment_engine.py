"""ORD.6: the vendor's fulfilment actions and the tracking number rules (pure, no database)."""

import pytest

from apps.orders import engine


def test_the_three_actions_map_to_vendor_order_states() -> None:
    assert engine.FULFILMENT_ACTIONS == {
        "accept": "accepted",
        "ship": "shipped",
        "deliver": "delivered",
    }


def test_an_unknown_action_is_refused() -> None:
    with pytest.raises(engine.UnknownFulfilmentAction):
        engine.fulfilment_target("cancel")


@pytest.mark.parametrize("raw", ["1Z999AA10123456784", "  RR123456789IT ", "AB-12/34 56", "123"])
def test_a_plausible_tracking_number_is_trimmed_and_kept(raw: str) -> None:
    assert engine.normalise_tracking(raw) == raw.strip()


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "12",
        "x" * 101,
        "<script>",
        "abc\ndef",
        "https://evil.example/x",
        "a;b",
        "ab\x00cd",
    ],
)
def test_an_implausible_tracking_number_is_refused(raw: str) -> None:
    with pytest.raises(engine.InvalidTracking):
        engine.normalise_tracking(raw)


def test_a_carrier_is_optional_and_trimmed() -> None:
    assert engine.normalise_carrier("  Poste  ") == "Poste"
    assert engine.normalise_carrier("") == ""


@pytest.mark.parametrize("raw", ["x" * 101, "bad\nname", "<b>"])
def test_a_bad_carrier_is_refused(raw: str) -> None:
    with pytest.raises(engine.InvalidTracking):
        engine.normalise_carrier(raw)
