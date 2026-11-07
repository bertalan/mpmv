"""VEN.12: pure rules of the vendor legal identity — validators, required fields resolution and
the FatturaPA 1.2.x party block.

Identifiers are documentation examples or computed from the check-digit functions; never real.
"""

import dataclasses
from decimal import Decimal
from typing import Any

import pytest

from apps.vendors import engine
from apps.vendors.engine import (
    CEDENTE,
    CESSIONARIO,
    Completeness,
    LegalIdentity,
    PartyBlockError,
    PayoutNotAllowed,
    Requirement,
    UnknownLegalField,
    codice_fiscale_check_char,
    completeness,
    fatturapa_fiscal_representative,
    fatturapa_party,
    fatturapa_transmission,
    iban_check_digits,
    identity_errors,
    is_valid_bic,
    is_valid_codice_fiscale,
    is_valid_iban,
    is_valid_italian_vat,
    italian_vat_check_digit,
    mask_iban,
    missing_required_fields,
    normalized,
    required_fields,
    sdi_code_error,
)

# Documentation examples (ISO 13616 / bank documentation), not real accounts.
IT_IBAN = "IT60X0542811101000000123456"
DE_IBAN = "DE89370400440532013000"
GB_IBAN = "GB82WEST12345698765432"
# The textbook codice fiscale example, and two omocodic variants of it.
CF = "RSSMRA85T10A562S"
CF_OMOCODIC_LAST = "RSSMRA85T10A56NH"  # 2 → N in the municipality code
CF_OMOCODIC_DAY = "RSSMRA85T1LA562V"  # 0 → L in the day


def vat(first_ten: str) -> str:
    return first_ten + italian_vat_check_digit(first_ten)


IT_VAT = vat("0123456789")  # 01234567897


# --- Italian VAT number (partita IVA)


def test_italian_vat_check_digit_is_luhn() -> None:
    assert italian_vat_check_digit("0123456789") == "7"
    assert is_valid_italian_vat("01234567897")


@pytest.mark.parametrize("value", ["01234567897", "IT01234567897", "it 0123 4567 897"])
def test_italian_vat_accepts_prefix_spaces_and_case(value: str) -> None:
    assert is_valid_italian_vat(value)


@pytest.mark.parametrize(
    "value",
    ["01234567898", "0123456789", "012345678971", "0123456789A", "DE01234567897", ""],
)
def test_italian_vat_rejects_bad_check_digit_length_and_characters(value: str) -> None:
    assert not is_valid_italian_vat(value)


# --- Codice fiscale


def test_codice_fiscale_check_character_of_the_textbook_example() -> None:
    assert codice_fiscale_check_char(CF[:15]) == "S"
    assert is_valid_codice_fiscale(CF)
    assert is_valid_codice_fiscale(CF.lower())


@pytest.mark.parametrize("value", [CF_OMOCODIC_LAST, CF_OMOCODIC_DAY])
def test_codice_fiscale_accepts_omocodia(value: str) -> None:
    assert is_valid_codice_fiscale(value)


@pytest.mark.parametrize(
    "value",
    [
        "RSSMRA85T10A562T",  # wrong check character
        "RSSMRA85Z10A562S",  # Z is not a month letter
        "RSSMRA85T32A562S",  # day 32
        "RSSMRA85TA0A562S",  # A is not an omocodia letter
        "RSSMRA85T10A562",  # 15 characters
        "",
    ],
)
def test_codice_fiscale_rejects_invalid_codes(value: str) -> None:
    assert not is_valid_codice_fiscale(value)


def test_codice_fiscale_of_women_has_the_day_plus_40() -> None:
    first_fifteen = "RSSMRA85T50A562"
    assert is_valid_codice_fiscale(first_fifteen + codice_fiscale_check_char(first_fifteen))


def test_codice_fiscale_numeric_form_uses_the_vat_check_digit() -> None:
    assert is_valid_codice_fiscale(IT_VAT)
    assert not is_valid_codice_fiscale("01234567898")


# --- IBAN and BIC


@pytest.mark.parametrize("value", [IT_IBAN, DE_IBAN, GB_IBAN, "it60 x054 2811 1010 0000 0123 456"])
def test_valid_ibans(value: str) -> None:
    assert is_valid_iban(value)


