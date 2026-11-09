"""VEN.25 (owner decision MQ-5): who may declare fiscal regime RF20, the cross-border VAT
franchise for small enterprises (art. 284 of the VAT directive as amended by Directive (EU)
2020/285). No ORM.

RF20 is only for vendors whose registered office (seat) is in an EU member state, with the
individual identification number (suffix `EX`, art. 284(3)) issued by that member state; a
permanent establishment alone does not qualify (owner interim decision MQ-17). Identifiers are
synthetic: national parts follow each member state's VAT number format, never a real number.
"""

import dataclasses

import pytest

from apps.core.countries import EU_MEMBER_STATES
from apps.vendors import engine
from apps.vendors.engine import LegalIdentity, identity_errors

RF20 = engine.CROSS_BORDER_FRANCHISE_REGIME
NUMBER = "cross_border_franchise_number"

# One synthetic, well-formed number per issuing member state (the VAT prefix, EL for Greece).
VALID = {
    "AT": "ATU12345678EX",
    "BE": "BE0123456789EX",
    "BG": "BG123456789EX",
    "CY": "CY12345678XEX",
    "CZ": "CZ12345678EX",
    "DE": "DE123456789EX",
    "DK": "DK12345678EX",
    "EE": "EE123456789EX",
    "ES": "ESX1234567XEX",
    "FI": "FI12345678EX",
    "FR": "FRXX123456789EX",
    "GR": "EL123456789EX",
    "HR": "HR12345678901EX",
    "HU": "HU12345678EX",
    "IE": "IE1234567WAEX",
    "IT": "IT01234567897EX",
    "LT": "LT123456789EX",
    "LU": "LU12345678EX",
    "LV": "LV12345678901EX",
    "MT": "MT12345678EX",
    "NL": "NL123456789B01EX",
    "PL": "PL1234567890EX",
    "PT": "PT123456789EX",
    "RO": "RO1234567890EX",
    "SE": "SE123456789001EX",
    "SI": "SI12345678EX",
    "SK": "SK1234567890EX",
}


def vendor(country: str = "DE", **values: str) -> LegalIdentity:
    return LegalIdentity(country=country, legal_name="Vendor A", **values)  # type: ignore[arg-type]


# --- The number format, per issuing member state


def test_every_eu_member_state_has_a_format_and_nothing_else_does() -> None:
    assert set(engine.CROSS_BORDER_FRANCHISE_NUMBER_FORMATS) == EU_MEMBER_STATES
    assert set(VALID) == EU_MEMBER_STATES


@pytest.mark.parametrize(("country", "number"), sorted(VALID.items()))
def test_a_well_formed_number_names_its_issuing_member_state(country: str, number: str) -> None:
    assert engine.franchise_number_issuer(number) == country


def test_spaces_and_lower_case_are_tolerated() -> None:
    assert engine.franchise_number_issuer(" de 123 456 789 ex ") == "DE"


@pytest.mark.parametrize(
    "number",
    [
        "DE123456789",  # no EX suffix
        "123456789EX",  # no country prefix
        "DE12345678EX",  # 8 digits: Germany issues 9
        "DE1234567890EX",  # 10 digits
        "ATU1234567EX",  # Austria: U + 8 digits
        "NL123456789X01EX",  # Netherlands: B before the last two digits
        "GR123456789EX",  # Greece uses the EL prefix
        "CH123456789EX",  # not an EU member state
        "US123456789EX",
        "XX123456789EX",  # not a country
        "EX",
        "",
    ],
)
def test_a_malformed_number_has_no_issuer(number: str) -> None:
    assert engine.franchise_number_issuer(number) is None


# --- Establishment in the EU: the registered office (seat) only, owner interim decision MQ-17
#
# Until the tax advisor answers, a permanent establishment alone does not qualify for RF20.
# Revisit when MQ-17 is answered.


def test_the_seat_is_the_registered_office_and_defaults_to_the_vendor_country() -> None:
    assert engine.rf20_seat_country(vendor("DE")) == "DE"
    assert engine.rf20_seat_country(vendor("DE", registered_country="AT")) == "AT"


