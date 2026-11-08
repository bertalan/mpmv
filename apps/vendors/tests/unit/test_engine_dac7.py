"""VEN.23: the DAC7 seller dataset (owner decision MQ-6), pure rules.

Done when: a test pins the field list to Annex V III.B; a TIN without its issuing country is
incomplete for a payout; "same as the VAT number" fills the TIN from a VIES-valid VAT number; a
malformed TIN for its country is refused; a vendor whose country issues no TIN is complete with
a place of birth. The DAC7 check is the single source the payout gate uses: no requirement rule
can relax it.
"""

import dataclasses
import datetime as dt
from typing import Any

import pytest

from apps.vendors import engine
from apps.vendors.engine import LegalIdentity, Requirement

IT_VAT = "01234567897"
IT_IBAN = "IT60X0542811101000000123456"
DE_IBAN = "DE89370400440532013000"
GB_IBAN = "GB82WEST12345698765432"
CF = "RSSMRA85T10A562S"

ADDRESS = (
    "registered_street",
    "registered_street_number",
    "registered_postal_code",
    "registered_city",
    "registered_province",
    "registered_country",
)


def entity(**overrides: Any) -> LegalIdentity:
    values: dict[str, Any] = {
        "country": "DE",
        "legal_name": "Vendor C GmbH",
        "legal_form": engine.OTHER_LEGAL_FORM,
        "vat_number": "DE123456789",
        "tax_code": "12345678901",
        "tin_country": "DE",
        "tax_residence_country": "DE",
        "business_registration_number": "HRB123456",
        "registered_street": "Example Strasse",
        "registered_street_number": "1",
        "registered_postal_code": "10115",
        "registered_city": "Berlin",
        "registered_country": "DE",
        "iban": DE_IBAN,
    }
    values.update(overrides)
    return LegalIdentity(**values)


def individual(**overrides: Any) -> LegalIdentity:
    values: dict[str, Any] = {
        "country": "IT",
        "legal_name": "Mario Rossi",
        "legal_form": engine.SOLE_PROPRIETORSHIP,
        "first_name": "Mario",
        "last_name": "Rossi",
        "tax_code": CF,
        "tin_country": "IT",
        "tax_residence_country": "IT",
        "date_of_birth": dt.date(1985, 12, 10),
        "registered_street": "Via Example",
        "registered_street_number": "1",
        "registered_postal_code": "20100",
        "registered_city": "Milan",
        "registered_province": "MI",
        "registered_country": "IT",
        "iban": IT_IBAN,
    }
    values.update(overrides)
    return LegalIdentity(**values)


# --- The dataset is pinned to Directive (EU) 2021/514, Annex V


def test_the_individual_dataset_is_pinned_to_annex_v_iii_b() -> None:
    """Section III.B.2: the Section II.B.1 data plus the account and the residence."""
    assert engine.DAC7_INDIVIDUAL_DATASET == (
        ("II.B.1(a)", "first and last name", ("first_name", "last_name")),
        ("II.B.1(b)", "primary address", ADDRESS),
        (
            "II.B.1(c)",
            "TIN with its Member State of issuance, or the place of birth without a TIN",
            ("tax_code", "tin_country", "place_of_birth"),
        ),
        ("II.B.1(d)", "VAT identification number, where available", ("vat_number",)),
        ("II.B.1(e)", "date of birth", ("date_of_birth",)),
        ("III.B.2(b)", "financial account identifier", ("iban",)),
        (
            "III.B.2(c)",
            "name of the financial account holder, where different",
            ("account_holder_name",),
        ),
        ("III.B.2(d)", "Member State of residence", ("tax_residence_country",)),
    )


def test_the_entity_dataset_is_pinned_to_annex_v_iii_b() -> None:
    """Section III.B.2: the Section II.B.2 data plus the account and the residence."""
    establishment = tuple(name.replace("registered", "establishment") for name in ADDRESS)
    assert (
        ("II.B.2(a)", "legal name", ("legal_name",)),
        ("II.B.2(b)", "primary address", ADDRESS),
        ("II.B.2(c)", "TIN with its Member State of issuance", ("tax_code", "tin_country")),
        ("II.B.2(d)", "VAT identification number, where available", ("vat_number",)),
        ("II.B.2(e)", "business registration number", ("business_registration_number",)),
        ("II.B.2(f)", "permanent establishment in the Union, where available", establishment),
        ("III.B.2(b)", "financial account identifier", ("iban",)),
        (
            "III.B.2(c)",
            "name of the financial account holder, where different",
            ("account_holder_name",),
        ),
        ("III.B.2(d)", "Member State of residence", ("tax_residence_country",)),
    ) == engine.DAC7_ENTITY_DATASET