@pytest.mark.parametrize(
    "value",
    [
        "IT60X0542811101000000123457",  # checksum
        "IT60X054281110100000012345",  # Italian IBANs have 27 characters
        "IT6000542811101000000123456",  # CIN must be a letter
        "DE8937040044053201300",  # length
        "QQ12345678901234567",  # not a country
        "",
    ],
)
def test_invalid_ibans(value: str) -> None:
    assert not is_valid_iban(value)


def test_iban_check_digits_rebuild_a_valid_iban() -> None:
    assert iban_check_digits("IT", "X0542811101000000123456") == "60"
    bban = "X0300003200000000000042"
    assert is_valid_iban("IT" + iban_check_digits("IT", bban) + bban)


@pytest.mark.parametrize("value", ["DEUTDEFF", "DEUTDEFF500", "exmpitmmxxx"])
def test_valid_bics(value: str) -> None:
    assert is_valid_bic(value)


@pytest.mark.parametrize("value", ["DEUTDEF", "DEUTQQFF", "DEUTDEFF5000", "1EUTDEFF", ""])
def test_invalid_bics(value: str) -> None:
    assert not is_valid_bic(value)


# --- SDI recipient code


@pytest.mark.parametrize(
    ("code", "country"),
    [("ABC1234", "IT"), ("0000000", "IT"), ("XXXXXXX", "DE"), ("abc1234", "IT")],
)
def test_valid_sdi_codes(code: str, country: str) -> None:
    assert sdi_code_error(code, country=country) is None


def test_six_character_codes_are_for_public_administrations_only() -> None:
    error = sdi_code_error("UFABCD", country="IT")
    assert error is not None
    assert "public administration" in error


def test_foreign_code_is_refused_for_italian_vendors() -> None:
    assert sdi_code_error("XXXXXXX", country="IT") == (
        "XXXXXXX is only for vendors established outside Italy."
    )


@pytest.mark.parametrize("code", ["ABC12345", "ABC-123", "ABC12"])
def test_malformed_sdi_codes(code: str) -> None:
    assert sdi_code_error(code, country="IT") == "The SDI recipient code has 7 letters or digits."


# --- Reference tables


def test_italian_provinces_are_the_official_list() -> None:
    assert len(engine.ITALIAN_PROVINCES) == 107
    assert {"RM", "MI", "MB", "BT", "FM", "SU", "AO"} <= engine.ITALIAN_PROVINCES
    assert engine.is_valid_province("OG")  # former Sardinian code, still on registrations
    assert not engine.is_valid_province("EE")  # "abroad" in tax codes, not a province
    assert not engine.is_valid_province("XX")


def test_fiscal_regimes_are_rf01_to_rf20_without_rf03() -> None:
    expected = [f"RF{n:02d}" for n in range(1, 21) if n != 3]
    assert list(engine.FISCAL_REGIMES) == expected
    assert engine.FISCAL_REGIMES["RF01"] == "Ordinario"
    assert engine.FISCAL_REGIMES["RF18"] == "Altro"
    assert engine.FISCAL_REGIMES[engine.FLAT_RATE_REGIME] == (
        "Regime forfettario (art.1, c.54-89, L. 190/2014)"
    )


