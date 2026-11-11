"""SHP.3: `engine.quote_shipping`, the pure quote of one vendor's package (spec 06 §6.2, cases
8-13). Outcomes: Rates, IndicativeRates, NeedsQuote, Blocked; never a silent empty list. Rates
are net: VAT is TAX.4's.
"""

from decimal import Decimal

import pytest

from apps.shipping import engine
from apps.shipping.engine import (
    AreaTreatment,
    BandSnap,
    Blocked,
    Destination,
    IndicativeRates,
    MethodSnap,
    NeedsQuote,
    PackageLine,
    PickupSnap,
    Rates,
    ShippingConfigSnap,
    SpecialAreaSnap,
    VendorPackage,
    ZoneSnap,
    quote_shipping,
)

D = Decimal
VENDOR = 1
IT = ZoneSnap(code="it", priority=10, countries=frozenset({"IT"}), vendor_id=None)
WORLD = ZoneSnap(code="world", priority=999, countries=frozenset({"*"}), vendor_id=None)
SARDINIA = SpecialAreaSnap(
    code="it-sardinia",
    kind=engine.ISLAND,
    countries=frozenset({"IT"}),
    postcode_patterns=("07*", "08*", "09*"),
)
FIXED_ONE = AreaTreatment(mode=engine.SURCHARGE, surcharge_type=engine.FIXED, surcharge=D("1"))
BANDS = (
    BandSnap(D("0"), D("5"), D("9.90")),
    BandSnap(D("5"), D("10"), D("14.90")),
)


def method(key: str = "std", **kwargs: object) -> MethodSnap:
    values: dict[str, object] = {
        "key": key,
        "name": key.title(),
        "vendor_id": VENDOR,
        "zone": IT,
        "carrier_code": "main",
        "bands": BANDS,
        "min_delivery_days": 2,
        "max_delivery_days": 5,
        "is_default": True,
    }
    values.update(kwargs)
    return MethodSnap(**values)  # type: ignore[arg-type]


def package(weight: str = "1", goods: str = "50.00", vendor_id: int = VENDOR) -> VendorPackage:
    return VendorPackage(
        vendor_id=vendor_id,
        lines=(PackageLine(weight_kg=D(weight), quantity=1, net_goods=D(goods)),),
    )


def cfg(*methods: MethodSnap, **kwargs: object) -> ShippingConfigSnap:
    return ShippingConfigSnap(methods=methods, areas=(SARDINIA,), **kwargs)  # type: ignore[arg-type]


ROME = Destination("IT", "00100")
CAGLIARI = Destination("IT", "09100")


def test_package_weight_is_sum_of_weight_times_quantity_to_three_decimals() -> None:
    pkg = VendorPackage(
        vendor_id=VENDOR,
        lines=(
            PackageLine(D("0.3335"), 3, D("10")),
            PackageLine(D("1.2"), 2, D("5")),
        ),
    )
    assert pkg.weight_kg == D("3.401")  # 1.0005 + 2.4 = 3.4005 -> half up
    assert pkg.goods_net == D("15.00")