def test_every_dataset_field_is_a_legal_field() -> None:
    for _, _, names in engine.DAC7_INDIVIDUAL_DATASET + engine.DAC7_ENTITY_DATASET:
        for name in names:
            assert name == "legal_name" or name in engine.LEGAL_FIELDS, name
    assert {"tin_same_as_vat", "legal_form"} <= engine.DAC7_FIELDS


# --- Completeness depends on the legal form


def test_complete_individual_and_entity() -> None:
    assert engine.dac7_missing(individual()) == ()
    assert engine.dac7_missing(entity()) == ()


def test_an_unknown_legal_form_cannot_be_decided() -> None:
    assert "legal_form" in engine.dac7_missing(entity(legal_form=""))


def test_an_individual_needs_names_and_date_of_birth() -> None:
    identity = individual(first_name="", last_name="", date_of_birth=None)
    assert engine.dac7_missing(identity) == ("first_name", "last_name", "date_of_birth")


def test_an_entity_needs_its_legal_name_and_business_registration_number() -> None:
    identity = entity(legal_name="", business_registration_number="")
    assert engine.dac7_missing(identity) == ("legal_name", "business_registration_number")


def test_an_entity_needs_no_date_of_birth_and_an_individual_no_registration_number() -> None:
    assert engine.dac7_missing(entity(date_of_birth=None)) == ()
    assert engine.dac7_missing(individual(business_registration_number="")) == ()


def test_the_tin_is_always_asked_even_with_a_vat_number() -> None:
    assert engine.dac7_missing(entity(tax_code="")) == ("tax_code",)


def test_a_tin_without_its_issuing_country_is_incomplete() -> None:
    assert engine.dac7_missing(entity(tin_country="")) == ("tin_country",)


def test_the_vat_number_is_optional_but_a_vies_invalid_one_blocks() -> None:
    assert engine.dac7_missing(entity(vat_number="")) == ()
    invalid = entity(vies_status=engine.VIES_INVALID)
    assert engine.dac7_check(invalid).invalid == ("vat_number",)
    assert engine.dac7_missing(entity(vies_status=engine.VIES_UNAVAILABLE)) == ()


def test_the_rest_of_the_dataset() -> None:
    identity = entity(tax_residence_country="", iban="", registered_city="", registered_country="")
    assert engine.dac7_missing(identity) == (
        "tax_residence_country",
        "registered_city",
        "registered_country",
        "iban",
    )
    assert engine.dac7_missing(individual(registered_province="")) == ("registered_province",)


def test_the_account_holder_and_the_establishment_are_never_missing() -> None:
    assert engine.dac7_missing(entity(account_holder_name="", establishment_city="")) == ()


# --- Countries that issue no TIN


def test_a_vendor_whose_country_issues_no_tin_is_complete_with_a_place_of_birth() -> None:
    assert "BS" in engine.NO_TIN_COUNTRIES
    identity = individual(
        country="BS",
        tax_code="",
        tin_country="BS",
        tax_residence_country="BS",
        registered_province="",
        registered_country="BS",
        registered_postal_code="N-1234",
        registered_city="Nassau",
        iban=GB_IBAN,
    )
    assert engine.dac7_missing(identity) == ("place_of_birth",)
    assert engine.dac7_missing(dataclasses.replace(identity, place_of_birth="Nassau")) == ()


def test_a_country_that_issues_tins_needs_the_tin_not_the_place_of_birth() -> None:
    assert engine.dac7_missing(individual(tax_code="", place_of_birth="Milan")) == ("tax_code",)


# --- TIN formats per issuing country