# VEN.18: the RegimeFiscale labels of the FatturaPA technical specification 1.9.1 (Agenzia delle
# Entrate). A new specification version must update both the version and this list, on purpose.
SPECIFICATION_1_9_1_REGIMES = {
    "RF01": "Ordinario",
    "RF02": "Contribuenti minimi (art.1, c.96-117, L. 244/07)",
    "RF04": "Agricoltura e attività connesse e pesca (artt.34 e 34-bis, DPR 633/72)",
    "RF05": "Vendita sali e tabacchi (art.74, c.1, DPR. 633/72)",
    "RF06": "Commercio fiammiferi (art.74, c.1, DPR 633/72)",
    "RF07": "Editoria (art.74, c.1, DPR 633/72)",
    "RF08": "Gestione servizi telefonia pubblica (art.74, c.1, DPR 633/72)",
    "RF09": "Rivendita documenti di trasporto pubblico e di sosta (art.74, c.1, DPR 633/72)",
    "RF10": (
        "Intrattenimenti, giochi e altre attività di cui alla tariffa allegata al DPR 640/72 "
        "(art.74, c.6, DPR 633/72)"
    ),
    "RF11": "Agenzie viaggi e turismo (art.74-ter, DPR 633/72)",
    "RF12": "Agriturismo (art.5, c.2, L. 413/91)",
    "RF13": "Vendite a domicilio (art.25-bis, c.6, DPR 600/73)",
    "RF14": (
        "Rivendita beni usati, oggetti d'arte, d'antiquariato o da collezione (art.36, DL 41/95)"
    ),
    "RF15": (
        "Agenzie di vendite all'asta di oggetti d'arte, antiquariato o da collezione "
        "(art.40-bis, DL 41/95)"
    ),
    "RF16": "IVA per cassa P.A. (art.6, c.5, DPR 633/72)",
    "RF17": "IVA per cassa (art. 32-bis, DL 83/2012)",
    "RF18": "Altro",
    "RF19": "Regime forfettario (art.1, c.54-89, L. 190/2014)",
    "RF20": "Regime transfrontaliero di Franchigia IVA (Direttiva UE 2020/285)",
}


def test_the_regime_labels_are_pinned_to_the_specification_version() -> None:
    assert engine.FATTURAPA_SPECIFICATION_VERSION == "1.9.1"
    assert dict(engine.FISCAL_REGIMES) == SPECIFICATION_1_9_1_REGIMES


def test_rf20_is_the_cross_border_vat_franchise() -> None:
    assert engine.CROSS_BORDER_FRANCHISE_REGIME == "RF20"
    assert "2020/285" in engine.FISCAL_REGIMES[engine.CROSS_BORDER_FRANCHISE_REGIME]


@pytest.mark.parametrize(("value", "valid"), [("IT01234567897", True), ("DE12", False)])
def test_eori_format(value: str, valid: bool) -> None:
    assert engine.is_valid_eori(value) is valid


@pytest.mark.parametrize(("value", "valid"), [("47.91.10", True), ("47", True), ("4791", False)])
def test_ateco_format(value: str, valid: bool) -> None:
    assert engine.is_valid_ateco(value) is valid


def test_vat_prefix_of_greece_is_el() -> None:
    assert engine.vat_prefix("GR") == "EL"
    assert engine.split_vat_number("EL123456789", "GR") == ("GR", "123456789")
    assert engine.split_vat_number("de 123456789", "DE") == ("DE", "123456789")
    assert engine.split_vat_number(IT_VAT, "IT") == ("IT", IT_VAT)


@pytest.mark.parametrize("value", ["CHE123456789", "che 123 456 789"])
def test_swiss_vat_numbers_keep_che_it_is_not_a_country_prefix(value: str) -> None:
    # The Swiss VAT number is the UID "CHE" + 9 digits: stripping "CH" would leave "E123456789".
    assert engine.split_vat_number(value, "CH") == ("CH", "CHE123456789")
    assert "vat_number" not in identity_errors(LegalIdentity(country="CH", vat_number=value))


def test_eu_prefixes_followed_by_letters_are_still_stripped() -> None:
    assert engine.split_vat_number("ATU12345678", "AT") == ("AT", "U12345678")


def test_mask_iban_keeps_the_country_and_the_last_four_characters() -> None:
    assert mask_iban(IT_IBAN) == "IT" + "*" * 21 + "3456"
    assert mask_iban("") == ""


# --- Identity validation: everything optional, present values valid


