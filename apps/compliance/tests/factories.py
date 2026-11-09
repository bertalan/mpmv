"""factory_boy factories for compliance (CMP.3).

Names, addresses and contacts are synthetic (example.* domains), never real manufacturers.
"""

import factory
from factory.django import DjangoModelFactory

from apps.catalog.tests.factories import ProductFactory
from apps.compliance.models import GpsrInfo, VendorGpsrDefaults
from apps.vendors.tests.factories import VendorFactory


class VendorGpsrDefaultsFactory(DjangoModelFactory[VendorGpsrDefaults]):
    """A vendor that is its own manufacturer, in Italy."""

    class Meta:
        model = VendorGpsrDefaults

    vendor = factory.SubFactory(VendorFactory)
    manufacturer_name = factory.Sequence(lambda n: f"Maker {n} S.r.l.")
    manufacturer_address = "Via Esempio 1, 00100 Roma"
    manufacturer_contact = factory.Sequence(lambda n: f"safety{n}@maker.example")
    manufacturer_country = "IT"


class GpsrInfoFactory(DjangoModelFactory[GpsrInfo]):
    """A product's own GPSR data: only a model number; the vendor defaults fill the rest."""

    class Meta:
        model = GpsrInfo

    product = factory.SubFactory(ProductFactory)
    model_number = factory.Sequence(lambda n: f"MODEL-{n:04d}")
