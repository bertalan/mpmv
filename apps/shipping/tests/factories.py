"""factory_boy factories for shipping (SHP.1). Synthetic values only.

Codes and labels are generic ("carrier-0", "Zone 0"); vendors come from the vendors factories
(pseudonyms). Weight bands default to 0-5 kg; pass `from_kg`/`to_kg` explicitly in tests that
assert on bounds.
"""

from decimal import Decimal

import factory
from factory.django import DjangoModelFactory

from apps.shipping.models import (
    Carrier,
    CarrierTranslation,
    PickupOption,
    PickupOptionTranslation,
    ShippingConfigVersion,
    ShippingZone,
    VendorZoneRate,
    WeightBand,
)
from apps.vendors.tests.factories import VendorFactory


class CarrierFactory(DjangoModelFactory[Carrier]):
    class Meta:
        model = Carrier

    code = factory.Sequence(lambda n: f"carrier-{n}")
    is_active = True
    tracking_url_template = "https://tracking.example.com/{tracking_number}"
    supports_api = False


class CarrierTranslationFactory(DjangoModelFactory[CarrierTranslation]):
    class Meta:
        model = CarrierTranslation

    carrier = factory.SubFactory(CarrierFactory)
    language = "en"
    label = factory.Sequence(lambda n: f"Carrier {n}")


class ShippingZoneFactory(DjangoModelFactory[ShippingZone]):
    """A country-only zone for Italy; pass `countries`, `postcode_patterns` or `states`."""

    class Meta:
        model = ShippingZone

    code = factory.Sequence(lambda n: f"zone-{n}")
    name = factory.Sequence(lambda n: f"Zone {n}")
    priority = 100
    countries = factory.LazyFunction(lambda: ["IT"])
    postcode_patterns = factory.LazyFunction(list)
    states = factory.LazyFunction(list)


class VendorZoneRateFactory(DjangoModelFactory[VendorZoneRate]):
    class Meta:
        model = VendorZoneRate

    vendor = factory.SubFactory(VendorFactory)
    zone = factory.SubFactory(ShippingZoneFactory)
    carrier = factory.SubFactory(CarrierFactory)


class WeightBandFactory(DjangoModelFactory[WeightBand]):
    class Meta:
        model = WeightBand

    rate = factory.SubFactory(VendorZoneRateFactory)
    from_kg = Decimal("0.000")
    to_kg = Decimal("5.000")
    cost_net = Decimal("9.90")
    surcharge_net = Decimal("0.00")


class PickupOptionFactory(DjangoModelFactory[PickupOption]):
    class Meta:
        model = PickupOption

    vendor = factory.SubFactory(VendorFactory)
    key = factory.Sequence(lambda n: f"pickup-{n}")
    cost_net = Decimal("0.00")
    address = factory.Faker("address")


class PickupOptionTranslationFactory(DjangoModelFactory[PickupOptionTranslation]):
    class Meta:
        model = PickupOptionTranslation

    pickup_option = factory.SubFactory(PickupOptionFactory)
    language = "en"
    label = "Pick up at the shop"


class ShippingConfigVersionFactory(DjangoModelFactory[ShippingConfigVersion]):
    class Meta:
        model = ShippingConfigVersion

    snapshot = factory.LazyFunction(lambda: {"zones": [], "rates": []})
    created_by = None
    note = factory.Faker("sentence")
