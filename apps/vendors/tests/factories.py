"""factory_boy factories for vendors (ARC.12).

Names are pseudonyms ("Vendor A", "Vendor B", ...) and texts come from the seeded Faker: never
real vendor data. Legal identifiers (VAT numbers, codici fiscali, IBANs) are synthetic but
checksum-valid, generated at runtime from the factory sequence (VEN.12).
"""

import datetime as dt
import string
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import factory
from django.db.models import Max
from django.utils import timezone
from django.utils.text import slugify
from factory.django import DjangoModelFactory

from apps.accounts.tests.factories import UserFactory
from apps.core.tests.factories import FeatureFlagFactory
from apps.core.translations import source_hash
from apps.vendors import engine
from apps.vendors.models import (
    ContractAcceptance,
    CrossVendorConsent,
    LegalFieldRequirement,
    Vendor,
    VendorClaim,
    VendorContract,
    VendorContractTranslation,
    VendorFeatureFlag,
    VendorMember,
    VendorTranslation,
)


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
            tin_country="IT",
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
            business_registration_number=factory.LazyAttribute(
                lambda o: f"{o.rea_office}-{o.rea_number}"
            ),
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
            tin_country="IT",
            date_of_birth=dt.date(1985, 1, 31),
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
            tax_code=factory.Sequence(lambda n: f"{10**10 + n}"),
            tin_country="DE",
            business_registration_number=factory.Sequence(lambda n: f"HRB{n:06d}"),
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
            tin_country="CH",
            business_registration_number=factory.SelfAttribute("vat_number"),
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
            tin_country="US",
            business_registration_number=factory.Sequence(lambda n: f"C{n:07d}"),
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


def next_contract_version() -> int:
    """The version after the latest one, as `services.save_contract` numbers them."""
    latest: int | None = VendorContract.objects.aggregate(latest=Max("version"))["latest"]
    return (latest or 0) + 1


class VendorContractFactory(DjangoModelFactory[VendorContract]):
    """A contract version in force since yesterday, with a published English text.

    `languages=[...]` chooses the published texts (`[]`: none). Factories write rows directly:
    the start-date rule is tested through `services.save_contract`.
    """

    class Meta:
        model = VendorContract
        skip_postgeneration_save = True

    version = None  # numbered after the latest version when created
    valid_from = factory.LazyFunction(lambda: timezone.now() - dt.timedelta(days=1))

    @classmethod
    def _create(cls, model_class: type[VendorContract], *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("version") is None:
            kwargs["version"] = next_contract_version()
        return super()._create(model_class, *args, **kwargs)  # type: ignore[no-untyped-call]

    @factory.post_generation
    def languages(
        obj: VendorContract, create: bool, extracted: Iterable[str] | None, **kwargs: Any
    ) -> None:
        if create:
            for language in ("en",) if extracted is None else extracted:
                VendorContractTranslationFactory.create(contract=obj, language=language)


class VendorContractTranslationFactory(DjangoModelFactory[VendorContractTranslation]):
    class Meta:
        model = VendorContractTranslation

    contract = factory.SubFactory(VendorContractFactory, languages=[])
    language = "en"
    status = VendorContractTranslation.Status.PUBLISHED
    title = factory.LazyAttribute(lambda o: f"Vendor agreement [{o.language}]")
    body = factory.Faker("paragraph")


class ContractAcceptanceFactory(DjangoModelFactory[ContractAcceptance]):
    """The owner of an onboarding vendor accepted the English text of a contract in force.

    When created, the user becomes an owner of the vendor (unless already a member) and the
    text hash is the one of the contract's text in `language`.
    """

    class Meta:
        model = ContractAcceptance
        skip_postgeneration_save = True

    vendor = factory.SubFactory(VendorFactory, status=Vendor.Status.ONBOARDING)
    contract = factory.SubFactory(VendorContractFactory)
    user = factory.SubFactory(UserFactory)
    language = "en"
    text_hash = ""
    accepted_at = factory.LazyFunction(timezone.now)
    ip = "198.51.100.1"
    onboarding_round = factory.SelfAttribute("vendor.onboarding_round")  # VEN.21

    @classmethod
    def _create(cls, model_class: type[ContractAcceptance], *args: Any, **kwargs: Any) -> Any:
        if not kwargs.get("text_hash"):
            text = kwargs["contract"].translations.get(language=kwargs["language"])
            kwargs["text_hash"] = source_hash(text.source_values())
        return super()._create(model_class, *args, **kwargs)  # type: ignore[no-untyped-call]

    @factory.post_generation
    def owner(obj: ContractAcceptance, create: bool, extracted: Any, **kwargs: Any) -> None:
        if create and not VendorMember.objects.filter(vendor=obj.vendor, user=obj.user).exists():
            VendorMemberFactory.create(vendor=obj.vendor, user=obj.user)


class VendorFeatureFlagFactory(DjangoModelFactory[VendorFeatureFlag]):
    """A vendor's override switching a registered flag off for that vendor (ARC.8)."""

    class Meta:
        model = VendorFeatureFlag

    vendor = factory.SubFactory(VendorFactory)
    flag = factory.SubFactory(FeatureFlagFactory)
    enabled = False
    note = ""


class CrossVendorConsentFactory(DjangoModelFactory[CrossVendorConsent]):
    """A vendor opted in to other vendors' suggestions from yesterday (CAT.14). Production code
    records versions with `services.set_cross_vendor_consent`, which audits them."""

    class Meta:
        model = CrossVendorConsent

    vendor = factory.SubFactory(VendorFactory)
    in_suggestions = True
    in_bundles = False
    valid_from = factory.LazyFunction(lambda: timezone.now() - dt.timedelta(days=1))
    set_by = "staff:factory"


class VendorClaimFactory(DjangoModelFactory[VendorClaim]):
    """An open claim of an imported vendor (VEN.24); the link itself is sent by the service."""

    class Meta:
        model = VendorClaim

    vendor = factory.SubFactory(VendorFactory)
    status = VendorClaim.Status.OPEN
    version = 1
