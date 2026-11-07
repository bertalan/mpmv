"""SHP.1 and SHP.12: shipping models and their database constraints (spec 06 §6.2), on real
PostgreSQL."""

import importlib
from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.core.countries import EU_MEMBER_STATES, ISO_3166_ALPHA2
from apps.core.translations import PUBLISHED, Resolved
from apps.shipping import engine
from apps.shipping.models import (
    Carrier,
    CountryGroup,
    ShippingConfigVersion,
    ShippingMethod,
    ShippingZone,
    WeightBand,
)
from apps.shipping.tests.factories import (
    CarrierFactory,
    CarrierTranslationFactory,
    CountryGroupFactory,
    CountryGroupTranslationFactory,
    PickupOptionFactory,
    PickupOptionTranslationFactory,
    ShippingConfigVersionFactory,
    ShippingMethodFactory,
    ShippingMethodTranslationFactory,
    ShippingZoneFactory,
    WeightBandFactory,
)
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

CONTINENTS = (
    "africa",
    "northern-america",
    "central-america-caribbean",
    "south-america",
    "asia",
    "europe",
    "oceania",
)


def kg(value: str) -> Decimal:
    return Decimal(value)


def refused() -> pytest.RaisesExc[IntegrityError]:
    return pytest.raises(IntegrityError)


def label_of(resolved: Resolved[Any], field: str = "label") -> str:
    assert resolved.translation is not None
    text: str = getattr(resolved.translation, field)
    return text


@pytest.fixture
def seeded_groups() -> None:
    """Transactional tests flush the seeded groups; re-run the idempotent seeding migration."""
    importlib.import_module("apps.shipping.migrations.0005_seed_country_groups").seed(apps, None)


def members(code: str) -> set[str]:
    return set(CountryGroup.objects.get(code=code).countries)


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
    zone = ShippingZoneFactory.create(world=True)
    zone.full_clean()
    assert ShippingZone.objects.get(pk=zone.pk).countries == ["*"]


def test_zone_must_list_a_country_or_a_country_group() -> None:
    with refused(), transaction.atomic():
        ShippingZoneFactory.create(countries=[])
    group = CountryGroupFactory.create()
    zone = ShippingZoneFactory.create(countries=[], country_groups=[group.code])
    zone.full_clean()


def test_rest_of_the_world_marker_cannot_be_mixed_with_countries_or_groups() -> None:
    with refused(), transaction.atomic():
        ShippingZoneFactory.create(countries=["IT", "*"])
    group = CountryGroupFactory.create()
    with refused(), transaction.atomic():
        ShippingZoneFactory.create(countries=["*"], country_groups=[group.code])


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


def test_zone_postcode_patterns_and_provinces_are_normalized_on_validation() -> None:
    zone = ShippingZoneFactory.build(
        postcode_patterns=[" bt* ", "07000 .. 09999", "9000-001"], states=[" ss ", "ca"]
    )
    zone.full_clean()
    assert zone.postcode_patterns == ["BT*", "07000..09999", "9000-001"]
    assert zone.states == ["SS", "CA"]


@pytest.mark.parametrize("pattern", ["70000-74999", "74999..70000", "B*T"])
def test_zone_with_a_malformed_postcode_pattern_fails_validation(pattern: str) -> None:
    zone = ShippingZoneFactory.build(postcode_patterns=[pattern])
    with pytest.raises(ValidationError) as excinfo:
        zone.full_clean()
    assert "postcode_patterns" in excinfo.value.message_dict


def test_platform_zone_codes_are_unique() -> None:
    ShippingZoneFactory.create(code="italy")
    with refused(), transaction.atomic():
        ShippingZoneFactory.create(code="italy")


def test_vendor_zone_codes_are_unique_per_vendor_only() -> None:
    ShippingZoneFactory.create(code="sardinia")  # a platform zone
    mine = ShippingZoneFactory.create(vendor_zone=True, code="sardinia")
    ShippingZoneFactory.create(vendor_zone=True, code="sardinia")  # another vendor
    with refused(), transaction.atomic():
        ShippingZoneFactory.create(vendor=mine.vendor, code="sardinia")