def italian_company(**overrides: Any) -> LegalIdentity:
    values: dict[str, Any] = {
        "country": "IT",
        "legal_name": "Vendor A S.r.l.",
        "legal_form": engine.SRL,
        "vat_number": IT_VAT,
        "tax_code": IT_VAT,
        "fiscal_regime": "RF01",
        "registered_street": "Via Example",
        "registered_street_number": "1",
        "registered_postal_code": "00100",
        "registered_city": "Rome",
        "registered_province": "RM",
        "registered_country": "IT",
        "rea_office": "RM",
        "rea_number": "123456",
        "share_capital": Decimal("10000"),
        "sole_shareholder": engine.SOLE_SHAREHOLDER,
        "liquidation_status": engine.NOT_IN_LIQUIDATION,
        "sdi_recipient_code": "ABC1234",
        "pec": "vendor-a@pec.example.com",
        "iban": IT_IBAN,
        "bic": "EXMPITMMXXX",
        "bank_name": "Example Bank",
        "contact_email": "vendor-a@example.com",
        "phone": "+39 06 1234567",
    }
    values.update(overrides)
    return LegalIdentity(**values)


def test_an_empty_identity_has_no_errors() -> None:
    assert identity_errors(LegalIdentity(country="IT")) == {}


def test_a_complete_italian_company_has_no_errors() -> None:
    assert identity_errors(italian_company()) == {}


def test_invalid_italian_values_are_reported_by_field() -> None:
    errors = identity_errors(
        italian_company(
            vat_number="01234567898",
            tax_code="RSSMRA85T10A562T",
            registered_postal_code="0010",
            registered_province="Lazio",
            rea_office="ZZ",
            sdi_recipient_code="XXXXXXX",
            iban="IT60X0542811101000000123457",
            bic="NOTABIC",
            eori="IT1",
            ateco_code="47-91",
            share_capital=Decimal("-1"),
            fiscal_representative_vat_number="123",
        )
    )
    assert set(errors) == {
        "vat_number",
        "tax_code",
        "registered_postal_code",
        "registered_province",
        "rea_office",
        "sdi_recipient_code",
        "iban",
        "bic",
        "eori",
        "ateco_code",
        "share_capital",
        "fiscal_representative_vat_number",
    }
    assert errors["sdi_recipient_code"] == [
        "XXXXXXX is only for vendors established outside Italy."
    ]


def test_foreign_vendors_get_their_own_formats() -> None:
    identity = LegalIdentity(
        country="DE",
        vat_number="DE123456789",
        tax_code="12/345/67890",  # national TIN: free format outside Italy
        registered_postal_code="10115",
        registered_province="Berlin",  # a region, not an Italian province
        sdi_recipient_code="XXXXXXX",
        iban=DE_IBAN,
    )
    assert identity_errors(identity) == {}


def test_normalized_compacts_identifiers() -> None:
    identity = normalized(
        LegalIdentity(
            country="IT",
            vat_number=" it 0123 4567 897 ",
            iban="it60 x054 2811 1010 0000 0123 456",
            bic="exmpitmm",
            sdi_recipient_code="abc1234",
            registered_province="rm",
            pec=" vendor@pec.example.com ",
        )
    )
    assert identity.vat_number == "IT01234567897"
    assert identity.iban == IT_IBAN
    assert identity.bic == "EXMPITMM"
    assert identity.sdi_recipient_code == "ABC1234"
    assert identity.registered_province == "RM"
    assert identity.pec == "vendor@pec.example.com"


# --- Required fields: rules are data, additive


def test_no_rules_means_nothing_is_required() -> None:
    assert missing_required_fields({"country": "IT"}, []) == []


def test_rules_apply_to_their_country_only() -> None:
    rules = [Requirement("pec", country="IT")]
    assert missing_required_fields({"country": "IT"}, rules) == ["pec"]
    assert missing_required_fields({"country": "DE"}, rules) == []


def test_rules_are_additive_in_legal_field_order() -> None:
    rules = [
        Requirement("iban"),  # every country
        Requirement("pec", country="IT"),
        Requirement("rea_number", country="IT", legal_form=engine.SRL),
        Requirement("pec", country="IT"),  # duplicates count once
    ]
    values = {"country": "IT", "legal_form": engine.SRL}
    assert missing_required_fields(values, rules) == ["rea_number", "pec", "iban"]
    sole = {"country": "IT", "legal_form": engine.SOLE_PROPRIETORSHIP}
    assert missing_required_fields(sole, rules) == ["pec", "iban"]


