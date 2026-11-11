"""Withdrawal rules (CMP.6 2/2, spec 11): the window, the labels and the pre-contractual blocks."""

import datetime as dt
import zoneinfo

import pytest

from apps.compliance import engine

ROME = zoneinfo.ZoneInfo("Europe/Rome")
UTC = dt.UTC
CONTRACT = dt.datetime(2027, 3, 1, 10, 0, tzinfo=UTC)
NOW = dt.datetime(2027, 3, 5, 12, 0, tzinfo=UTC)


def window(**overrides: object) -> engine.WithdrawalWindow:
    args: dict[str, object] = {
        "is_consumer": True,
        "contract_at": CONTRACT,
        "delivered_at": None,
        "vendor_order_state": "accepted",
        "period_days": 14,
        "now": NOW,
        "tz": ROME,
    }
    args.update(overrides)
    return engine.withdrawal_window(**args)  # type: ignore[arg-type]


def test_a_paid_undelivered_order_is_open_with_no_end_yet() -> None:
    result = window()
    assert result.is_open
    assert result.opens_at == CONTRACT
    assert result.closes_at is None  # the 14 days start when the goods arrive (art. 9(2)(b))


def test_delivery_starts_the_period_and_it_ends_at_midnight_of_the_last_day() -> None:
    delivered = dt.datetime(2027, 3, 3, 15, 30, tzinfo=UTC)
    result = window(delivered_at=delivered)
    # 3 March + 14 days = 17 March, end of that local day (Europe/Rome is UTC+1 in March).
    assert result.closes_at == dt.datetime(2027, 3, 17, 23, 59, 59, 999999, tzinfo=ROME)
    assert result.is_open


def test_the_last_day_counts_and_the_next_one_does_not() -> None:
    delivered = dt.datetime(2027, 3, 3, 15, 30, tzinfo=UTC)
    last = dt.datetime(2027, 3, 17, 22, 59, tzinfo=UTC)  # 23:59 in Rome
    after = dt.datetime(2027, 3, 17, 23, 1, tzinfo=UTC)  # 00:01 on the 18th in Rome
    assert window(delivered_at=delivered, now=last).is_open
    closed = window(delivered_at=delivered, now=after)
    assert not closed.is_open
    assert closed.reason == "expired"


def test_the_period_follows_the_setting() -> None:
    delivered = dt.datetime(2027, 3, 3, 15, 30, tzinfo=UTC)
    result = window(delivered_at=delivered, period_days=30)
    assert result.closes_at is not None
    assert result.closes_at.astimezone(ROME).date() == dt.date(2027, 4, 2)


def test_a_period_below_the_legal_minimum_is_raised_to_14_days() -> None:
    assert engine.effective_withdrawal_days(7) == 14
    assert engine.effective_withdrawal_days(14) == 14
    assert engine.effective_withdrawal_days(30) == 30


def test_a_business_buyer_has_no_withdrawal_right() -> None:
    result = window(is_consumer=False)
    assert not result.is_open
    assert result.reason == "not_consumer"


def test_an_order_that_was_never_paid_has_no_contract_yet() -> None:
    result = window(contract_at=None)
    assert not result.is_open
    assert result.reason == "not_paid"


@pytest.mark.parametrize("state", ["cancelled", "refunded"])
def test_a_cancelled_or_refunded_vendor_order_is_closed(state: str) -> None:
    result = window(vendor_order_state=state)
    assert not result.is_open
    assert result.reason == state


def test_an_aware_datetime_is_required() -> None:
    with pytest.raises(ValueError, match="aware"):
        window(now=dt.datetime(2027, 3, 5, 12, 0))


def test_labels_are_the_directive_texts_in_italian_and_english() -> None:
    english = engine.withdrawal_labels("en")
    italian = engine.withdrawal_labels("it")
    assert english.button == "withdraw from contract here"
    assert english.confirm == "confirm withdrawal"
    assert italian.button == "recedere dal contratto qui"
    assert italian.confirm == "conferma recesso"
    assert (english.verified, italian.verified) == (True, True)


@pytest.mark.parametrize("language", ["de", "fr", "es"])
def test_other_languages_show_english_until_counsel_verifies_them(language: str) -> None:
    labels = engine.withdrawal_labels(language)
    assert labels == engine.withdrawal_labels("en")
    assert language in engine.PROVISIONAL_LABELS  # kept for when counsel signs off


def test_an_unknown_language_falls_back_to_english() -> None:
    assert engine.withdrawal_labels("xx") == engine.withdrawal_labels("en")


def test_the_precontractual_blocks_say_where_the_function_is() -> None:
    info = engine.precontractual_info(
        language="it",
        period_days=14,
        withdrawal_url="/it/withdrawal/",
        legal_guarantee_months=24,
    )
    assert info.withdrawal.period_days == 14
    assert info.withdrawal.url == "/it/withdrawal/"
    assert info.withdrawal.button_label == "recedere dal contratto qui"
    assert info.guarantee.months == 24
    assert info.guarantee.notice_is_draft  # the harmonised wording is not in code (owner Q)


def test_the_precontractual_info_needs_a_function_url() -> None:
    with pytest.raises(ValueError, match="url"):
        engine.precontractual_info(
            language="en", period_days=14, withdrawal_url="", legal_guarantee_months=24
        )