def test_vendor_zones_are_visible_only_to_their_vendor() -> None:
    member = VendorMemberFactory.create()
    mine = ShippingZoneFactory.create(vendor=member.vendor)
    theirs = ShippingZoneFactory.create(vendor_zone=True)
    ShippingZoneFactory.create()  # platform zone: not owned by any vendor
    assert list(ShippingZone.objects.for_member(member.user)) == [mine]
    assert theirs not in ShippingZone.objects.for_vendor(member.vendor)


def test_zone_country_groups_must_exist_and_are_normalized() -> None:
    group = CountryGroupFactory.create(code="group-x")
    zone = ShippingZoneFactory.build(countries=[], country_groups=[" GROUP-X "])
    zone.full_clean()
    assert zone.country_groups == [group.code]
    unknown = ShippingZoneFactory.build(country_groups=["no-such-group"])
    with pytest.raises(ValidationError) as excinfo:
        unknown.full_clean()
    assert "country_groups" in excinfo.value.message_dict


def test_the_effective_countries_of_a_zone_include_its_groups() -> None:
    group = CountryGroupFactory.create(countries=["DE", "AT"])
    zone = ShippingZoneFactory.create(countries=["IT"], country_groups=[group.code])
    assert zone.effective_countries() == frozenset({"IT", "DE", "AT"})


def test_a_zone_used_by_another_vendors_method_cannot_become_a_vendor_zone() -> None:
    method = ShippingMethodFactory.create()
    zone = method.zone
    zone.vendor = VendorFactory.create()
    with pytest.raises(ValidationError) as excinfo:
        zone.full_clean()
    assert "vendor" in excinfo.value.message_dict


# --- CountryGroup


def test_country_group_countries_are_normalized_and_named_by_translation() -> None:
    group = CountryGroupFactory.build(code="dach", countries=["de", " at ", "ch"])
    group.full_clean()
    group.save()
    assert group.countries == ["DE", "AT", "CH"]
    CountryGroupTranslationFactory.create(
        country_group=group, language="en", name="DACH", status=PUBLISHED
    )
    assert label_of(group.translation("it"), "name") == "DACH"
    assert str(group) == "dach"


def test_country_group_needs_countries_and_never_the_world_marker() -> None:
    with refused(), transaction.atomic():
        CountryGroupFactory.create(countries=[])
    with refused(), transaction.atomic():
        CountryGroupFactory.create(countries=["*"])


def test_country_group_code_is_unique() -> None:
    CountryGroupFactory.create(code="dach")
    with refused(), transaction.atomic():
        CountryGroupFactory.create(code="dach")


def test_a_country_group_in_use_keeps_its_code() -> None:
    group = CountryGroupFactory.create(code="dach")
    ShippingZoneFactory.create(country_groups=["dach"])
    group.code = "renamed"
    with pytest.raises(ValidationError) as excinfo:
        group.full_clean()
    assert "code" in excinfo.value.message_dict


def test_the_eu_group_is_seeded_with_the_member_states(seeded_groups: None) -> None:
    assert members("eu") == set(EU_MEMBER_STATES)
    assert label_of(CountryGroup.objects.get(code="eu").translation("en"), "name") == (
        "European Union"
    )
    assert label_of(CountryGroup.objects.get(code="eu").translation("it"), "name") == (
        "Unione europea"
    )


def test_the_seeded_continents_cover_every_country(seeded_groups: None) -> None:
    # UN M49 regions; Antarctica belongs to none. Europe also lists Cyprus (M49: Western Asia)
    # so that every EU member state is in Europe.
    covered: list[str] = [c for code in CONTINENTS for c in members(code)]
    assert set(covered) == ISO_3166_ALPHA2 - {"AQ"}
    assert sorted(c for c in set(covered) if covered.count(c) > 1) == ["CY"]
    assert set(EU_MEMBER_STATES) <= members("europe")
    americas = ("northern-america", "central-america-caribbean", "south-america")
    assert members("americas") == set().union(*(members(code) for code in americas))


# --- ShippingMethod