@pytest.mark.parametrize(
    ("country", "expected"),
    [("US", ["tax_code"]), ("CH", ["tax_code"]), ("DE", ["eori"]), ("IT", ["eori"])],
)
def test_region_rules_apply_inside_or_outside_the_eu(country: str, expected: list[str]) -> None:
    """Owner decision: non-EU vendors need their TIN; a region rule covers every such country."""
    rules = [
        Requirement("tax_code", region=engine.NON_EU),
        Requirement("eori", region=engine.EU),
    ]
    assert missing_required_fields({"country": country}, rules) == expected


def test_a_form_specific_rule_needs_the_legal_form() -> None:
    rules = [Requirement("rea_number", country="IT", legal_form=engine.SRL)]
    assert missing_required_fields({"country": "IT", "legal_form": ""}, rules) == ["legal_form"]


def test_rules_for_another_purpose_are_ignored() -> None:
    rules = [Requirement("pec", purpose="einvoicing")]
    assert missing_required_fields({"country": "IT"}, rules) == []
    assert missing_required_fields({"country": "IT"}, rules, purpose="einvoicing") == ["pec"]


def test_blank_values_count_as_missing() -> None:
    rules = [Requirement("pec"), Requirement("share_capital"), Requirement("iban")]
    values = {"country": "IT", "pec": "   ", "share_capital": Decimal("0"), "iban": IT_IBAN}
    assert missing_required_fields(values, rules) == ["pec"]


@pytest.mark.parametrize(
    ("code", "pec", "country", "filled"),
    [
        ("ABC1234", "", "IT", True),
        (engine.SDI_PEC_CODE, "vendor@pec.example.com", "IT", True),
        (engine.SDI_PEC_CODE, "", "IT", False),  # 0000000 needs a PEC
        ("", "vendor@pec.example.com", "IT", False),  # the code itself is mandatory
        (engine.SDI_FOREIGN_CODE, "", "IT", False),
        (engine.SDI_FOREIGN_CODE, "", "DE", True),
        ("UFABCD", "", "IT", False),  # public administration office code
    ],
)
def test_the_sdi_rule_is_the_delivery_channel(
    code: str, pec: str, country: str, filled: bool
) -> None:
    rules = [Requirement("sdi_recipient_code")]
    values = {"country": country, "sdi_recipient_code": code, "pec": pec}
    assert missing_required_fields(values, rules) == ([] if filled else ["sdi_recipient_code"])


def test_a_rule_on_an_unknown_field_is_refused() -> None:
    with pytest.raises(UnknownLegalField):
        required_fields({"country": "IT"}, [Requirement("password")])


def test_completeness_reports_invalid_required_values() -> None:
    identity = italian_company(iban="IT60X0542811101000000123457", pec="")
    rules = [Requirement("iban"), Requirement("pec"), Requirement("bic")]
    result = completeness(identity, rules)
    assert result == Completeness(missing=("pec",), invalid=("iban",))
    assert not result.complete
    assert completeness(italian_company(), rules).complete


def test_payout_not_allowed_lists_the_fields() -> None:
    error = PayoutNotAllowed("Vendor A", Completeness(missing=("pec", "iban"), invalid=("bic",)))
    assert error.missing == ("pec", "iban")
    assert str(error) == (
        "Vendor A cannot request a payout until its legal profile is complete: "
        "missing pec, iban; invalid bic."
    )


# --- FatturaPA 1.2.x party block


def test_italian_company_fills_the_cedente_prestatore_block() -> None:
    assert fatturapa_party(italian_company(), CEDENTE) == {
        "DatiAnagrafici": {
            "IdFiscaleIVA": {"IdPaese": "IT", "IdCodice": IT_VAT},
            "CodiceFiscale": IT_VAT,
            "Anagrafica": {"Denominazione": "Vendor A S.r.l."},
            "RegimeFiscale": "RF01",
        },
        "Sede": {
            "Indirizzo": "Via Example",
            "NumeroCivico": "1",
            "CAP": "00100",
            "Comune": "Rome",
            "Provincia": "RM",
            "Nazione": "IT",
        },
        "IscrizioneREA": {
            "Ufficio": "RM",
            "NumeroREA": "123456",
            "CapitaleSociale": "10000.00",
            "SocioUnico": "SU",
            "StatoLiquidazione": "LN",
        },
        "Contatti": {"Telefono": "+39061234567", "Email": "vendor-a@example.com"},
    }