class TestBands:  # case 8
    @pytest.mark.parametrize(
        ("weight", "cost"),
        [("0.001", "9.90"), ("5", "9.90"), ("5.000", "9.90"), ("5.001", "14.90"), ("10", "14.90")],
    )
    def test_bounds_are_inclusive_on_the_right(self, weight: str, cost: str) -> None:
        outcome = quote_shipping(package(weight), ROME, cfg(method()))
        assert isinstance(outcome, Rates)
        assert outcome.rates[0].net == D(cost)

    def test_unlimited_last_band(self) -> None:
        bands = (*BANDS, BandSnap(D("10"), None, D("29.00")))
        outcome = quote_shipping(package("500"), ROME, cfg(method(bands=bands)))
        assert isinstance(outcome, Rates)
        assert outcome.rates[0].net == D("29.00")

    def test_band_surcharge_is_added(self) -> None:
        bands = (BandSnap(D("0"), D("5"), D("9.90"), D("1.10")),)
        outcome = quote_shipping(package(), ROME, cfg(method(bands=bands)))
        assert isinstance(outcome, Rates)
        assert outcome.rates[0].net == D("11.00")

    def test_beyond_last_band_last_band_policy(self) -> None:
        outcome = quote_shipping(package("12"), ROME, cfg(method(over_max_weight=engine.LAST_BAND)))
        assert isinstance(outcome, Rates)
        assert outcome.rates[0].net == D("14.90")

    def test_beyond_last_band_quote_policy(self) -> None:
        outcome = quote_shipping(package("12"), ROME, cfg(method(over_max_weight=engine.QUOTE)))
        assert isinstance(outcome, NeedsQuote)
        assert outcome.reason == engine.OVER_MAX_WEIGHT

    def test_beyond_last_band_block_policy(self) -> None:
        outcome = quote_shipping(package("12"), ROME, cfg(method(over_max_weight=engine.BLOCK)))
        assert isinstance(outcome, Blocked)
        assert outcome.reason == engine.OVER_MAX_WEIGHT

    def test_method_without_policy_takes_the_platform_default(self) -> None:
        config = cfg(method(over_max_weight=""), default_over_max_weight=engine.BLOCK)
        assert isinstance(quote_shipping(package("12"), ROME, config), Blocked)
        config = cfg(method(over_max_weight=""), default_over_max_weight=engine.QUOTE)
        assert isinstance(quote_shipping(package("12"), ROME, config), NeedsQuote)

    def test_over_limit_on_one_method_does_not_hide_the_other(self) -> None:
        small = (BandSnap(D("0"), D("2"), D("5.00")),)
        outcome = quote_shipping(
            package("3"),
            ROME,
            cfg(
                method("small", bands=small, over_max_weight=engine.QUOTE),
                method("big", is_default=False),
            ),
        )
        assert isinstance(outcome, Rates)
        assert [rate.key for rate in outcome.rates] == ["big"]

    def test_weight_in_a_gap_between_bands_needs_a_quote(self) -> None:
        bands = (BandSnap(D("0"), D("2"), D("5")), BandSnap(D("4"), None, D("9")))
        outcome = quote_shipping(package("3"), ROME, cfg(method(bands=bands)))
        assert isinstance(outcome, NeedsQuote)
        assert outcome.reason == engine.NO_BAND

    def test_zero_weight_needs_a_quote(self) -> None:
        outcome = quote_shipping(package("0"), ROME, cfg(method()))
        assert isinstance(outcome, NeedsQuote)
        assert outcome.reason == engine.NO_WEIGHT


class TestZonesAndCarriers:
    def test_country_not_covered_with_quotes_enabled_needs_a_quote(self) -> None:  # case 11
        outcome = quote_shipping(package(), Destination("US", "10001"), cfg(method()))
        assert isinstance(outcome, NeedsQuote)
        assert outcome.reason == engine.COUNTRY_NOT_COVERED
        assert outcome.message

    def test_country_not_covered_with_quotes_disabled_is_blocked(self) -> None:  # case 11
        outcome = quote_shipping(
            package(), Destination("US", "10001"), cfg(method(), quotes_enabled=False)
        )
        assert isinstance(outcome, Blocked)
        assert outcome.reason == engine.COUNTRY_NOT_COVERED
        assert outcome.message

    def test_world_zone_is_the_last_resort(self) -> None:  # case 7
        world = method("world", zone=WORLD, bands=(BandSnap(D("0"), D("2"), D("40")),))
        outcome = quote_shipping(package(), Destination("US", "10001"), cfg(method(), world))
        assert isinstance(outcome, Rates)
        assert [rate.key for rate in outcome.rates] == ["world"]

    def test_other_vendors_methods_never_count(self) -> None:  # case 9
        other = method("theirs", vendor_id=2)
        outcome = quote_shipping(package(), ROME, cfg(other))
        assert isinstance(outcome, NeedsQuote)

    def test_inactive_carrier_offers_nothing(self) -> None:  # case 10
        off = method("off", carrier_active=False)
        outcome = quote_shipping(package(), ROME, cfg(off, method("on", is_default=False)))
        assert isinstance(outcome, Rates)
        assert [rate.key for rate in outcome.rates] == ["on"]

    def test_all_carriers_inactive_is_uncovered_never_empty(self) -> None:  # case 10
        outcome = quote_shipping(package(), ROME, cfg(method(carrier_active=False)))
        assert isinstance(outcome, NeedsQuote)
        assert outcome.reason == engine.COUNTRY_NOT_COVERED

    def test_vendor_zone_beats_the_platform_zone(self) -> None:  # case 15
        own = ZoneSnap(code="own", priority=50, countries=frozenset({"IT"}), vendor_id=VENDOR)
        outcome = quote_shipping(
            package(), ROME, cfg(method("plat"), method("mine", zone=own, is_default=False))
        )
        assert isinstance(outcome, Rates)
        assert [rate.key for rate in outcome.rates] == ["mine"]

    def test_zone_country_is_normalised(self) -> None:
        gr = ZoneSnap(code="gr", priority=10, countries=frozenset({"GR"}))
        outcome = quote_shipping(package(), Destination("EL", "10100"), cfg(method(zone=gr)))
        assert isinstance(outcome, Rates)


