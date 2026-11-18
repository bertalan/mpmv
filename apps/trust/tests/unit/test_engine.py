"""TRU.1: the pure trust engine, badges 1 to 7 (spec 16 §16.11 cases 4, 6 and 7). No database."""

import datetime as dt
from decimal import Decimal

import pytest

from apps.trust import engine
from apps.trust.engine import (
    DeliveredOrder,
    Dispatch,
    DisputeFact,
    PublishedReview,
    TrustRulesData,
    VendorTrustInputs,
    compute_vendor_trust,
)

AS_OF = dt.datetime(2027, 3, 2, 12, 0, tzinfo=dt.UTC)
RULES = TrustRulesData(version=1)


def ago(days: int) -> dt.datetime:
    return AS_OF - dt.timedelta(days=days)


def inputs(**kwargs: object) -> VendorTrustInputs:
    return VendorTrustInputs(vendor_ref="v1", as_of=AS_OF, **kwargs)  # type: ignore[arg-type]


def badge(result: engine.VendorTrustResult, number: int) -> engine.BadgeResult:
    return next(b for b in result.badges if b.number == number)


def dispatches(total: int, on_time: int) -> tuple[Dispatch, ...]:
    return tuple(
        Dispatch(ref=f"d{i}", shipped_at=ago(10), on_time=i < on_time) for i in range(total)
    )


def delivered(
    count: int, *, delivered_days: int = 30, net: str = "50.00"
) -> tuple[DeliveredOrder, ...]:
    return tuple(
        DeliveredOrder(
            ref=f"o{i}",
            buyer=f"b{i}",
            delivered_at=ago(delivered_days),
            net_goods=Decimal(net),
            country="IT",
        )
        for i in range(count)
    )


# --- §16.11 case 4: a withdrawal is never a dispute; an upheld warranty return is.


def test_a_withdrawal_never_counts_as_a_dispute_and_an_upheld_warranty_does() -> None:
    orders = delivered(20)
    withdrawals = tuple(
        DisputeFact(ref=f"w{i}", occurred_at=ago(20), kind="withdrawal") for i in range(10)
    )
    result = compute_vendor_trust(inputs(delivered_orders=orders, disputes=withdrawals), RULES)
    assert badge(result, 6).earned
    assert badge(result, 6).figures["upheld_disputes"] == 0

    warranty = (DisputeFact(ref="w", occurred_at=ago(20), kind="warranty"),)
    result = compute_vendor_trust(inputs(delivered_orders=orders, disputes=warranty), RULES)
    assert badge(result, 6).figures["upheld_disputes"] == 1  # 1 of 20 = 5%
    assert not badge(result, 6).earned


@pytest.mark.parametrize(
    "kind", ["warranty", "not_as_described", "payment_dispute", "notice_upheld"]
)
def test_every_upheld_kind_counts(kind: str) -> None:
    facts = (DisputeFact(ref="x", occurred_at=ago(5), kind=kind),)
    result = compute_vendor_trust(inputs(delivered_orders=delivered(20), disputes=facts), RULES)
    assert badge(result, 6).figures["upheld_disputes"] == 1


def test_an_unknown_dispute_kind_is_refused() -> None:
    facts = (DisputeFact(ref="x", occurred_at=ago(5), kind="whatever"),)
    with pytest.raises(ValueError, match="dispute kind"):
        compute_vendor_trust(inputs(delivered_orders=delivered(20), disputes=facts), RULES)


def test_the_dispute_rate_has_a_minimum_of_delivered_orders() -> None:
    result = compute_vendor_trust(inputs(delivered_orders=delivered(19)), RULES)
    assert not badge(result, 6).earned
    assert badge(result, 6).missing == "not_enough_orders"


def test_dispute_hysteresis_earn_under_3_lose_over_4() -> None:
    orders = delivered(100)

    def run(disputes: int, previous: bool) -> bool:
        facts = tuple(
            DisputeFact(ref=f"d{i}", occurred_at=ago(9), kind="warranty") for i in range(disputes)
        )
        result = compute_vendor_trust(
            inputs(delivered_orders=orders, disputes=facts), RULES, {6: previous}
        )
        return badge(result, 6).earned

    assert run(2, previous=False)  # 2% earns
    assert not run(3, previous=False)  # 3% does not earn from scratch
    assert run(4, previous=True)  # 4% keeps
    assert not run(5, previous=True)  # 5% loses


def test_the_dispute_rate_is_never_a_public_figure() -> None:
    result = compute_vendor_trust(inputs(delivered_orders=delivered(30)), RULES)
    assert badge(result, 6).public_figures == {}


# --- §16.11 case 6: the latest review is the most recent one, whatever its rating.


