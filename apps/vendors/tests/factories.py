"""factory_boy factories for vendors (ARC.12).

Names are pseudonyms ("Vendor A", "Vendor B", ...) and texts come from the seeded Faker: never
real vendor data. Legal identifiers (VAT numbers, codici fiscali, IBANs) are synthetic but
checksum-valid, generated at runtime from the factory sequence (VEN.12).
"""

import string
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import factory
from django.utils.text import slugify
from factory.django import DjangoModelFactory

from apps.accounts.tests.factories import UserFactory
from apps.vendors import engine
from apps.vendors.models import LegalFieldRequirement, Vendor, VendorMember, VendorTranslation


def pseudonym(n: int) -> str:
    """0 → "A", 25 → "Z", 26 → "A2", ..."""
    letter = string.ascii_uppercase[n % 26]
    return letter if n < 26 else f"{letter}{n // 26 + 1}"


def italian_vat(n: int) -> str:
    """A checksum-valid, synthetic Italian VAT number (11 digits)."""
    first_ten = f"{n % 10**7:07d}" + f"{n % 100 + 1:03d}"  # serial + office code 001-100
    return first_ten + engine.italian_vat_check_digit(first_ten)


def codice_fiscale(n: int) -> str:
    """A checksum-valid, synthetic personal codice fiscale (16 characters)."""
    letters = string.ascii_uppercase
    surname = "".join(letters[(n // 26**i) % 26] for i in range(3))
    month = "ABCDEHLMPRST"[n % 12]
    first_fifteen = f"{surname}XYZ{n % 100:02d}{month}{n % 28 + 1:02d}Z{n % 1000:03d}"
    return first_fifteen + engine.codice_fiscale_check_char(first_fifteen)


def iban(country: str, bban: str) -> str:
    """A checksum-valid IBAN for a synthetic BBAN."""
    return country + engine.iban_check_digits(country, bban) + bban


def sdi_code(n: int) -> str:
    return f"SDI{n % 10000:04d}"


class VendorFactory(DjangoModelFactory[Vendor]):
    """An applicant vendor in Italy; the slug comes from the display name, as in production.

    `languages=["en", "it"]` also creates one translation per language. Traits fill a complete
    legal identity: `italian_company`, `italian_sole_proprietor`, `eu_company` (Germany),
    `non_eu_company` (Switzerland, with an Italian fiscal representative) and
    `non_eu_without_vat` (United States: a TIN and no VAT number).
    """

    class Meta:
        model = Vendor
        skip_postgeneration_save = True

    class Params:
        italian_company = factory.Trait(
            country="IT",
            legal_form=Vendor.LegalForm.SRL,
            vat_number=factory.Sequence(italian_vat),
            tax_code=factory.SelfAttribute("vat_number"),
            tax_residence_country="IT",
            fiscal_regime=Vendor.FiscalRegime.RF01,
            registered_street="Via Example",
            registered_street_number=factory.Sequence(lambda n: str(n + 1)),
            registered_postal_code="00100",
            registered_city="Rome",
            registered_province="RM",
            registered_country="IT",
            rea_office="RM",
            rea_number=factory.Sequence(lambda n: f"{100000 + n}"),
            share_capital=Decimal("10000.00"),
            sole_shareholder=engine.SEVERAL_SHAREHOLDERS,
            liquidation_status=engine.NOT_IN_LIQUIDATION,
            sdi_recipient_code=factory.Sequence(sdi_code),
            pec=factory.LazyAttribute(lambda o: f"{slugify(o.display_name)}@pec.example.com"),
            iban=factory.Sequence(lambda n: iban("IT", f"X0300003200{n:012d}")),
            bic="EXMPITMMXXX",
            bank_name="Example Bank",
        )
        italian_sole_proprietor = factory.Trait(
            country="IT",
            legal_form=Vendor.LegalForm.SOLE_PROPRIETORSHIP,
            first_name=factory.Faker("first_name"),
            last_name=factory.Faker("last_name"),
            legal_name=factory.LazyAttribute(lambda o: f"{o.first_name} {o.last_name}"),
            vat_number=factory.Sequence(italian_vat),
            tax_code=factory.Sequence(codice_fiscale),
            tax_residence_country="IT",
            fiscal_regime=Vendor.FiscalRegime.RF19,
            registered_street="Via Example",
            registered_street_number=factory.Sequence(lambda n: str(n + 1)),
            registered_postal_code="20100",
            registered_city="Milan",
            registered_province="MI",
            registered_country="IT",
            sdi_recipient_code=engine.SDI_PEC_CODE,
            pec=factory.LazyAttribute(lambda o: f"{slugify(o.display_name)}@pec.example.com"),
            iban=factory.Sequence(lambda n: iban("IT", f"X0300003200{n:012d}")),
        )
        eu_company = factory.Trait(
            country="DE",
            legal_form=Vendor.LegalForm.OTHER,
            legal_name=factory.LazyAttribute(lambda o: f"{o.display_name} GmbH"),
            vat_number=factory.Sequence(lambda n: f"DE{n:09d}"),
            tax_residence_country="DE",
            fiscal_regime=Vendor.FiscalRegime.RF18,
            eori=factory.Sequence(lambda n: f"DE{n:013d}"),
            registered_street="Example Strasse",
            registered_street_number="1",
            registered_postal_code="10115",
            registered_city="Berlin",
            registered_country="DE",
            sdi_recipient_code=engine.SDI_FOREIGN_CODE,
            iban=factory.Sequence(lambda n: iban("DE", f"10000000{n:010d}")),
            bic="EXMPDEFFXXX",
            bank_name="Example Bank",
        )
        non_eu_company = factory.Trait(
            country="CH",
            legal_form=Vendor.LegalForm.OTHER,
            legal_name=factory.LazyAttribute(lambda o: f"{o.display_name} AG"),
            vat_number=factory.Sequence(lambda n: f"CHE{n:09d}"),
            tax_code=factory.SelfAttribute("vat_number"),  # the Swiss UID is also the TIN
            tax_residence_country="CH",
            fiscal_regime=Vendor.FiscalRegime.RF18,
            registered_street="Example Strasse",
            registered_street_number="1",
            registered_postal_code="8001",
            registered_city="Zurich",
            registered_country="CH",
            sdi_recipient_code=engine.SDI_FOREIGN_CODE,
            fiscal_representative_name="Fiscal Representative Example S.r.l.",
            fiscal_representative_vat_number=factory.Sequence(lambda n: italian_vat(n + 5000)),
            iban=factory.Sequence(lambda n: iban("CH", f"00000{n:012d}")),
            bic="EXMPCHZZXXX",
            bank_name="Example Bank",
        )
        non_eu_without_vat = factory.Trait(
            country="US",
            legal_form=Vendor.LegalForm.OTHER,
            legal_name=factory.LazyAttribute(lambda o: f"{o.display_name} Inc."),
            vat_number="",
            tax_code=factory.Sequence(lambda n: f"{n % 100:02d}-{n:07d}"),
            tax_residence_country="US",
            registered_street="Example Street",
            registered_street_number="1",
            registered_postal_code="94105",
            registered_city="San Francisco",
            registered_country="US",
            iban=factory.Sequence(lambda n: iban("GB", f"EXMP{n:014d}")),
        )

    display_name = factory.Sequence(lambda n: f"Vendor {pseudonym(n)}")
    legal_name = factory.LazyAttribute(lambda o: f"{o.display_name} S.r.l.")
    country = "IT"
    vat_number = factory.Sequence(lambda n: f"IT{italian_vat(n)}")
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


class LegalFieldRequirementFactory(DjangoModelFactory[LegalFieldRequirement]):
    """A payout requirement for every vendor; pass `country=` and `legal_form=` to narrow it.

    The seeding data migration's rules all name a country, so these defaults never collide with
    them (see `apps.vendors.tests.fakes.seed_legal_requirements`).
    """

    class Meta:
        model = LegalFieldRequirement

    country = ""
    legal_form = ""
    field_name = factory.Sequence(lambda n: engine.LEGAL_FIELDS[n % len(engine.LEGAL_FIELDS)])
    purpose = LegalFieldRequirement.Purpose.PAYOUT
    active = True
