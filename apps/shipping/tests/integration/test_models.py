"""SHP.1: shipping models and their database constraints (spec 06 §6.2), on real PostgreSQL."""

from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.core.translations import PUBLISHED, Resolved
from apps.shipping.models import (
    Carrier,
    ShippingConfigVersion,
    ShippingZone,
    VendorZoneRate,
    WeightBand,
)
from apps.shipping.tests.factories import (
    CarrierFactory,
    CarrierTranslationFactory,
    PickupOptionFactory,
    PickupOptionTranslationFactory,
    ShippingConfigVersionFactory,
    ShippingZoneFactory,
    VendorZoneRateFactory,
    WeightBandFactory,
)
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db


def kg(value: str) -> Decimal:
    return Decimal(value)


def refused() -> pytest.RaisesExc[IntegrityError]:
    return pytest.raises(IntegrityError)


def label_of(resolved: Resolved[Any]) -> str:
    assert resolved.translation is not None
    label: str = resolved.translation.label
    return label


# --- Carrier


def test_carrier_defaults_and_translated_label() -> None:
    carrier = Carrier.objects.create(code="main")  # model defaults under test
    assert carrier.is_active is True
    assert carrier.supports_api is False
    assert carrier.tracking_url_template == ""
    CarrierTranslationFactory.create(
        carrier=carrier, language="en", label="Main courier", status=PUBLISHED
    )
    CarrierTranslationFactory.create(
        carrier=carrier, language="it", label="Corriere", status=PUBLISHED
    )
    assert label_of(carrier.translation("it")) == "Corriere"
    fallback = carrier.translation("de")
    assert fallback.is_fallback  # explicit EN fallback
    assert label_of(fallback) == "Main courier"
    assert str(carrier) == "main"


def test_carrier_code_is_unique() -> None:
    CarrierFactory.create(code="main")
    with refused(), transaction.atomic():
        CarrierFactory.create(code="main")


def test_carrier_has_one_translation_per_language() -> None:
    translation = CarrierTranslationFactory.create(language="en")
    with refused(), transaction.atomic():
        CarrierTranslationFactory.create(carrier=translation.carrier, language="en")


# --- ShippingZone


def test_zone_countries_are_normalized_on_validation() -> None:
    zone = ShippingZoneFactory.build(countries=["el", "uk", " it "])
    zone.full_clean()
    assert zone.countries == ["GR", "GB", "IT"]


def test_zone_with_an_unknown_country_fails_validation() -> None:
    zone = ShippingZoneFactory.build(countries=["IT", "XX"])
    with pytest.raises(ValidationError) as excinfo:
        zone.full_clean()
    assert "countries" in excinfo.value.message_dict


def test_rest_of_the_world_zone_is_accepted() -> None:
    zone = ShippingZoneFactory.create(countries=["*"], priority=999)
    zone.full_clean()
    assert ShippingZone.objects.get(pk=zone.pk).countries == ["*"]


def test_zone_must_list_at_least_one_country() -> None:
    with refused(), transaction.atomic():
        ShippingZoneFactory.create(countries=[])


def test_rest_of_the_world_marker_cannot_be_mixed_with_countries() -> None:
    with refused(), transaction.atomic():
        ShippingZoneFactory.create(countries=["IT", "*"])


@pytest.mark.parametrize("priority", [0, 1000])
def test_zone_priority_is_between_1_and_999(priority: int) -> None:
    with refused(), transaction.atomic():
        ShippingZoneFactory.create(priority=priority)


def test_zone_keeps_postcode_patterns_and_states() -> None:
    zone = ShippingZoneFactory.create(
        countries=["GB"], postcode_patterns=["BT*"], states=[], priority=10
    )
    zone.refresh_from_db()
    assert zone.postcode_patterns == ["BT*"]
    assert ShippingZoneFactory.create().postcode_patterns == []


def test_zone_code_is_unique() -> None:
    ShippingZoneFactory.create(code="italy")
    with refused(), transaction.atomic():
        ShippingZoneFactory.create(code="italy")


# --- VendorZoneRate


def test_a_vendor_cannot_be_on_two_carriers_in_the_same_zone() -> None:
    # Spec 06 §6.3 case 9: a DB constraint, not "the first one wins".
    rate = VendorZoneRateFactory.create()
    with refused(), transaction.atomic():
        VendorZoneRateFactory.create(vendor=rate.vendor, zone=rate.zone)


def test_a_vendor_can_use_different_carriers_in_different_zones() -> None:
    vendor = VendorFactory.create()
    VendorZoneRateFactory.create(vendor=vendor)
    VendorZoneRateFactory.create(vendor=vendor)
    assert VendorZoneRate.objects.filter(vendor=vendor).count() == 2


def test_over_max_weight_policy_is_unset_until_d06_is_decided() -> None:
    rate = VendorZoneRateFactory.create()
    assert rate.over_max_weight is None


@pytest.mark.parametrize("policy", ["last_band", "quote", "block"])
def test_over_max_weight_accepts_the_listed_policies(policy: str) -> None:
    assert VendorZoneRateFactory.create(over_max_weight=policy).over_max_weight == policy


def test_over_max_weight_refuses_other_values() -> None:
    with refused(), transaction.atomic():
        VendorZoneRateFactory.create(over_max_weight="free")


