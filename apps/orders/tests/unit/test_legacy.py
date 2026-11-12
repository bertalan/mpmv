"""MIG.4 / MIG.20 (no database): the pure reading of a legacy order record."""

from decimal import Decimal

import pytest

from apps.orders import legacy
from apps.orders.tests.legacy_records import CANARIES, record


def test_a_clean_record_is_reduced_to_the_tax_fields() -> None:
    parsed = legacy.parse_record(record())
    assert parsed.customer_name == "Mario Rossi"
    assert parsed.vat_number == "IT12345678901"
    assert parsed.billing_country == "IT"
    assert parsed.order_state == "closed"
    assert parsed.vendor_state == "completed"
    assert parsed.vendors[0].lines[0].unit_price_net == Decimal("10.00")


def test_no_canary_reaches_the_parsed_output() -> None:
    parsed = legacy.parse_record(record())
    text = repr(parsed)
    for value in CANARIES.values():
        assert value not in text


def test_a_company_name_wins_over_the_person() -> None:
    parsed = legacy.parse_record(record(billing={"company": "Rossi Srl"}))
    assert parsed.customer_name == "Rossi Srl"


@pytest.mark.parametrize("status", ["processing", "on-hold", "pending"])
def test_an_order_in_progress_is_refused_until_it_is_closed(status: str) -> None:
    with pytest.raises(legacy.OrderInProgress):
        legacy.parse_record(record(status=status))


def test_an_unknown_status_is_a_problem() -> None:
    with pytest.raises(legacy.LegacyRecordError, match="status"):
        legacy.parse_record(record(status="mystery"))


@pytest.mark.parametrize("bad", ["12,50", "-1.00", "1e3", "abc", 12.5, None, "1.001"])
def test_a_malformed_money_value_is_rejected_with_a_report(bad: object) -> None:
    rec = record()
    rec["vendors"][0]["shipping_net"] = bad
    with pytest.raises(legacy.LegacyRecordError) as raised:
        legacy.parse_record(rec)
    assert any("shipping" in p for p in raised.value.problems)


def test_all_problems_are_reported_together() -> None:
    rec = record(created_at="yesterday", tax_total="x")
    with pytest.raises(legacy.LegacyRecordError) as raised:
        legacy.parse_record(rec)
    assert len(raised.value.problems) >= 2


def test_the_tax_total_must_equal_the_legacy_tax_lines() -> None:
    with pytest.raises(legacy.LegacyRecordError, match="tax lines"):
        legacy.parse_record(record(tax_total="5.51"))


def test_an_order_with_no_tax_line_is_legitimate_for_an_exempt_sale() -> None:
    rec = record(tax_total="0.00")
    rec["vendors"][0]["lines"][0]["tax_rate"] = "0"
    rec["vendors"][0]["shipping_tax_rate"] = "0"
    assert legacy.parse_record(rec).order_state == "closed"


def test_a_zero_price_line_is_refused() -> None:
    rec = record(tax_total="1.10")
    rec["vendors"][0]["lines"][0]["unit_price_net"] = "0.00"
    with pytest.raises(legacy.LegacyRecordError, match="zero price"):
        legacy.parse_record(rec)


def test_a_naive_date_is_refused() -> None:
    with pytest.raises(legacy.LegacyRecordError, match="timezone-aware"):
        legacy.parse_record(record(created_at="2024-03-10T09:30:00"))


# --- refunds (MIG.20) -------------------------------------------------------------------------


def test_a_partial_refund_is_kept_inside_the_order() -> None:
    parsed = legacy.parse_record(
        record(refunds=[{"amount": "5.00", "date": "2024-04-01", "reason": "damaged"}])
    )
    assert parsed.refunds == ({"amount": "5.00", "date": "2024-04-01", "reason": "damaged"},)
    assert parsed.refunded_total == Decimal("5.00")
    assert (parsed.order_state, parsed.vendor_state) == ("partially_refunded", "completed")


def test_a_full_refund_makes_the_order_refunded() -> None:
    parsed = legacy.parse_record(
        record(refunds=[{"amount": "30.50", "date": "2024-04-01", "reason": ""}])
    )
    assert (parsed.order_state, parsed.vendor_state) == ("refunded", "refunded")


def test_a_refund_amount_that_does_not_parse_is_rejected_with_a_report() -> None:
    with pytest.raises(legacy.LegacyRecordError, match="refund 1 amount"):
        legacy.parse_record(
            record(refunds=[{"amount": "five", "date": "2024-04-01", "reason": ""}])
        )


def test_refunds_above_the_total_are_refused() -> None:
    with pytest.raises(legacy.LegacyRecordError, match="exceed"):
        legacy.parse_record(
            record(refunds=[{"amount": "30.51", "date": "2024-04-01", "reason": ""}])
        )


@pytest.mark.parametrize("status", ["cancelled", "failed"])
def test_cancelled_and_failed_orders_load_as_cancelled(status: str) -> None:
    parsed = legacy.parse_record(record(status=status, paid_at=None))
    assert (parsed.order_state, parsed.vendor_state) == ("cancelled", "cancelled")