def test_a_vendor_offers_two_methods_to_the_same_zone_with_different_bands() -> None:
    # SHP.12 "Done when": several methods per vendor and zone, each with its own bands.
    standard = ShippingMethodFactory.create(default=True, key="standard")
    express = ShippingMethodFactory.create(
        express=True, vendor=standard.vendor, zone=standard.zone, carrier=CarrierFactory.create()
    )
    WeightBandFactory.create(method=standard, from_kg=kg("0"), to_kg=kg("5"), cost_net="9.90")
    WeightBandFactory.create(method=standard, from_kg=kg("5"), to_kg=None, cost_net="14.90")
    WeightBandFactory.create(method=express, from_kg=kg("0"), to_kg=kg("3"), cost_net="19.90")

    methods = ShippingMethod.objects.filter(vendor=standard.vendor, zone=standard.zone)
    assert methods.count() == 2
    assert [(b.from_kg, b.to_kg) for b in standard.bands.all()] == [(0, 5), (5, None)]
    assert [(b.from_kg, b.to_kg, b.cost_net) for b in express.bands.all()] == [
        (0, 3, Decimal("19.90"))
    ]
    assert methods.get(is_default=True) == standard


def test_only_one_default_method_per_vendor_and_zone() -> None:
    default = ShippingMethodFactory.create(default=True)
    ShippingMethodFactory.create(vendor=default.vendor, zone=default.zone)  # not default: fine
    ShippingMethodFactory.create(default=True, vendor=default.vendor)  # another zone: fine
    ShippingMethodFactory.create(default=True, zone=default.zone)  # another vendor: fine
    with refused(), transaction.atomic():
        ShippingMethodFactory.create(default=True, vendor=default.vendor, zone=default.zone)


def test_method_keys_are_unique_per_vendor_and_zone() -> None:
    method = ShippingMethodFactory.create(key="standard")
    ShippingMethodFactory.create(key="standard", vendor=method.vendor)  # another zone
    with refused(), transaction.atomic():
        ShippingMethodFactory.create(key="standard", vendor=method.vendor, zone=method.zone)


def test_the_vat_treatment_is_stored_per_method() -> None:
    # SHP.12 "Done when": stored only; TAX.4 applies it (D-03 is open).
    method = ShippingMethodFactory.create()
    assert method.tax_treatment == engine.FOLLOWS_GOODS
    exempt = ShippingMethodFactory.create(tax_treatment=engine.EXEMPT, vendor=method.vendor)
    exempt.refresh_from_db()
    assert exempt.tax_treatment == "exempt"
    with refused(), transaction.atomic():
        ShippingMethodFactory.create(tax_treatment="force_vat")


def test_method_service_level_is_one_of_the_choices() -> None:
    assert ShippingMethodFactory.create(express=True).service_level == "express"
    with refused(), transaction.atomic():
        ShippingMethodFactory.create(service_level="overnight")


def test_method_delivery_time_is_ordered_and_optional() -> None:
    method = ShippingMethodFactory.create(min_delivery_days=None, max_delivery_days=None)
    assert method.min_delivery_days is None
    ShippingMethodFactory.create(min_delivery_days=3, max_delivery_days=3)
    with refused(), transaction.atomic():
        ShippingMethodFactory.create(min_delivery_days=5, max_delivery_days=2)


def test_method_free_shipping_threshold_is_net_money_and_not_negative() -> None:
    method = ShippingMethodFactory.create(free_shipping_threshold=Decimal("99"))
    method.refresh_from_db()
    assert method.free_shipping_threshold == Decimal("99.00")
    assert ShippingMethodFactory.create().free_shipping_threshold is None
    with refused(), transaction.atomic():
        ShippingMethodFactory.create(free_shipping_threshold=Decimal("-1.00"))


def test_method_name_is_translated() -> None:
    translation = ShippingMethodTranslationFactory.create(
        name="Express courier", language="en", status=PUBLISHED
    )
    assert label_of(translation.method.translation("it"), "name") == "Express courier"
    with refused(), transaction.atomic():
        ShippingMethodTranslationFactory.create(method=translation.method, language="en")