def test_zones_and_carriers_in_use_cannot_be_deleted() -> None:
    rate = VendorZoneRateFactory.create()
    with pytest.raises(ProtectedError):
        rate.zone.delete()
    with pytest.raises(ProtectedError):
        rate.carrier.delete()


def test_deleting_a_rate_deletes_its_bands() -> None:
    band = WeightBandFactory.create()
    band.rate.delete()
    assert not WeightBand.objects.exists()


# --- WeightBand: no overlaps (exclusion constraint), right-inclusive bounds


def test_adjacent_bands_are_allowed() -> None:
    rate = VendorZoneRateFactory.create()
    WeightBandFactory.create(rate=rate, from_kg=kg("0"), to_kg=kg("5"))
    WeightBandFactory.create(rate=rate, from_kg=kg("5"), to_kg=kg("10"))
    WeightBandFactory.create(rate=rate, from_kg=kg("10"), to_kg=None)
    assert rate.bands.count() == 3


@pytest.mark.parametrize(
    ("from_kg", "to_kg"),
    [("4.999", "10"), ("1", "2"), ("0", "5"), ("3", None)],
    ids=["straddles", "inside", "same", "unlimited"],
)
def test_overlapping_bands_of_the_same_rate_are_refused(from_kg: str, to_kg: str | None) -> None:
    rate = VendorZoneRateFactory.create()
    WeightBandFactory.create(rate=rate, from_kg=kg("0"), to_kg=kg("5"))
    with refused(), transaction.atomic():
        WeightBandFactory.create(
            rate=rate, from_kg=kg(from_kg), to_kg=None if to_kg is None else kg(to_kg)
        )


def test_only_one_unlimited_band_per_rate() -> None:
    rate = VendorZoneRateFactory.create()
    WeightBandFactory.create(rate=rate, from_kg=kg("10"), to_kg=None)
    with refused(), transaction.atomic():
        WeightBandFactory.create(rate=rate, from_kg=kg("20"), to_kg=None)


def test_bands_of_different_rates_may_overlap() -> None:
    WeightBandFactory.create(from_kg=kg("0"), to_kg=kg("5"))
    WeightBandFactory.create(from_kg=kg("0"), to_kg=kg("5"))
    assert WeightBand.objects.count() == 2


def test_overlapping_band_fails_model_validation_before_the_database() -> None:
    rate = VendorZoneRateFactory.create()
    WeightBandFactory.create(rate=rate, from_kg=kg("0"), to_kg=kg("5"))
    band = WeightBandFactory.build(rate=rate, from_kg=kg("2"), to_kg=kg("8"))
    with pytest.raises(ValidationError):
        band.full_clean()


@pytest.mark.parametrize(("from_kg", "to_kg"), [("5", "5"), ("5", "2"), ("-1", "2")])
def test_band_bounds_must_be_ordered_and_not_negative(from_kg: str, to_kg: str) -> None:
    with refused(), transaction.atomic():
        WeightBandFactory.create(from_kg=kg(from_kg), to_kg=kg(to_kg))


@pytest.mark.parametrize("field", ["cost_net", "surcharge_net"])
def test_band_amounts_are_not_negative(field: str) -> None:
    with refused(), transaction.atomic():
        WeightBandFactory.create(**{field: Decimal("-0.01")})


def test_band_amounts_are_net_money_and_weights_have_three_decimals() -> None:
    band = WeightBandFactory.create(
        from_kg=kg("0"), to_kg=kg("2.5"), cost_net=Decimal("12.5"), surcharge_net=Decimal("3")
    )
    band.refresh_from_db()
    assert band.to_kg == Decimal("2.500")
    assert band.cost_net == Decimal("12.50")
    assert band.surcharge_net == Decimal("3.00")
    with pytest.raises(TypeError):
        WeightBandFactory.create(cost_net=9.9)  # floats are refused (MoneyField)


# --- PickupOption


def test_pickup_option_has_a_translated_label_and_a_unique_key_per_vendor() -> None:
    pickup = PickupOptionFactory.create(key="shop", cost_net=Decimal("0.00"))
    PickupOptionTranslationFactory.create(pickup_option=pickup, language="en", status=PUBLISHED)
    assert label_of(pickup.translation("it")) == "Pick up at the shop"
    PickupOptionFactory.create(key="shop")  # another vendor may reuse the key
    with refused(), transaction.atomic():
        PickupOptionFactory.create(vendor=pickup.vendor, key="shop")


def test_pickup_cost_is_not_negative() -> None:
    with refused(), transaction.atomic():
        PickupOptionFactory.create(cost_net=Decimal("-1.00"))


# --- ShippingConfigVersion


def test_config_versions_are_history_and_never_edited() -> None:
    version = ShippingConfigVersionFactory.create(snapshot={"zones": [{"code": "italy"}]})
    assert ShippingConfigVersion.objects.get().snapshot == {"zones": [{"code": "italy"}]}
    version.note = "rewritten"
    with pytest.raises(PermissionError):
        version.save()


def test_config_versions_are_listed_newest_first() -> None:
    first = ShippingConfigVersionFactory.create()
    second = ShippingConfigVersionFactory.create()
    assert list(ShippingConfigVersion.objects.all()) == [second, first]