def test_the_latest_review_is_the_most_recent_whatever_its_rating() -> None:
    reviews = (
        PublishedReview(ref="a", published_at=ago(30), rating=5, excerpt="great"),
        PublishedReview(ref="b", published_at=ago(1), rating=1, excerpt="bad"),
        PublishedReview(ref="c", published_at=ago(10), rating=5, excerpt="fine"),
    )
    result = compute_vendor_trust(inputs(reviews=reviews), RULES)
    latest = badge(result, 2).figures["latest"]
    assert latest["ref"] == "b"
    assert latest["rating"] == 1


def test_the_latest_review_excerpt_is_cut_at_200_characters() -> None:
    reviews = (PublishedReview(ref="a", published_at=ago(1), rating=4, excerpt="x" * 500),)
    latest = badge(compute_vendor_trust(inputs(reviews=reviews), RULES), 2).figures["latest"]
    assert len(latest["excerpt"]) == 200


def test_review_count_from_one_and_stars_from_five() -> None:
    def run(n: int) -> engine.BadgeResult:
        reviews = tuple(
            PublishedReview(ref=f"r{i}", published_at=ago(i + 1), rating=4) for i in range(n)
        )
        return badge(compute_vendor_trust(inputs(reviews=reviews), RULES), 2)

    assert not run(0).earned
    assert run(1).earned
    assert not run(1).figures["stars_shown"]
    assert run(4).figures["mean"] == "4.00"
    assert not run(4).figures["stars_shown"]
    assert run(5).figures["stars_shown"]


def test_the_review_mean_rounds_half_up() -> None:
    ratings = (5, 4, 4, 4, 4, 4, 4, 4)  # 33 / 8 = 4.125
    reviews = tuple(
        PublishedReview(ref=f"r{i}", published_at=ago(i + 1), rating=r)
        for i, r in enumerate(ratings)
    )
    assert badge(compute_vendor_trust(inputs(reviews=reviews), RULES), 2).figures["mean"] == "4.13"


# --- §16.11 case 7: hysteresis on dispatch on time.


def test_dispatch_hysteresis_89_keeps_a_gained_badge_and_84_loses_it() -> None:
    # 89 of 100 on time is 89%.
    kept = compute_vendor_trust(inputs(dispatches=dispatches(100, 89)), RULES, {5: True})
    assert badge(kept, 5).earned
    lost = compute_vendor_trust(inputs(dispatches=dispatches(100, 84)), RULES, {5: True})
    assert not badge(lost, 5).earned
    assert badge(lost, 5).missing == "below_threshold"


def test_dispatch_is_earned_from_90_and_not_from_89_without_a_previous_badge() -> None:
    assert badge(compute_vendor_trust(inputs(dispatches=dispatches(100, 90)), RULES), 5).earned
    assert not badge(compute_vendor_trust(inputs(dispatches=dispatches(100, 89)), RULES), 5).earned


def test_dispatch_needs_ten_orders_in_the_last_90_days() -> None:
    result = compute_vendor_trust(inputs(dispatches=dispatches(9, 9)), RULES, {5: True})
    assert not badge(result, 5).earned
    assert badge(result, 5).missing == "not_enough_orders"
    old = tuple(Dispatch(ref=f"d{i}", shipped_at=ago(91), on_time=True) for i in range(12))
    assert not badge(compute_vendor_trust(inputs(dispatches=old), RULES), 5).earned


# --- Badge 1: orders delivered.


def test_orders_tiers_and_the_withdrawal_window() -> None:
    assert badge(compute_vendor_trust(inputs(delivered_orders=delivered(9)), RULES), 1).tier is None
    result = compute_vendor_trust(inputs(delivered_orders=delivered(10)), RULES)
    assert badge(result, 1).earned
    assert badge(result, 1).tier == 10
    assert badge(compute_vendor_trust(inputs(delivered_orders=delivered(60)), RULES), 1).tier == 50
    # Delivered 13 days ago: the withdrawal window is still open.
    recent = delivered(10, delivered_days=13)
    assert (
        badge(compute_vendor_trust(inputs(delivered_orders=recent), RULES), 1).figures["count"] == 0
    )


def test_orders_below_the_floor_or_wholly_refunded_do_not_count() -> None:
    cheap = delivered(10, net="4.99")
    assert (
        badge(compute_vendor_trust(inputs(delivered_orders=cheap), RULES), 1).figures["count"] == 0
    )
    refunded = tuple(
        DeliveredOrder(
            ref=o.ref,
            buyer=o.buyer,
            delivered_at=o.delivered_at,
            net_goods=o.net_goods,
            country="IT",
            wholly_refunded=True,
        )
        for o in delivered(10)
    )
    assert (
        badge(compute_vendor_trust(inputs(delivered_orders=refunded), RULES), 1).figures["count"]
        == 0
    )