@pytest.mark.parametrize(
    ("country", "tin"),
    [
        ("IT", CF),
        ("IT", IT_VAT),
        ("DE", "12345678901"),
        ("DE", "12/345/67890"),
        ("FR", "0123456789012"),
        ("ES", "12345678Z"),
        ("NL", "123456789"),
        ("AT", "123456789"),
        ("PL", "1234567890"),
        ("US", "12-3456789"),  # no format table: any plausible identifier
    ],
)
def test_well_formed_tins(country: str, tin: str) -> None:
    assert engine.is_valid_tin(tin, country)
    assert "tax_code" not in engine.identity_errors(entity(tax_code=tin, tin_country=country))


@pytest.mark.parametrize(
    ("country", "tin"),
    [("IT", "RSSMRA85T10A562X"), ("DE", "1234"), ("FR", "ABC"), ("NL", "12345678"), ("US", "?")],
)
def test_a_malformed_tin_for_its_country_is_refused(country: str, tin: str) -> None:
    assert not engine.is_valid_tin(tin, country)
    errors = engine.identity_errors(entity(tax_code=tin, tin_country=country))
    assert "tax_code" in errors
    assert engine.dac7_check(entity(tax_code=tin, tin_country=country)).invalid == ("tax_code",)


def test_without_an_issuing_country_the_tin_follows_the_vendor_country() -> None:
    assert "tax_code" in engine.identity_errors(individual(tin_country="", tax_code="XYZ"))


def test_every_eu_member_state_has_a_tin_format() -> None:
    assert set(engine.TIN_FORMATS) == set(engine.EU_MEMBER_STATES)


# --- "Same as the VAT number"


def test_same_as_vat_fills_the_tin_from_a_vies_valid_vat_number() -> None:
    identity = entity(
        country="IT",
        vat_number="IT" + IT_VAT,
        tax_code="",
        tin_country="",
        tin_same_as_vat=True,
        vies_status=engine.VIES_VALID,
    )
    filled = engine.tin_from_vat(identity)
    assert (filled.tax_code, filled.tin_country) == (IT_VAT, "IT")


@pytest.mark.parametrize(
    "status", [engine.VIES_UNCHECKED, engine.VIES_INVALID, engine.VIES_UNAVAILABLE]
)
def test_same_as_vat_leaves_no_tin_while_the_vat_number_is_not_vies_valid(status: str) -> None:
    identity = entity(tin_same_as_vat=True, vies_status=status)
    filled = engine.tin_from_vat(identity)
    assert filled.tax_code == ""
    assert "tax_code" in engine.dac7_missing(filled)


def test_without_the_option_the_tin_is_left_alone() -> None:
    identity = entity(vies_status=engine.VIES_VALID)
    assert engine.tin_from_vat(identity) == identity


def test_same_as_vat_is_for_entities_with_a_vat_number() -> None:
    errors = engine.identity_errors(individual(tin_same_as_vat=True, vat_number=IT_VAT))
    assert "tin_same_as_vat" in errors
    errors = engine.identity_errors(entity(tin_same_as_vat=True, vat_number=""))
    assert "tin_same_as_vat" in errors


# --- The payout gate: requirement rules plus the DAC7 check, which no rule can relax


def test_the_payout_gate_enforces_dac7_without_any_rule() -> None:
    result = engine.payout_completeness(entity(tin_country=""), [])
    assert result.missing == ("tin_country",)
    assert not result.complete


def test_the_payout_gate_merges_rules_and_dac7_in_field_order() -> None:
    rules = [Requirement(field="pec"), Requirement(field="tin_country")]
    result = engine.payout_completeness(entity(tin_country="", iban=""), rules)
    assert result.missing == ("tin_country", "pec", "iban")


def test_the_payout_gate_reports_invalid_dac7_fields() -> None:
    result = engine.payout_completeness(entity(tax_code="1234"), [])
    assert result.invalid == ("tax_code",)


# --- "Same as the VAT number" only where the VAT number is the TIN (review of PR #137)

