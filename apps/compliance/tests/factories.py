"""factory_boy factories for compliance (CMP.3).

Names, addresses and contacts are synthetic (example.* domains), never real manufacturers.
"""

import datetime as dt
import uuid

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.catalog.tests.factories import ProductFactory
from apps.compliance.models import (
    CountryWarningLanguages,
    GpsrInfo,
    GpsrSafetyText,
    Notice,
    ProductRestriction,
    SafetyCase,
    StatementOfReasons,
    TransparencySubmission,
    VendorGpsrDefaults,
)
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


class CountryWarningLanguagesFactory(DjangoModelFactory[CountryWarningLanguages]):
    """A country outside the seeded EU and EEA rows, with one accepted language."""

    class Meta:
        model = CountryWarningLanguages

    country = factory.Iterator(["CH", "GB", "TR", "AL", "RS", "UA", "MD", "ME", "MK", "BA"])
    languages = factory.LazyFunction(lambda: ["de"])


class NoticeFactory(DjangoModelFactory[Notice]):
    """A received notice about a product, with a notifier (synthetic example.* data)."""

    class Meta:
        model = Notice

    target_kind = Notice.TargetKind.PRODUCT
    url = factory.Sequence(lambda n: f"https://shop.example.com/products/item-{n}/")
    category = Notice.Category.GENERAL
    explanation = "The item is counterfeit."
    notifier_name = "Ada Reporter"
    notifier_email = factory.Sequence(lambda n: f"reporter{n}@example.org")
    notifier_language = "en"
    status = Notice.Status.RECEIVED
    good_faith = True
    decided_at = factory.LazyAttribute(
        lambda n: timezone.now() if n.status in ("action_taken", "no_action") else None
    )


class StatementOfReasonsFactory(DjangoModelFactory[StatementOfReasons]):
    """A sent statement for a removed product of a vendor, on the platform's own initiative."""

    class Meta:
        model = StatementOfReasons

    vendor = factory.SubFactory(VendorFactory)
    target_kind = StatementOfReasons.TargetKind.PRODUCT
    target_ref = factory.LazyFunction(uuid.uuid4)
    restriction = "remove"
    scope = "The product is no longer shown or sold"
    facts = "The listing offers a counterfeit item."
    ground_kind = "illegal_content"
    ground = "Trade mark infringement"
    transparency_explanation = "Counterfeit item offered."
    language = "en"
    sent_at = factory.LazyFunction(timezone.now)


class TransparencySubmissionFactory(DjangoModelFactory[TransparencySubmission]):
    """A pending article 24(5) record of a statement."""

    class Meta:
        model = TransparencySubmission

    statement = factory.SubFactory(StatementOfReasonsFactory)
    payload = factory.LazyFunction(lambda: {"puid": "x"})


class ProductRestrictionFactory(DjangoModelFactory[ProductRestriction]):
    """An active hold on a product."""

    class Meta:
        model = ProductRestriction

    product = factory.SubFactory(ProductFactory)
    statement = factory.SubFactory(StatementOfReasonsFactory)