def test_a_permanent_establishment_does_not_change_the_seat() -> None:
    assert engine.rf20_seat_country(vendor("IT", establishment_country="DE")) == "IT"


def test_a_seat_outside_the_eu_has_no_rf20_seat() -> None:
    assert engine.rf20_seat_country(vendor("US")) == ""
    assert engine.rf20_seat_country(vendor("CH", establishment_country="IT")) == ""


# --- Who may declare RF20


def test_rf20_is_refused_for_a_vendor_outside_the_eu_without_an_ex_number() -> None:
    errors = identity_errors(vendor("US", fiscal_regime=RF20))
    assert "EU member state" in errors["fiscal_regime"][0]
    assert "EX" in errors[NUMBER][0]


def test_rf20_is_refused_for_a_vendor_outside_the_eu_even_with_an_ex_number() -> None:
    errors = identity_errors(
        vendor("CH", fiscal_regime=RF20, cross_border_franchise_number=VALID["DE"])
    )
    assert "fiscal_regime" in errors
    assert NUMBER in errors


def test_rf20_without_the_number_is_refused_for_an_eu_vendor() -> None:
    errors = identity_errors(vendor("DE", fiscal_regime=RF20))
    assert set(errors) == {NUMBER}
    assert "EX" in errors[NUMBER][0]


def test_a_malformed_number_is_refused() -> None:
    errors = identity_errors(
        vendor("DE", fiscal_regime=RF20, cross_border_franchise_number="DE12345EX")
    )
    assert set(errors) == {NUMBER}
    assert "Not a valid" in errors[NUMBER][0]


def test_a_number_issued_by_another_member_state_is_refused() -> None:
    errors = identity_errors(
        vendor("DE", fiscal_regime=RF20, cross_border_franchise_number=VALID["IT"])
    )
    assert set(errors) == {NUMBER}
    assert "IT" in errors[NUMBER][0]
    assert "DE" in errors[NUMBER][0]


@pytest.mark.parametrize(("country", "number"), sorted(VALID.items()))
def test_an_eu_vendor_with_its_own_member_states_number_may_declare_rf20(
    country: str, number: str
) -> None:
    identity = vendor(country, fiscal_regime=RF20, cross_border_franchise_number=number)
    assert identity_errors(identity) == {}


def test_a_non_eu_seat_with_an_eu_permanent_establishment_is_refused() -> None:
    """MQ-17 interim: a permanent establishment alone does not qualify."""
    identity = vendor(
        "CH",
        establishment_country="IT",
        fiscal_regime=RF20,
        cross_border_franchise_number=VALID["IT"],
    )
    errors = identity_errors(identity)
    assert "EU member state" in errors["fiscal_regime"][0]
    assert NUMBER in errors


def test_an_it_seat_with_a_de_permanent_establishment_needs_an_it_number() -> None:
    """MQ-17 interim: the EX number must be issued by the seat's member state."""
    refused = vendor(
        "IT",
        establishment_country="DE",
        fiscal_regime=RF20,
        cross_border_franchise_number=VALID["DE"],
    )
    errors = identity_errors(refused)
    assert set(errors) == {NUMBER}
    assert "DE" in errors[NUMBER][0]
    assert "IT" in errors[NUMBER][0]
    accepted = dataclasses.replace(refused, cross_border_franchise_number=VALID["IT"])
    assert identity_errors(accepted) == {}


def test_the_number_is_stored_compact() -> None:
    identity = engine.normalized(vendor("DE", cross_border_franchise_number=" de 123456789 ex"))
    assert identity.cross_border_franchise_number == "DE123456789EX"


def test_a_number_without_rf20_is_still_checked() -> None:
    errors = identity_errors(vendor("DE", fiscal_regime="RF01", cross_border_franchise_number="X"))
    assert set(errors) == {NUMBER}
    assert (
        identity_errors(
            vendor("DE", fiscal_regime="RF01", cross_border_franchise_number=VALID["DE"])
        )
        == {}
    )


def test_other_regimes_need_no_number() -> None:
    assert identity_errors(vendor("US", fiscal_regime="RF01")) == {}
