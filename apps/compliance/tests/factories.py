"""factory_boy factories for compliance (CMP.3).

Names, addresses and contacts are synthetic (example.* domains), never real manufacturers.
"""

import datetime as dt

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.catalog.tests.factories import ProductFactory
from apps.compliance.models import GpsrInfo, GpsrSafetyText, SafetyCase, VendorGpsrDefaults
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


class GpsrSafetyTextFactory(DjangoModelFactory[GpsrSafetyText]):
    """English warnings for a product."""

    class Meta:
        model = GpsrSafetyText

    product = factory.SubFactory(ProductFactory)
    language = "en"
    warnings = "Keep out of the reach of children."


class SafetyCaseFactory(DjangoModelFactory[SafetyCase]):
    """An open safety notice about a vendor's product, due in three working days."""

    class Meta:
        model = SafetyCase

    product = factory.SubFactory(ProductFactory)
    vendor = factory.SelfAttribute("product.vendor")
    product_sku = factory.SelfAttribute("product.sku")
    kind = SafetyCase.Kind.SAFETY_NOTICE
    received_at = factory.LazyFunction(timezone.now)
    due_on = factory.LazyFunction(lambda: timezone.localdate() + dt.timedelta(days=3))
    summary = "A buyer reports a safety problem."
