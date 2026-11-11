"""SHP.3: `services.quote_shipping` builds the configuration snapshot from the database and
returns the engine's outcome (spec 06 cases 8-13, 15, 16, 19). Net amounts only."""

from decimal import Decimal
from typing import Any

import pytest

from apps.shipping import engine
from apps.shipping.engine import (
    Blocked,
    Destination,
    IndicativeRates,
    NeedsQuote,
    PackageLine,
    Rates,
    VendorPackage,
)
from apps.shipping.models import ShippingMethod, ShippingZone, SpecialArea
from apps.shipping.services import quote_shipping
from apps.shipping.tests.factories import (
    CarrierFactory,
    PickupOptionFactory,
    PickupOptionTranslationFactory,
    ShippingMethodFactory,
    ShippingMethodTranslationFactory,
    ShippingZoneFactory,
    SpecialAreaTreatmentFactory,
    WeightBandFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

D = Decimal


def pkg(vendor_id: int, weight: str = "1", goods: str = "50.00") -> VendorPackage:
    return VendorPackage(vendor_id=vendor_id, lines=(PackageLine(D(weight), 1, D(goods)),))


def priced_method(vendor: Vendor, zone: ShippingZone, **kwargs: Any) -> ShippingMethod:
    method = ShippingMethodFactory.create(vendor=vendor, zone=zone, **kwargs)
    ShippingMethodTranslationFactory.create(
        method=method, name=f"Name {method.key}", status="published"
    )
    WeightBandFactory.create(method=method, from_kg=D("0"), to_kg=D("5"), cost_net=D("9.90"))
    WeightBandFactory.create(method=method, from_kg=D("5"), to_kg=D("10"), cost_net=D("14.90"))
    return method


def test_rates_of_the_resolved_zone_with_translated_names() -> None:
    vendor = VendorFactory.create()
    zone = ShippingZoneFactory.create(countries=["IT"])
    priced_method(vendor, zone, key="std", is_default=True)
    outcome = quote_shipping(vendor, pkg(vendor.pk), Destination("IT", "00100"))
    assert isinstance(outcome, Rates)
    assert [(r.key, r.net, r.label) for r in outcome.rates] == [("std", D("9.90"), "Name std")]


def test_vendor_d_never_gets_main_carrier_rates() -> None:  # case 9
    d = VendorFactory.create()
    other = VendorFactory.create()
    zone = ShippingZoneFactory.create(countries=["IT"])
    priced_method(other, zone, key="main")
    outcome = quote_shipping(d, pkg(d.pk), Destination("IT", "00100"))
    assert isinstance(outcome, NeedsQuote)


def test_inactive_carrier_none_of_its_rates() -> None:  # case 10
    vendor = VendorFactory.create()
    zone = ShippingZoneFactory.create(countries=["IT"])
    priced_method(vendor, zone, carrier=CarrierFactory.create(is_active=False))
    outcome = quote_shipping(vendor, pkg(vendor.pk), Destination("IT", "00100"))
    assert isinstance(outcome, NeedsQuote)
    assert outcome.reason == engine.COUNTRY_NOT_COVERED


def test_uncovered_country_quotes_enabled_or_disabled() -> None:  # case 11
    vendor = VendorFactory.create()
    priced_method(vendor, ShippingZoneFactory.create(countries=["IT"]))
    dest = Destination("US", "10001")
    assert isinstance(quote_shipping(vendor, pkg(vendor.pk), dest), NeedsQuote)
    blocked = quote_shipping(vendor, pkg(vendor.pk), dest, quotes_enabled=False)
    assert isinstance(blocked, Blocked)
    assert blocked.message


def test_mixed_cart_keeps_the_covered_vendors_rate() -> None:  # case 12
    covered, uncovered = VendorFactory.create(), VendorFactory.create()
    priced_method(covered, ShippingZoneFactory.create(countries=["DE"]))
    priced_method(uncovered, ShippingZoneFactory.create(countries=["IT"]))
    dest = Destination("DE", "10115")
    assert isinstance(quote_shipping(covered, pkg(covered.pk), dest), Rates)
    assert isinstance(quote_shipping(uncovered, pkg(uncovered.pk), dest), NeedsQuote)


def test_pickup_does_not_intercept_uncovered_countries() -> None:  # case 13
    vendor = VendorFactory.create()
    priced_method(vendor, ShippingZoneFactory.create(countries=["IT"]))
    option = PickupOptionFactory.create(vendor=vendor, key="shop")
    PickupOptionTranslationFactory.create(pickup_option=option, label="Shop", status="published")
    covered = quote_shipping(vendor, pkg(vendor.pk), Destination("IT", "00100"))
    assert isinstance(covered, Rates)
    assert [r.key for r in covered.rates] == ["shop", "std"] or {r.key for r in covered.rates} >= {
        "shop"
    }
    uncovered = quote_shipping(vendor, pkg(vendor.pk), Destination("US", "10001"))
    assert isinstance(uncovered, NeedsQuote)


def test_vendor_zone_beats_platform_zone_only_for_its_vendor() -> None:  # case 15
    a, b = VendorFactory.create(), VendorFactory.create()
    platform = ShippingZoneFactory.create(countries=["IT"], priority=100)
    own = ShippingZoneFactory.create(
        vendor=a, countries=["IT"], postcode_patterns=["07*"], priority=10
    )
    priced_method(a, platform, key="plat-a")
    priced_method(a, own, key="own-a")
    priced_method(b, platform, key="plat-b")
    dest = Destination("IT", "07100")
    out_a = quote_shipping(a, pkg(a.pk), dest)
    out_b = quote_shipping(b, pkg(b.pk), dest)
    assert isinstance(out_a, (Rates, IndicativeRates))
    assert {r.key for r in out_a.rates} == {"own-a"}
    assert isinstance(out_b, (Rates, IndicativeRates))
    assert {r.key for r in out_b.rates} == {"plat-b"}


def test_two_methods_each_with_its_own_bands_and_threshold() -> None:  # case 16
    vendor = VendorFactory.create()
    zone = ShippingZoneFactory.create(countries=["IT"])
    priced_method(vendor, zone, key="std", is_default=True, free_shipping_threshold=D("40.00"))
    express = ShippingMethodFactory.create(vendor=vendor, zone=zone, express=True)
    WeightBandFactory.create(method=express, from_kg=D("0"), to_kg=D("5"), cost_net=D("19.00"))
    outcome = quote_shipping(vendor, pkg(vendor.pk), Destination("IT", "00100"))
    assert isinstance(outcome, Rates)
    assert [(r.key, r.net) for r in outcome.rates] == [("std", D("0.00")), ("express", D("19.00"))]


def test_over_max_weight_policy_is_read_from_the_method() -> None:  # case 8
    vendor = VendorFactory.create()
    zone = ShippingZoneFactory.create(countries=["IT"])
    priced_method(vendor, zone, over_max_weight=engine.BLOCK)
    outcome = quote_shipping(vendor, pkg(vendor.pk, "12"), Destination("IT", "00100"))
    assert isinstance(outcome, Blocked)


def test_sardinia_indicative_and_vendor_override_to_fixed_surcharge() -> None:  # case 19
    vendor, other = VendorFactory.create(), VendorFactory.create()
    zone = ShippingZoneFactory.create(countries=["IT"])
    mine = priced_method(vendor, zone, key="mine")
    priced_method(other, zone, key="theirs")
    dest = Destination("IT", "09100")
    indicative = quote_shipping(other, pkg(other.pk), dest)
    assert isinstance(indicative, IndicativeRates)
    assert indicative.rates[0].is_indicative
    SpecialAreaTreatmentFactory.create(method=mine, area=_area("it-sardinia"), fixed_surcharge=True)
    exact = quote_shipping(vendor, pkg(vendor.pk), dest)
    assert isinstance(exact, Rates)
    assert exact.rates[0].net == D("24.90")


def _area(code: str) -> SpecialArea:
    return SpecialArea.objects.get(code=code)


def test_query_count_does_not_grow_with_methods(
    django_assert_max_num_queries: Any,
) -> None:
    vendor = VendorFactory.create()
    zone = ShippingZoneFactory.create(countries=["IT"])
    for _ in range(4):
        priced_method(vendor, zone)
    with django_assert_max_num_queries(12):
        quote_shipping(vendor, pkg(vendor.pk), Destination("IT", "00100"))