def test_sole_proprietor_uses_first_and_last_name_and_no_rea() -> None:
    identity = italian_company(
        legal_form=engine.SOLE_PROPRIETORSHIP,
        legal_name="Ada Example",
        first_name="Ada",
        last_name="Example",
        tax_code=CF,
        fiscal_regime=engine.FLAT_RATE_REGIME,
        rea_office="",
        rea_number="",
        share_capital=None,
        sole_shareholder="",
        liquidation_status="",
    )
    party = fatturapa_party(identity)
    assert party["DatiAnagrafici"] == {
        "IdFiscaleIVA": {"IdPaese": "IT", "IdCodice": IT_VAT},
        "CodiceFiscale": CF,
        "Anagrafica": {"Nome": "Ada", "Cognome": "Example"},
        "RegimeFiscale": "RF19",
    }
    assert "IscrizioneREA" not in party


def test_a_cross_border_franchise_vendor_declares_rf20() -> None:
    party = fatturapa_party(italian_company(fiscal_regime=engine.CROSS_BORDER_FRANCHISE_REGIME))
    assert party["DatiAnagrafici"]["RegimeFiscale"] == "RF20"


def test_a_missing_regime_names_the_whole_range() -> None:
    with pytest.raises(PartyBlockError) as exc:
        fatturapa_party(italian_company(fiscal_regime=""))
    assert "fiscal_regime: RegimeFiscale is mandatory (RF01-RF20)" in exc.value.problems


def test_vat_prefix_is_split_into_id_paese_and_id_codice() -> None:
    party = fatturapa_party(italian_company(vat_number="IT" + IT_VAT))
    assert party["DatiAnagrafici"]["IdFiscaleIVA"] == {"IdPaese": "IT", "IdCodice": IT_VAT}


def test_foreign_vendor_block() -> None:
    identity = LegalIdentity(
        country="DE",
        legal_name="Vendor D GmbH",
        legal_form=engine.OTHER_LEGAL_FORM,
        vat_number="DE123456789",
        tax_code="12/345/67890",
        fiscal_regime="RF18",
        eori="DE1234567890123",
        registered_street="Example Strasse",
        registered_street_number="1",
        registered_postal_code="D-10115",
        registered_city="Berlin",
        registered_province="Berlin",
        registered_country="DE",
    )
    party = fatturapa_party(identity)
    assert party["DatiAnagrafici"] == {
        "IdFiscaleIVA": {"IdPaese": "DE", "IdCodice": "123456789"},
        "Anagrafica": {"Denominazione": "Vendor D GmbH", "CodEORI": "DE1234567890123"},
        "RegimeFiscale": "RF18",
    }  # no CodiceFiscale: a foreign TIN is not an Italian codice fiscale
    assert party["Sede"] == {
        "Indirizzo": "Example Strasse",
        "NumeroCivico": "1",
        "CAP": "00000",  # FatturaPA: foreign postal codes are not 5 digits
        "Comune": "Berlin",
        "Nazione": "DE",  # no Provincia outside Italy
    }


def test_permanent_establishment_fills_stabile_organizzazione() -> None:
    identity = dataclasses.replace(
        italian_company(country="CH", registered_country="CH", registered_province=""),
        vat_number="CHE123456789",
        tax_code="",
        registered_postal_code="8001",
        establishment_street="Via Stabile",
        establishment_postal_code="20100",
        establishment_city="Milan",
        establishment_province="MI",
        establishment_country="IT",
    )
    party = fatturapa_party(identity)
    assert party["StabileOrganizzazione"] == {
        "Indirizzo": "Via Stabile",
        "CAP": "20100",
        "Comune": "Milan",
        "Provincia": "MI",
        "Nazione": "IT",
    }


def test_missing_mandatory_values_are_all_reported() -> None:
    identity = LegalIdentity(country="IT", legal_name="Vendor A S.r.l.")
    with pytest.raises(PartyBlockError) as excinfo:
        fatturapa_party(identity, CEDENTE)
    assert excinfo.value.problems == (
        "vat_number: IdFiscaleIVA is mandatory for the CedentePrestatore",
        "fiscal_regime: RegimeFiscale is mandatory (RF01-RF20)",
        "registered_street: Indirizzo is mandatory",
        "registered_postal_code: CAP is mandatory",
        "registered_city: Comune is mandatory",
    )