# One VAT number per country that allows the option: without its prefix it is a TIN of that
# country (TIN_FORMATS), so the derived TIN is valid.
SAME_AS_VAT_SAMPLES = {
    "BE": "BE0123456789",
    "BG": "BG123456789",
    "CY": "CY12345678X",
    "CZ": "CZ12345678",
    "DK": "DK12345678",
    "ES": "ESB12345678",
    "FI": "FI12345678",
    "GR": "EL123456789",
    "HR": "HR12345678901",
    "IE": "IE1234567T",
    "IT": "IT" + IT_VAT,
    "LV": "LV12345678901",
    "PL": "PL1234567890",
    "PT": "PT123456789",
    "RO": "RO12345678",
    "SI": "SI12345678",
    "SK": "SK1234567890",
}


def same_as_vat(country: str, vat_number: str, **overrides: Any) -> LegalIdentity:
    values: dict[str, Any] = {
        "country": country,
        "vat_number": vat_number,
        "tax_code": "",
        "tin_country": "",
        "tin_same_as_vat": True,
        "vies_status": engine.VIES_VALID,
        "registered_country": country,
    }
    values.update(overrides)
    return entity(**values)


def test_every_country_allowing_same_as_vat_has_a_sample() -> None:
    assert set(SAME_AS_VAT_SAMPLES) == set(engine.SAME_AS_VAT_COUNTRIES)


@pytest.mark.parametrize(("country", "vat_number"), sorted(SAME_AS_VAT_SAMPLES.items()))
def test_same_as_vat_derives_a_valid_tin_in_every_allowed_country(
    country: str, vat_number: str
) -> None:
    identity = same_as_vat(country, vat_number)
    assert "tin_same_as_vat" not in engine.identity_errors(identity)
    filled = engine.tin_from_vat(identity)
    assert filled.tin_country == country
    assert filled.tax_code
    assert engine.is_valid_tin(filled.tax_code, country)
    assert "tax_code" not in engine.dac7_check(filled).invalid


@pytest.mark.parametrize(
    ("country", "vat_number"),
    [
        ("DE", "DE000000000"),
        ("AT", "ATU12345678"),
        ("FR", "FR12345678901"),
        ("NL", "NL123456789B01"),
        ("SE", "SE123456789001"),
        ("US", "US123456789"),
    ],
)
def test_same_as_vat_is_refused_where_the_vat_number_is_not_the_tin(
    country: str, vat_number: str
) -> None:
    identity = same_as_vat(country, vat_number)
    assert "tin_same_as_vat" in engine.identity_errors(identity)
    filled = engine.tin_from_vat(identity)
    assert (filled.tax_code, filled.tin_country) == ("", "")


def test_vies_unavailable_keeps_a_tin_already_derived_from_the_vat_number() -> None:
    identity = same_as_vat("IT", "IT" + IT_VAT, tax_code=IT_VAT, tin_country="IT")
    kept = engine.tin_from_vat(dataclasses.replace(identity, vies_status=engine.VIES_UNAVAILABLE))
    assert (kept.tax_code, kept.tin_country) == (IT_VAT, "IT")
    # A definite answer still decides.
    gone = engine.tin_from_vat(dataclasses.replace(identity, vies_status=engine.VIES_INVALID))
    assert (gone.tax_code, gone.tin_country) == ("", "")


def test_vies_unavailable_does_not_keep_a_tin_that_is_not_the_vat_number() -> None:
    identity = same_as_vat(
        "IT", "IT" + IT_VAT, tax_code=CF, tin_country="IT", vies_status=engine.VIES_UNAVAILABLE
    )
    assert engine.tin_from_vat(identity).tax_code == ""


# --- An entity always needs a TIN, even where TINs are not issued (review of PR #137)


def test_an_entity_whose_country_issues_no_tin_still_needs_a_tin() -> None:
    assert "AE" in engine.NO_TIN_COUNTRIES
    identity = entity(
        country="AE",
        legal_name="Acme LLC",
        vat_number="",
        tax_code="",
        tin_country="AE",
        tax_residence_country="AE",
        business_registration_number="123",
        registered_country="AE",
        registered_postal_code="00000",
        registered_city="Dubai",
        iban=GB_IBAN,
    )
    assert engine.dac7_check(identity).missing == ("tax_code",)
    with_birthplace = dataclasses.replace(identity, place_of_birth="Dubai")
    assert engine.dac7_check(with_birthplace).missing == ("tax_code",)
    assert engine.dac7_check(dataclasses.replace(identity, tax_code="TRN123456")).complete