def test_one_buyer_counts_for_at_most_three_orders_in_twelve_months() -> None:
    orders = tuple(
        DeliveredOrder(
            ref=f"o{i}",
            buyer="same",
            delivered_at=ago(20 + i),
            net_goods=Decimal("10"),
            country="IT",
        )
        for i in range(10)
    )
    assert (
        badge(compute_vendor_trust(inputs(delivered_orders=orders), RULES), 1).figures["count"] == 3
    )
    # A fourth order more than 12 months after the first is counted again.
    spread = tuple(
        DeliveredOrder(
            ref=f"o{i}",
            buyer="same",
            delivered_at=ago(20 + 100 * i),
            net_goods=Decimal("10"),
            country="IT",
        )
        for i in range(6)
    )
    assert (
        badge(compute_vendor_trust(inputs(delivered_orders=spread), RULES), 1).figures["count"] > 3
    )


# --- Badge 3, 4, 7.


def test_countries_need_two_orders_from_two_buyers() -> None:
    def order(ref: str, buyer: str, country: str) -> DeliveredOrder:
        return DeliveredOrder(
            ref=ref, buyer=buyer, delivered_at=ago(30), net_goods=Decimal("20"), country=country
        )

    orders = (
        order("1", "a", "DE"),
        order("2", "b", "DE"),  # counts
        order("3", "a", "FR"),
        order("4", "a", "FR"),  # one buyer only: no
        order("5", "c", "ES"),  # one order: no
    )
    result = badge(compute_vendor_trust(inputs(delivered_orders=orders), RULES), 3)
    assert result.figures["countries"] == ["DE"]
    assert result.earned


def test_repeat_customers_need_two_orders_seven_days_apart_and_show_from_three() -> None:
    def pair(buyer: str, gap: int) -> tuple[DeliveredOrder, DeliveredOrder]:
        return (
            DeliveredOrder(
                ref=f"{buyer}1",
                buyer=buyer,
                delivered_at=ago(100),
                net_goods=Decimal("20"),
                country="IT",
            ),
            DeliveredOrder(
                ref=f"{buyer}2",
                buyer=buyer,
                delivered_at=ago(100 - gap),
                net_goods=Decimal("20"),
                country="IT",
            ),
        )

    orders = (*pair("a", 7), *pair("b", 30), *pair("c", 6))
    result = badge(compute_vendor_trust(inputs(delivered_orders=orders), RULES), 4)
    assert result.figures["count"] == 2
    assert not result.earned
    result = badge(
        compute_vendor_trust(inputs(delivered_orders=(*orders, *pair("d", 9))), RULES), 4
    )
    assert result.figures["count"] == 3
    assert result.earned


def test_on_mpmv_since_is_the_activation_year() -> None:
    first = dt.datetime(2026, 5, 1, tzinfo=dt.UTC)
    result = compute_vendor_trust(inputs(first_activated_at=first), RULES)
    assert badge(result, 7).earned
    assert badge(result, 7).figures["since"] == 2026
    assert not badge(compute_vendor_trust(inputs(), RULES), 7).earned


# --- Shape of the result.


def test_the_result_carries_the_rules_version_and_is_json_safe() -> None:
    import json

    result = compute_vendor_trust(inputs(delivered_orders=delivered(12)), TrustRulesData(version=7))
    assert result.rules_version == 7
    assert result.as_of == AS_OF
    assert [b.number for b in result.badges] == [1, 2, 3, 4, 5, 6, 7]
    json.dumps(result.to_json())


def test_every_missing_badge_says_why() -> None:
    result = compute_vendor_trust(inputs(), RULES)
    assert all(b.missing for b in result.badges if not b.earned)


def test_a_new_rules_version_changes_the_result() -> None:
    orders = delivered(10)
    strict = TrustRulesData(version=2, orders_tiers=(20, 50))
    assert badge(compute_vendor_trust(inputs(delivered_orders=orders), RULES), 1).earned
    assert not badge(compute_vendor_trust(inputs(delivered_orders=orders), strict), 1).earned


def test_the_engine_does_not_mutate_its_inputs_and_is_deterministic() -> None:
    data = inputs(delivered_orders=delivered(12), dispatches=dispatches(20, 19))
    assert compute_vendor_trust(data, RULES) == compute_vendor_trust(data, RULES)


def test_rules_validation() -> None:
    assert engine.rules_errors(RULES) == {}
    assert "dispatch_lose_percent" in engine.rules_errors(
        TrustRulesData(
            version=1, dispatch_earn_percent=Decimal(80), dispatch_lose_percent=Decimal(85)
        )
    )
    assert "dispute_lose_percent" in engine.rules_errors(
        TrustRulesData(version=1, dispute_earn_percent=Decimal(5), dispute_lose_percent=Decimal(4))
    )
    assert "orders_tiers" in engine.rules_errors(TrustRulesData(version=1, orders_tiers=(50, 10)))


def test_rules_round_trip_through_params() -> None:
    params = engine.rules_to_params(RULES)
    assert engine.rules_from_params(1, params) == RULES
    with pytest.raises(ValueError, match="unknown"):
        engine.rules_from_params(1, {**params, "nope": 1})
