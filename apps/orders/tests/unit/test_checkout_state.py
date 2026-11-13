"""The checkout session state (ORD.5): a VIES answer backs the reverse charge only for the
vendors it was checked as (D-28)."""

from apps.orders.ui import state


def test_the_vies_answer_counts_when_it_covers_every_vendor_of_the_cart() -> None:
    data = {"vat_validation": 7, "vat_vendors": [1, 2]}
    assert state.vat_validation_for(data, {1, 2}) == 7
    assert state.vat_validation_for(data, {2}) == 7


def test_a_vendor_the_answer_was_not_checked_for_drops_it() -> None:
    data = {"vat_validation": 7, "vat_vendors": [1]}
    assert state.vat_validation_for(data, {1, 2}) is None


def test_no_answer_or_no_covered_vendors_means_no_answer() -> None:
    assert state.vat_validation_for({}, {1}) is None
    assert state.vat_validation_for({"vat_validation": 7}, {1}) is None
    assert state.vat_validation_for({"vat_validation": 7, "vat_vendors": "1"}, {1}) is None
    assert state.vat_validation_for({"vat_validation": 7, "vat_vendors": [1]}, set()) is None
