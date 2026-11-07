"""TAX.1: tax models on the real database (spec 05 §5.2): constraints, validation, history."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.core.tests.factories import utc
from apps.tax import engine
from apps.tax.models import TaxRate, Territory, VatValidation, VendorTaxProfile
from apps.tax.tests.factories import (
    TaxRateFactory,
    TerritoryFactory,
    VatValidationFactory,
    VendorTaxProfileFactory,
    VendorTaxProfileTranslationFactory,
)
from apps.vendors.engine import FLAT_RATE_REGIME
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db


# --- Territory


def test_a_territory_stores_normalized_codes_and_postcode_patterns() -> None:
    territory = TerritoryFactory.build(
        code="es-cn", country="es", postcode_patterns=["35*", " 38* "]
    )
    territory.full_clean()
    territory.save()
    stored = Territory.objects.get(pk=territory.pk)
    assert (stored.code, stored.country) == ("ES-CN", "ES")
    assert stored.postcode_patterns == ["35*", "38*"]


def test_northern_ireland_is_data_with_a_special_status_and_no_rule() -> None:
    territory = TerritoryFactory.build(
        code="XI", country="GB", vat_status=engine.SPECIAL, postcode_patterns=["BT*"]
    )
    territory.full_clean()
    territory.save()
    assert Territory.objects.get(code="XI").vat_status == "special"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code", "ES_CN"),
        ("country", "XX"),
        ("vat_status", "inside"),
        ("postcode_patterns", ["3*5"]),
    ],
)
def test_invalid_territories_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError) as exc:
        TerritoryFactory.build(**{field: value}).full_clean()
    assert field in exc.value.message_dict


def test_territory_codes_are_unique() -> None:
    TerritoryFactory.create(code="IT-LIVIGNO", country="IT")
    with pytest.raises(IntegrityError), transaction.atomic():
        TerritoryFactory.create(code="IT-LIVIGNO", country="IT")


# --- TaxRate


def test_a_rate_keeps_four_decimals_exactly() -> None:
    rate = TaxRateFactory.create(country="FI", rate=Decimal("25.5"))
    assert TaxRate.objects.get(pk=rate.pk).rate == Decimal("25.5000")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("country", "XX"),
        ("category", "luxury"),
        ("source", "rumour"),
        ("rate", Decimal("100.5")),
        ("rate", Decimal("22.00001")),
    ],
)
def test_invalid_rates_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError) as exc:
        TaxRateFactory.build(**{field: value}).full_clean()
    assert field in exc.value.message_dict


def test_the_zero_category_cannot_hold_a_positive_rate() -> None:
    with pytest.raises(ValidationError) as exc:
        TaxRateFactory.build(category=engine.ZERO, rate=Decimal("4")).full_clean()
    assert "rate" in exc.value.message_dict
    with pytest.raises(IntegrityError), transaction.atomic():
        TaxRateFactory.create(category=engine.ZERO, rate=Decimal("4"))


def test_a_negative_rate_is_refused_by_the_database() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        TaxRateFactory.create(rate=Decimal("-1"))


def test_a_territory_rate_belongs_to_the_territory_country() -> None:
    madeira = TerritoryFactory.create(code="PT-30", country="PT", vat_status=engine.OWN_RATES)
    with pytest.raises(ValidationError) as exc:
        TaxRateFactory.build(country="ES", territory=madeira).full_clean()
    assert "territory" in exc.value.message_dict
    TaxRateFactory.build(country="PT", territory=madeira, rate=Decimal("22")).full_clean()


def test_overlapping_rates_of_the_same_country_and_category_are_refused() -> None:
    TaxRateFactory.create(valid_from=utc(2027), open_ended=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        TaxRateFactory.create(valid_from=utc(2028), valid_to=utc(2029))


def test_overlapping_rates_of_the_same_territory_are_refused() -> None:
    madeira = TerritoryFactory.create(code="PT-30", country="PT", vat_status=engine.OWN_RATES)
    TaxRateFactory.create(country="PT", territory=madeira, valid_from=utc(2027), open_ended=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        TaxRateFactory.create(country="PT", territory=madeira, valid_from=utc(2028))


def test_rates_of_other_categories_countries_and_territories_may_overlap() -> None:
    madeira = TerritoryFactory.create(code="PT-30", country="PT", vat_status=engine.OWN_RATES)
    TaxRateFactory.create(country="PT", valid_from=utc(2027), open_ended=True)
    TaxRateFactory.create(country="PT", territory=madeira, valid_from=utc(2027), open_ended=True)
    TaxRateFactory.create(
        country="PT", category=engine.REDUCED, rate=Decimal("6"), valid_from=utc(2027)
    )
    TaxRateFactory.create(country="ES", rate=Decimal("21"), valid_from=utc(2027))
    assert TaxRate.objects.count() == 4


def test_adjacent_rate_periods_are_allowed() -> None:
    TaxRateFactory.create(valid_from=utc(2027), valid_to=utc(2028))
    TaxRateFactory.create(valid_from=utc(2028), open_ended=True)
    assert TaxRate.objects.count() == 2


def test_a_rate_period_ends_after_it_starts() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        TaxRateFactory.create(valid_from=utc(2028), valid_to=utc(2027))


def test_rates_are_never_edited_in_place() -> None:
    rate = TaxRateFactory.create()
    rate.rate = Decimal("23")
    with pytest.raises(PermissionError, match="new row"):
        rate.save()


def test_a_territory_with_rates_cannot_be_deleted() -> None:
    madeira = TerritoryFactory.create(code="PT-30", country="PT", vat_status=engine.OWN_RATES)
    TaxRateFactory.create(country="PT", territory=madeira)
    with pytest.raises(IntegrityError), transaction.atomic():
        madeira.delete()


# --- VendorTaxProfile


@pytest.mark.parametrize("trait", ["oss", "art74", "flat_rate"])
def test_profiles_of_every_regime_are_valid(trait: str) -> None:
    profile = VendorTaxProfileFactory.create(**{trait: True})
    profile.full_clean()


def test_an_exempt_profile_without_a_reason_is_refused() -> None:
    with pytest.raises(ValidationError) as exc:
        VendorTaxProfileFactory.build(regime=engine.EXEMPT).full_clean()
    assert "exempt_reason" in exc.value.message_dict
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorTaxProfileFactory.create(regime=engine.EXEMPT)


def test_a_taxable_profile_with_a_reason_is_refused_by_the_database() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorTaxProfileFactory.create(exempt_reason=engine.ART74_PUBLISHING)


def test_the_flat_rate_exemption_needs_a_vendor_declaring_rf19() -> None:
    vendor = VendorFactory.create(fiscal_regime="RF01")
    profile = VendorTaxProfileFactory.build(
        vendor=vendor, regime=engine.EXEMPT, exempt_reason=engine.FLAT_RATE_FORFETTARIO
    )
    with pytest.raises(ValidationError) as exc:
        profile.full_clean()
    assert "exempt_reason" in exc.value.message_dict


def test_a_vendor_declaring_rf19_cannot_charge_vat() -> None:
    vendor = VendorFactory.create(fiscal_regime=FLAT_RATE_REGIME)
    with pytest.raises(ValidationError) as exc:
        VendorTaxProfileFactory.build(vendor=vendor, oss=True).full_clean()
    assert "regime" in exc.value.message_dict


def test_overlapping_profiles_of_one_vendor_are_refused() -> None:
    profile = VendorTaxProfileFactory.create(valid_from=utc(2027))
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorTaxProfileFactory.create(vendor=profile.vendor, valid_from=utc(2028))


def test_profiles_of_different_vendors_are_independent() -> None:
    VendorTaxProfileFactory.create(valid_from=utc(2027))
    VendorTaxProfileFactory.create(valid_from=utc(2027))
    assert VendorTaxProfile.objects.count() == 2


def test_profiles_are_never_edited_in_place() -> None:
    profile = VendorTaxProfileFactory.create()
    profile.regime = engine.OSS
    with pytest.raises(PermissionError, match="new row"):
        profile.save()


def test_profiles_are_scoped_to_their_vendor() -> None:
    mine = VendorTaxProfileFactory.create()
    VendorTaxProfileFactory.create()
    assert list(VendorTaxProfile.objects.for_vendor(mine.vendor)) == [mine]


def test_the_legal_note_is_translated_per_profile_version() -> None:
    note = VendorTaxProfileTranslationFactory.create(language="en")
    VendorTaxProfileTranslationFactory.create(profile=note.profile, language="it")
    assert note.profile.translations.count() == 2
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorTaxProfileTranslationFactory.create(profile=note.profile, language="it")


# --- VatValidation


def test_a_vat_validation_keeps_the_vies_answer() -> None:
    check = VatValidationFactory.build(country="el", vat_number="123456789")
    check.full_clean()
    check.save()
    stored = VatValidation.objects.get(pk=check.pk)
    assert stored.country == "GR"
    assert stored.result == "valid"
    assert stored.source == "vies"


@pytest.mark.parametrize(("field", "value"), [("result", "maybe"), ("source", "guess")])
def test_invalid_vat_validations_are_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError) as exc:
        VatValidationFactory.build(**{field: value}).full_clean()
    assert field in exc.value.message_dict


def test_vat_validations_are_append_only() -> None:
    check = VatValidationFactory.create()
    check.result = engine.VAT_INVALID
    with pytest.raises(PermissionError, match="append-only"):
        check.save()
