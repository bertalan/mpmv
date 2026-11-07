"""factory_boy factories for vendors (ARC.12).

Names are pseudonyms ("Vendor A", "Vendor B", ...) and texts come from the seeded Faker: never
real vendor data.
"""

import string
from collections.abc import Iterable
from typing import Any

import factory
from django.utils.text import slugify
from factory.django import DjangoModelFactory

from apps.accounts.tests.factories import UserFactory
from apps.vendors.models import Vendor, VendorMember, VendorTranslation


def pseudonym(n: int) -> str:
    """0 → "A", 25 → "Z", 26 → "A2", ..."""
    letter = string.ascii_uppercase[n % 26]
    return letter if n < 26 else f"{letter}{n // 26 + 1}"


class VendorFactory(DjangoModelFactory[Vendor]):
    """An applicant vendor in Italy; the slug comes from the display name, as in production.

    `languages=["en", "it"]` also creates one translation per language.
    """

    class Meta:
        model = Vendor
        skip_postgeneration_save = True

    display_name = factory.Sequence(lambda n: f"Vendor {pseudonym(n)}")
    legal_name = factory.LazyAttribute(lambda o: f"{o.display_name} S.r.l.")
    country = "IT"
    vat_number = factory.Sequence(lambda n: f"IT{n:011d}")
    contact_email = factory.LazyAttribute(lambda o: f"{slugify(o.display_name)}@example.com")
    status = Vendor.Status.APPLICANT

    @factory.post_generation
    def languages(
        obj: Vendor, create: bool, extracted: Iterable[str] | None, **kwargs: Any
    ) -> None:
        if create and extracted:
            for language in extracted:
                VendorTranslationFactory.create(vendor=obj, language=language)


class VendorTranslationFactory(DjangoModelFactory[VendorTranslation]):
    class Meta:
        model = VendorTranslation

    vendor = factory.SubFactory(VendorFactory)
    language = "en"
    tagline = factory.Faker("catch_phrase")
    description = factory.Faker("paragraph")
    shipping_policy = factory.Faker("sentence")
    refund_policy = factory.Faker("sentence")
    cancellation_policy = factory.Faker("sentence")


class VendorMemberFactory(DjangoModelFactory[VendorMember]):
    """An owner of a new vendor; pass `vendor=` to add members to the same vendor.

    Factories write the row directly: tests of the last-owner rule go through the services.
    """

    class Meta:
        model = VendorMember

    vendor = factory.SubFactory(VendorFactory)
    user = factory.SubFactory(UserFactory)
    role = VendorMember.Role.OWNER