class TestRatesList:  # spec rule 7, case 16
    def test_several_methods_default_first_with_delivery_time(self) -> None:
        express = method(
            "express",
            is_default=False,
            service_level=engine.EXPRESS,
            min_delivery_days=1,
            max_delivery_days=2,
            bands=(BandSnap(D("0"), D("5"), D("19.00")),),
        )
        outcome = quote_shipping(package(), ROME, cfg(express, method("std")))
        assert isinstance(outcome, Rates)
        assert [rate.key for rate in outcome.rates] == ["std", "express"]
        assert outcome.rates[0].is_default
        assert (outcome.rates[1].min_days, outcome.rates[1].max_days) == (1, 2)
        assert outcome.rates[1].net == D("19.00")

    def test_rates_carry_what_tax_4_needs(self) -> None:
        std = method(tax_treatment=engine.EXEMPT)
        outcome = quote_shipping(package(), ROME, cfg(std))
        assert isinstance(outcome, Rates)
        rate = outcome.rates[0]
        assert rate.tax_treatment == engine.EXEMPT
        assert rate.zone_code == "it"
        assert rate.carrier_code == "main"
        assert rate.vendor_id == VENDOR
        assert rate.territory_code == ""
        assert rate.net.as_tuple().exponent == -2  # 2 decimals, never a float


class TestFreeShipping:  # rule 6
    def test_free_at_the_threshold_per_method(self) -> None:
        free = method("free", free_shipping_threshold=D("50.00"))
        paid = method("paid", is_default=False, free_shipping_threshold=D("100.00"))
        outcome = quote_shipping(package(goods="50.00"), ROME, cfg(free, paid))
        assert isinstance(outcome, Rates)
        by_key = {rate.key: rate for rate in outcome.rates}
        assert by_key["free"].net == D("0.00")
        assert by_key["free"].free_shipping
        assert by_key["paid"].net == D("9.90")
        assert not by_key["paid"].free_shipping

    def test_just_below_the_threshold_pays(self) -> None:
        free = method(free_shipping_threshold=D("50.00"))
        outcome = quote_shipping(package(goods="49.99"), ROME, cfg(free))
        assert isinstance(outcome, Rates)
        assert outcome.rates[0].net == D("9.90")

    def test_free_shipping_beyond_the_last_band_with_quote_policy_still_quotes(self) -> None:
        free = method(free_shipping_threshold=D("10.00"), over_max_weight=engine.QUOTE)
        outcome = quote_shipping(package("12", goods="500"), ROME, cfg(free))
        assert isinstance(outcome, NeedsQuote)


class TestPickup:  # rule 5, case 13
    PICKUP = PickupSnap(vendor_id=VENDOR, key="shop", label="Shop", cost_net=D("0.00"), address="x")

    def test_pickup_is_offered_besides_the_carrier(self) -> None:
        outcome = quote_shipping(package(), ROME, cfg(method(), pickups=(self.PICKUP,)))
        assert isinstance(outcome, Rates)
        assert [(rate.key, rate.kind) for rate in outcome.rates] == [
            ("std", engine.METHOD_RATE),
            ("shop", engine.PICKUP_RATE),
        ]

    def test_other_vendors_pickup_never_counts(self) -> None:
        other = PickupSnap(vendor_id=2, key="o", label="O", cost_net=D("0"), address="x")
        outcome = quote_shipping(package(), ROME, cfg(method(), pickups=(other,)))
        assert isinstance(outcome, Rates)
        assert [rate.key for rate in outcome.rates] == ["std"]

    def test_pickup_does_not_intercept_an_uncovered_country(self) -> None:
        outcome = quote_shipping(
            package(), Destination("US", "10001"), cfg(method(), pickups=(self.PICKUP,))
        )
        assert isinstance(outcome, NeedsQuote)

    def test_pickup_does_not_hide_the_quote_path_above_the_bands(self) -> None:
        config = cfg(method(over_max_weight=engine.QUOTE), pickups=(self.PICKUP,))
        outcome = quote_shipping(package("12"), ROME, config)
        assert isinstance(outcome, NeedsQuote)
        assert [pick.key for pick in outcome.pickups] == ["shop"]

    def test_with_quotes_disabled_pickup_remains_when_the_carrier_cannot_ship(self) -> None:
        config = cfg(
            method(over_max_weight=engine.QUOTE), pickups=(self.PICKUP,), quotes_enabled=False
        )
        outcome = quote_shipping(package("12"), ROME, config)
        assert isinstance(outcome, Rates)
        assert [rate.key for rate in outcome.rates] == ["shop"]