def test_rea_needs_the_liquidation_status() -> None:
    with pytest.raises(PartyBlockError) as excinfo:
        fatturapa_party(italian_company(liquidation_status=""))
    assert excinfo.value.problems == ("liquidation_status: StatoLiquidazione is mandatory",)


def test_values_beyond_fatturapa_lengths_are_refused_not_truncated() -> None:
    identity = italian_company(legal_name="X" * 81, registered_street="Via " + "Y" * 60)
    with pytest.raises(PartyBlockError) as excinfo:
        fatturapa_party(identity)
    assert excinfo.value.problems == (
        "legal_name: Denominazione takes 1-80 characters",
        "registered_street: Indirizzo takes 1-60 characters",
    )


def test_optional_contacts_outside_the_lengths_are_left_out() -> None:
    party = fatturapa_party(italian_company(phone="+39 06 1234 5678 90"))
    assert party["Contatti"] == {"Email": "vendor-a@example.com"}


def test_cessionario_block_has_no_regime_rea_or_contacts() -> None:
    party = fatturapa_party(italian_company(), CESSIONARIO)
    assert set(party) == {"DatiAnagrafici", "Sede"}
    assert "RegimeFiscale" not in party["DatiAnagrafici"]


def non_eu_without_vat(**overrides: Any) -> LegalIdentity:
    values: dict[str, Any] = {
        "country": "US",
        "legal_name": "Vendor E Inc.",
        "legal_form": engine.OTHER_LEGAL_FORM,
        "tax_code": "12-3456789",
        "tax_residence_country": "US",
        "registered_street": "Example Street",
        "registered_street_number": "1",
        "registered_postal_code": "94105",
        "registered_city": "San Francisco",
        "registered_country": "US",
        "iban": GB_IBAN,
    }
    values.update(overrides)
    return LegalIdentity(**values)


def test_a_non_eu_cessionario_without_a_vat_number_is_identified_by_its_tin() -> None:
    party = fatturapa_party(non_eu_without_vat(), CESSIONARIO)
    assert party["DatiAnagrafici"]["IdFiscaleIVA"] == {"IdPaese": "US", "IdCodice": "12-3456789"}
    assert "CodiceFiscale" not in party["DatiAnagrafici"]
    assert party["Sede"]["Nazione"] == "US"


# --- DAC7 dataset (spec 11 CMP.1): part of the payout requirements for every vendor


def test_a_complete_identity_has_the_whole_dac7_dataset() -> None:
    assert engine.dac7_missing(italian_company(tax_residence_country="IT")) == ()
    assert engine.dac7_missing(non_eu_without_vat()) == ()


def test_the_dac7_dataset_lists_what_is_missing() -> None:
    assert engine.dac7_missing(LegalIdentity(country="US", legal_name="Vendor E Inc.")) == (
        "vat_number or tax_code",
        "tax_residence_country",
        "registered_street",
        "registered_postal_code",
        "registered_city",
        "registered_country",
        "iban",
    )


def test_italian_dac7_addresses_need_the_province() -> None:
    identity = italian_company(tax_residence_country="IT", registered_province="")
    assert engine.dac7_missing(identity) == ("registered_province",)


def test_dac7_needs_a_vat_number_or_a_tin() -> None:
    assert engine.dac7_missing(italian_company(tax_residence_country="IT", vat_number="")) == ()
    identity = italian_company(tax_residence_country="IT", vat_number="", tax_code="")
    assert engine.dac7_missing(identity) == ("vat_number or tax_code",)


def test_cessionario_needs_a_vat_number_or_a_codice_fiscale() -> None:
    party = fatturapa_party(italian_company(vat_number=""), CESSIONARIO)
    assert party["DatiAnagrafici"]["CodiceFiscale"] == IT_VAT
    with pytest.raises(PartyBlockError) as excinfo:
        fatturapa_party(italian_company(vat_number="", tax_code=""), CESSIONARIO)
    assert excinfo.value.problems == (
        "vat_number or tax_code: IdFiscaleIVA or CodiceFiscale is mandatory",
    )


