"""factory_boy factories for the tax models (TAX.1). Synthetic values only.

Versioned rows (rates, vendor tax profiles) are written directly by these factories; tests of
the versioning rules go through `apps.tax.services` with `.build()`, as the services save.
"""

import datetime as dt
from decimal import Decimal

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.core.tests.factories import utc
from apps.tax import engine
from apps.tax.models import (
    TaxRate,
    Territory,
    VatValidation,
    VendorTaxProfile,
    VendorTaxProfileTranslation,
)
from apps.vendors.engine import FLAT_RATE_REGIME
from apps.vendors.tests.factories import VendorFactory


def _one_year_later(start: dt.datetime) -> dt.datetime:
    return start.replace(year=start.year + 1)


class TerritoryFactory(DjangoModelFactory[Territory]):
    """A Spanish territory outside the EU VAT area, matched by a postcode prefix."""

    class Meta:
        model = Territory

    code = factory.Sequence(lambda n: f"ES-T{n}")
    name = factory.Sequence(lambda n: f"Territory {n}")
    country = "ES"
    vat_status = engine.OUTSIDE_VAT_AREA
    postcode_patterns = factory.LazyFunction(lambda: ["35*"])


class TaxRateFactory(DjangoModelFactory[TaxRate]):
    """A closed one-year period of the Italian standard rate; consecutive rows are adjacent.

    `open_ended=True` gives the current row. Pass explicit dates when a test is about validity.
    """

    class Meta:
        model = TaxRate

    class Params:
        open_ended = factory.Trait(valid_to=None)

    country = "IT"
    territory = None
    category = engine.STANDARD
    rate = Decimal("22.0000")
    source = engine.RATE_SOURCE_MANUAL
    valid_from = factory.Sequence(lambda n: utc(2027 + n))
    valid_to = factory.LazyAttribute(lambda o: _one_year_later(o.valid_from))


class VendorTaxProfileFactory(DjangoModelFactory[VendorTaxProfile]):
    """The current (open-ended) profile of a new vendor, `domestic_only` by default.

    Traits: `oss` (registered for OSS), `art74` (exempt publisher) and `flat_rate` (exempt
    forfettario, with a vendor declaring fiscal regime RF19).
    """

    class Meta:
        model = VendorTaxProfile

    class Params:
        oss = factory.Trait(
            regime=engine.OSS,
            oss_registered_since=factory.LazyFunction(lambda: dt.date(2026, 1, 1)),
        )
        art74 = factory.Trait(regime=engine.EXEMPT, exempt_reason=engine.ART74_PUBLISHING)
        flat_rate = factory.Trait(
            regime=engine.EXEMPT,
            exempt_reason=engine.FLAT_RATE_FORFETTARIO,
            vendor=factory.SubFactory(VendorFactory, fiscal_regime=FLAT_RATE_REGIME),
        )

    vendor = factory.SubFactory(VendorFactory)
    regime = engine.DOMESTIC_ONLY
    exempt_reason = ""
    oss_registered_since = None
    valid_from = factory.LazyFunction(lambda: utc(2027))
    valid_to = None


class VendorTaxProfileTranslationFactory(DjangoModelFactory[VendorTaxProfileTranslation]):
    class Meta:
        model = VendorTaxProfileTranslation

    profile = factory.SubFactory(VendorTaxProfileFactory, art74=True)
    language = "en"
    legal_note = "VAT not applicable: example exemption wording."


class VatValidationFactory(DjangoModelFactory[VatValidation]):
    """A valid VIES answer for a German VAT number (stored without its prefix)."""

    class Meta:
        model = VatValidation

    country = "DE"
    vat_number = factory.Sequence(lambda n: f"{n:09d}")
    result = engine.VAT_VALID
    name = factory.Sequence(lambda n: f"Example Customer {n} GmbH")
    address = "Example Strasse 1, 10115 Berlin"
    request_id = factory.Sequence(lambda n: f"REQ{n:08d}")
    checked_at = factory.LazyFunction(timezone.now)
