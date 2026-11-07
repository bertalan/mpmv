"""factory_boy factories for shipping (SHP.1, SHP.12). Synthetic values only.

Codes and labels are generic ("carrier-0", "Zone 0", "group-0"); vendors come from the vendors
factories (pseudonyms). Weight bands default to 0-5 kg; pass `from_kg`/`to_kg` explicitly in
tests that assert on bounds. Zones are platform zones unless a `vendor` is given (or the
`vendor_zone` trait); methods are standard, non-default ones unless a trait says otherwise.
"""

from decimal import Decimal

import factory
from factory.django import DjangoModelFactory

from apps.shipping import engine
from apps.shipping.config_format import empty_document
from apps.shipping.models import (
    Carrier,
    CarrierTranslation,
    CountryGroup,
    CountryGroupTranslation,
    PickupOption,
    PickupOptionTranslation,
    ShippingConfigVersion,
    ShippingMethod,
    ShippingMethodTranslation,
    ShippingTemplate,
    ShippingZone,
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


class CountryGroupFactory(DjangoModelFactory[CountryGroup]):
    """A small group of two countries; codes never collide with the seeded groups."""

    class Meta:
        model = CountryGroup

    code = factory.Sequence(lambda n: f"group-{n}")
    countries = factory.LazyFunction(lambda: ["IT", "FR"])


class CountryGroupTranslationFactory(DjangoModelFactory[CountryGroupTranslation]):
    class Meta:
        model = CountryGroupTranslation

    country_group = factory.SubFactory(CountryGroupFactory)
    language = "en"
    name = factory.Sequence(lambda n: f"Group {n}")


class ShippingZoneFactory(DjangoModelFactory[ShippingZone]):
    """A country-only platform zone for Italy; pass `countries`, `country_groups`,
    `postcode_patterns` or `states`. Traits: `vendor_zone` (a zone of a new vendor), `world`."""

    class Meta:
        model = ShippingZone

    class Params:
        vendor_zone = factory.Trait(vendor=factory.SubFactory(VendorFactory))
        world = factory.Trait(countries=factory.LazyFunction(lambda: [engine.WORLD]), priority=999)

    vendor = None
    code = factory.Sequence(lambda n: f"zone-{n}")
    name = factory.Sequence(lambda n: f"Zone {n}")
    priority = 100
    countries = factory.LazyFunction(lambda: ["IT"])
    country_groups = factory.LazyFunction(list)
    postcode_patterns = factory.LazyFunction(list)
    states = factory.LazyFunction(list)


class ShippingMethodFactory(DjangoModelFactory[ShippingMethod]):
    """A standard method of a vendor to a platform zone. Traits: `default` (the vendor's
    default method for the zone), `express` (next-day to 2-day delivery)."""

    class Meta:
        model = ShippingMethod

    class Params:
        default = factory.Trait(is_default=True)
        express = factory.Trait(
            key="express",
            service_level=engine.EXPRESS,
            min_delivery_days=1,
            max_delivery_days=2,
        )

    vendor = factory.SubFactory(VendorFactory)
    zone = factory.SubFactory(ShippingZoneFactory)
    carrier = factory.SubFactory(CarrierFactory)
    key = factory.Sequence(lambda n: f"method-{n}")
    service_level = engine.STANDARD
    min_delivery_days = 2
    max_delivery_days = 5
    is_default = False
    free_shipping_threshold = None


class ShippingMethodTranslationFactory(DjangoModelFactory[ShippingMethodTranslation]):
    class Meta:
        model = ShippingMethodTranslation

    method = factory.SubFactory(ShippingMethodFactory)
    language = "en"
    name = "Standard courier"


class WeightBandFactory(DjangoModelFactory[WeightBand]):
    class Meta:
        model = WeightBand

    method = factory.SubFactory(ShippingMethodFactory)
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


class ShippingTemplateFactory(DjangoModelFactory[ShippingTemplate]):
    """An empty, non-standard template; pass a `document` of the configuration file format."""

    class Meta:
        model = ShippingTemplate

    code = factory.Sequence(lambda n: f"template-{n}")
    name = factory.Sequence(lambda n: f"Template {n}")
    is_standard = False
    document = factory.LazyFunction(empty_document)


class ShippingConfigVersionFactory(DjangoModelFactory[ShippingConfigVersion]):
    class Meta:
        model = ShippingConfigVersion

    snapshot = factory.LazyFunction(lambda: {"zones": [], "methods": []})
    created_by = None
    note = factory.Faker("sentence")