def test_non_eu_vendor_fiscal_representative() -> None:
    rep_vat = vat("0765432100")
    identity = LegalIdentity(
        country="CH",
        legal_name="Vendor C AG",
        vat_number="CHE123456789",
        fiscal_regime="RF18",
        registered_street="Example Strasse",
        registered_postal_code="8001",
        registered_city="Zurich",
        registered_country="CH",
        fiscal_representative_name="Representative Example S.r.l.",
        fiscal_representative_vat_number="IT" + rep_vat,
    )
    assert fatturapa_fiscal_representative(identity) == {
        "DatiAnagrafici": {
            "IdFiscaleIVA": {"IdPaese": "IT", "IdCodice": rep_vat},
            "Anagrafica": {"Denominazione": "Representative Example S.r.l."},
        }
    }
    cessionario = fatturapa_party(identity, CESSIONARIO)
    assert cessionario["RappresentanteFiscale"] == {
        "IdFiscaleIVA": {"IdPaese": "IT", "IdCodice": rep_vat},
        "Denominazione": "Representative Example S.r.l.",
    }
    assert fatturapa_fiscal_representative(LegalIdentity(country="IT")) is None
    assert cessionario["DatiAnagrafici"]["IdFiscaleIVA"] == {
        "IdPaese": "CH",
        "IdCodice": "CHE123456789",
    }
    cedente = fatturapa_party(identity, CEDENTE)
    assert cedente["DatiAnagrafici"]["IdFiscaleIVA"] == {
        "IdPaese": "CH",
        "IdCodice": "CHE123456789",
    }


def test_invalid_identifiers_are_reported_not_emitted() -> None:
    identity = italian_company(
        vat_number="12345678901",  # 11 digits, wrong check digit
        tax_code="RSSMRA85T10A562T",  # wrong check character
        eori="IT12345678901-",
        registered_province="ZZ",
        rea_office="ZZ",
        sole_shareholder="XX",
        share_capital=Decimal("-1"),
    )
    with pytest.raises(PartyBlockError) as excinfo:
        fatturapa_party(identity, CEDENTE)
    fields = {problem.split(":", 1)[0] for problem in excinfo.value.problems}
    assert {
        "vat_number",
        "tax_code",
        "eori",
        "registered_province",
        "rea_office",
        "sole_shareholder",
        "share_capital",
    } <= fields
    assert (
        "vat_number: Not a valid Italian VAT number (11 digits with a check digit)."
        in excinfo.value.problems
    )


def test_an_invalid_fiscal_representative_vat_number_is_reported() -> None:
    identity = LegalIdentity(
        country="CH",
        legal_name="Vendor C AG",
        vat_number="CHE123456789",
        registered_street="Example Strasse",
        registered_postal_code="8001",
        registered_city="Zurich",
        fiscal_representative_name="Representative Example S.r.l.",
        fiscal_representative_vat_number="12345678901",
    )
    problem = (
        "fiscal_representative_vat_number: "
        "The fiscal representative needs a valid Italian VAT number."
    )
    with pytest.raises(PartyBlockError) as excinfo:
        fatturapa_party(identity, CESSIONARIO)
    assert excinfo.value.problems == (problem,)
    with pytest.raises(PartyBlockError) as excinfo:
        fatturapa_fiscal_representative(identity)
    assert excinfo.value.problems == (problem,)


def test_transmission_recipient() -> None:
    assert fatturapa_transmission(italian_company()) == {"CodiceDestinatario": "ABC1234"}
    by_pec = italian_company(sdi_recipient_code="0000000")
    assert fatturapa_transmission(by_pec) == {
        "CodiceDestinatario": "0000000",
        "PECDestinatario": "vendor-a@pec.example.com",
    }
    with pytest.raises(PartyBlockError):
        fatturapa_transmission(italian_company(sdi_recipient_code="0000000", pec=""))


def test_unknown_party_role_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown FatturaPA party role"):
        fatturapa_party(italian_company(), "Committente")