def test_a_method_uses_platform_zones_or_its_vendors_own_zones() -> None:
    own_zone = ShippingZoneFactory.create(vendor_zone=True)
    vendor, carrier = own_zone.vendor, CarrierFactory.create()
    ShippingMethodFactory.build(vendor=vendor, zone=own_zone, carrier=carrier).full_clean()
    platform_zone = ShippingZoneFactory.create()
    ShippingMethodFactory.build(vendor=vendor, zone=platform_zone, carrier=carrier).full_clean()
    foreign = ShippingMethodFactory.build(  # another vendor's zone
        vendor=VendorFactory.create(), zone=own_zone, carrier=carrier
    )
    with pytest.raises(ValidationError) as excinfo:
        foreign.full_clean()
    assert "zone" in excinfo.value.message_dict


def test_over_max_weight_policy_is_unset_until_d06_is_decided() -> None:
    method = ShippingMethodFactory.create()
    assert method.over_max_weight == engine.PLATFORM_DEFAULT == ""


@pytest.mark.parametrize("policy", ["last_band", "quote", "block"])
def test_over_max_weight_accepts_the_listed_policies(policy: str) -> None:
    assert ShippingMethodFactory.create(over_max_weight=policy).over_max_weight == policy


def test_over_max_weight_refuses_other_values() -> None:
    with refused(), transaction.atomic():
        ShippingMethodFactory.create(over_max_weight="free")


def test_zones_and_carriers_in_use_cannot_be_deleted() -> None:
    method = ShippingMethodFactory.create()
    with pytest.raises(ProtectedError):
        method.zone.delete()
    with pytest.raises(ProtectedError):
        method.carrier.delete()


def test_deleting_a_method_deletes_its_bands() -> None:
    band = WeightBandFactory.create()
    band.method.delete()
    assert not WeightBand.objects.exists()


# --- WeightBand: no overlaps within a method (exclusion constraint), right-inclusive bounds


def test_adjacent_bands_are_allowed() -> None:
    method = ShippingMethodFactory.create()
    WeightBandFactory.create(method=method, from_kg=kg("0"), to_kg=kg("5"))
    WeightBandFactory.create(method=method, from_kg=kg("5"), to_kg=kg("10"))
    WeightBandFactory.create(method=method, from_kg=kg("10"), to_kg=None)
    assert method.bands.count() == 3


@pytest.mark.parametrize(
    ("from_kg", "to_kg"),
    [("4.999", "10"), ("1", "2"), ("0", "5"), ("3", None)],
    ids=["straddles", "inside", "same", "unlimited"],
)
def test_overlapping_bands_of_the_same_method_are_refused(from_kg: str, to_kg: str | None) -> None:
    method = ShippingMethodFactory.create()
    WeightBandFactory.create(method=method, from_kg=kg("0"), to_kg=kg("5"))
    with refused(), transaction.atomic():
        WeightBandFactory.create(
            method=method, from_kg=kg(from_kg), to_kg=None if to_kg is None else kg(to_kg)
        )


def test_only_one_unlimited_band_per_method() -> None:
    method = ShippingMethodFactory.create()
    WeightBandFactory.create(method=method, from_kg=kg("10"), to_kg=None)
    with refused(), transaction.atomic():
        WeightBandFactory.create(method=method, from_kg=kg("20"), to_kg=None)


def test_bands_of_different_methods_of_one_vendor_and_zone_may_overlap() -> None:
    standard = ShippingMethodFactory.create()
    express = ShippingMethodFactory.create(vendor=standard.vendor, zone=standard.zone)
    WeightBandFactory.create(method=standard, from_kg=kg("0"), to_kg=kg("5"))
    WeightBandFactory.create(method=express, from_kg=kg("0"), to_kg=kg("5"))
    assert WeightBand.objects.count() == 2


def test_overlapping_band_fails_model_validation_before_the_database() -> None:
    method = ShippingMethodFactory.create()
    WeightBandFactory.create(method=method, from_kg=kg("0"), to_kg=kg("5"))
    band = WeightBandFactory.build(method=method, from_kg=kg("2"), to_kg=kg("8"))
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