class TestSpecialAreas:  # case 19
    def test_default_treatment_is_an_indicative_rate(self) -> None:
        outcome = quote_shipping(package(), CAGLIARI, cfg(method()))
        assert isinstance(outcome, IndicativeRates)
        rate = outcome.rates[0]
        assert rate.is_indicative
        assert rate.net == D("9.90")
        assert rate.area_code == "it-sardinia"

    def test_fixed_surcharge_is_an_exact_rate(self) -> None:
        treat = {
            "it-sardinia": AreaTreatment(
                mode=engine.SURCHARGE,
                surcharge_type=engine.FIXED,
                surcharge=D("15.00"),
                extra_days=2,
            )
        }
        outcome = quote_shipping(package(), CAGLIARI, cfg(method(treatments=treat)))
        assert isinstance(outcome, Rates)
        assert outcome.rates[0].net == D("24.90")
        assert not outcome.rates[0].is_indicative
        assert (outcome.rates[0].min_days, outcome.rates[0].max_days) == (4, 7)

    def test_per_kg_and_percent_surcharges_round_half_up(self) -> None:
        pct = {
            "it-sardinia": AreaTreatment(
                mode=engine.SURCHARGE, surcharge_type=engine.PERCENT, surcharge=D("12.5")
            )
        }
        outcome = quote_shipping(package(), CAGLIARI, cfg(method(treatments=pct)))
        assert isinstance(outcome, Rates)
        assert outcome.rates[0].net == D("11.14")  # 9.90 * 1.125 = 11.1375

    def test_quote_only_needs_a_quote(self) -> None:
        treat = {"it-sardinia": AreaTreatment(mode=engine.QUOTE_ONLY)}
        outcome = quote_shipping(package(), CAGLIARI, cfg(method(treatments=treat)))
        assert isinstance(outcome, NeedsQuote)
        assert outcome.reason == engine.AREA_QUOTE_ONLY

    def test_blocked_area_is_blocked(self) -> None:
        treat = {"it-sardinia": AreaTreatment(mode=engine.BLOCKED)}
        outcome = quote_shipping(package(), CAGLIARI, cfg(method(treatments=treat)))
        assert isinstance(outcome, Blocked)
        assert outcome.reason == engine.AREA_BLOCKED

    def test_unavailable_method_leaves_the_others(self) -> None:
        treat = {"it-sardinia": AreaTreatment(mode=engine.UNAVAILABLE)}
        outcome = quote_shipping(
            package(),
            CAGLIARI,
            cfg(
                method("a", treatments=treat),
                method(
                    "b",
                    is_default=False,
                    treatments={
                        "it-sardinia": AreaTreatment(
                            mode=engine.SURCHARGE, surcharge_type=engine.FIXED, surcharge=D("1")
                        )
                    },
                ),
            ),
        )
        assert isinstance(outcome, Rates)
        assert [rate.key for rate in outcome.rates] == ["b"]

    def test_exact_rate_beats_indicative_one(self) -> None:
        treat = {
            "it-sardinia": AreaTreatment(
                mode=engine.SURCHARGE, surcharge_type=engine.FIXED, surcharge=D("1")
            )
        }
        outcome = quote_shipping(
            package(),
            CAGLIARI,
            cfg(method("exact", treatments=treat), method("est", is_default=False)),
        )
        assert isinstance(outcome, Rates)
        assert [rate.key for rate in outcome.rates] == ["exact"]

    def test_quotes_disabled_makes_an_indicative_area_blocked(self) -> None:
        outcome = quote_shipping(package(), CAGLIARI, cfg(method(), quotes_enabled=False))
        assert isinstance(outcome, Blocked)
        assert outcome.reason == engine.QUOTES_DISABLED

    def test_extra_vat_territory_is_carried_to_the_rate(self) -> None:
        livigno = SpecialAreaSnap(
            code="it-livigno",
            kind=engine.EXTRA_VAT,
            countries=frozenset({"IT"}),
            postcode_patterns=("23030",),
            territory_code="IT-23030",
        )
        treat = {
            "it-livigno": AreaTreatment(
                mode=engine.SURCHARGE, surcharge_type=engine.FIXED, surcharge=D("0")
            )
        }
        config = ShippingConfigSnap(methods=(method(treatments=treat),), areas=(livigno,))
        outcome = quote_shipping(package(), Destination("IT", "23030"), config)
        assert isinstance(outcome, Rates)
        assert outcome.rates[0].territory_code == "IT-23030"

    def test_free_shipping_waives_the_band_cost_not_the_surcharge(self) -> None:
        treat = {
            "it-sardinia": AreaTreatment(
                mode=engine.SURCHARGE, surcharge_type=engine.FIXED, surcharge=D("15.00")
            )
        }
        free = method(treatments=treat, free_shipping_threshold=D("10"))
        outcome = quote_shipping(package(goods="50"), CAGLIARI, cfg(free))
        assert isinstance(outcome, Rates)
        assert outcome.rates[0].net == D("15.00")


def test_a_package_of_another_vendor_than_the_config_is_refused() -> None:
    outcome = quote_shipping(package(vendor_id=2), ROME, cfg(method()))
    assert isinstance(outcome, NeedsQuote)
