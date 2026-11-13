"""VEN.12: the vendor legal identity against the real database — validation, VIES status,
staff-editable requirements, payout guard and FatturaPA party block.

Done when: a vendor's data fills a valid FatturaPA party block; a vendor missing a required
field for its country cannot request a payout, but can open and edit its profile.
"""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors import engine
from apps.vendors.models import LegalFieldRequirement, Vendor
from apps.vendors.services import (
    assert_payout_allowed,
    change_status,
    check_vat_number,
    delete_requirement,
    legal_profile_completeness,
    save_requirement,
    save_vendor,
)
from apps.vendors.tests.factories import (
    ContractAcceptanceFactory,
    LegalFieldRequirementFactory,
    VendorFactory,
    italian_vat,
)
from apps.vendors.tests.fakes import (
    FakeVatNumberValidator,
    clear_activation_requirements,
    seed_legal_requirements,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="127.0.0.1")
TRAITS = ("italian_company", "italian_sole_proprietor", "eu_company", "non_eu_company")


@pytest.fixture
def seeded() -> None:
    seed_legal_requirements()


def bare_vendor(**fields: object) -> Vendor:
    """A vendor with no legal identity beyond its legal name and country."""
    return VendorFactory.create(vat_number="", **fields)


# --- Every legal field is optional; present values are validated


def test_a_vendor_with_no_legal_data_is_valid_and_saves() -> None:
    vendor = bare_vendor()
    vendor.full_clean()  # no ValidationError: the profile may stay incomplete
    vendor.save()
    assert vendor.vies_status == Vendor.ViesStatus.UNCHECKED


@pytest.mark.parametrize("trait", [*TRAITS, "non_eu_without_vat"])
def test_factory_traits_build_valid_identities(trait: str) -> None:
    vendor = VendorFactory.create(**{trait: True})
    vendor.full_clean()


def test_invalid_values_are_refused_by_field() -> None:
    vendor = VendorFactory.build(
        italian_company=True,
        vat_number="01234567898",
        tax_code="01234567897",
        iban="IT60X0542811101000000123457",
        sdi_recipient_code="UFABCD",
        registered_province="Lazio",
    )
    with pytest.raises(ValidationError) as excinfo:
        vendor.full_clean()
    assert set(excinfo.value.message_dict) == {
        "vat_number",
        "iban",
        "sdi_recipient_code",
        "registered_province",
    }


def test_identifiers_are_stored_compact() -> None:
    vendor = VendorFactory.build(
        italian_company=True, iban="it60 x054 2811 1010 0000 0123 456", bic="exmpitmm"
    )
    vendor.full_clean()
    assert vendor.iban == "IT60X0542811101000000123456"
    assert vendor.bic == "EXMPITMM"


def test_fiscal_regime_and_legal_form_outside_the_choices_are_refused_by_the_database() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorFactory.create(fiscal_regime="RF03")
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorFactory.create(legal_form="trust")


# --- VIES status behind our own interface


def test_check_vat_number_stores_and_audits_the_vies_answer() -> None:
    vendor = VendorFactory.create(eu_company=True)
    fake = FakeVatNumberValidator(default=engine.VIES_VALID)
    changes = check_vat_number(vendor, fake, ctx=CTX)

    assert fake.calls == [("DE", vendor.vat_number.removeprefix("DE"))]
    assert changes.summary()["update"] == 1
    vendor.refresh_from_db()
    assert vendor.vies_status == Vendor.ViesStatus.VALID
    assert vendor.vies_checked_at is not None
    entry = AuditLog.objects.get(action="vendor.vat_checked")
    assert entry.after is not None
    assert entry.after["vies_status"] == "valid"
    assert entry.before is not None
    assert entry.before["vies_status"] == "unchecked"


def test_greek_vat_numbers_are_checked_with_the_el_prefix() -> None:
    vendor = VendorFactory.create(country="GR", vat_number="EL123456789")
    fake = FakeVatNumberValidator()
    check_vat_number(vendor, fake, ctx=CTX)
    assert fake.calls == [("EL", "123456789")]


def test_check_vat_number_dry_run_changes_nothing() -> None:
    vendor = VendorFactory.create(eu_company=True)
    changes = check_vat_number(
        vendor, FakeVatNumberValidator(default=engine.VIES_INVALID), ctx=CTX, dry_run=True
    )
    assert changes.changes[0].after is not None
    assert changes.changes[0].after["vies_status"] == "invalid"
    vendor.refresh_from_db()
    assert vendor.vies_status == Vendor.ViesStatus.UNCHECKED
    assert AuditLog.objects.count() == 0


def test_an_unavailable_vies_is_recorded_as_such() -> None:
    vendor = VendorFactory.create(eu_company=True)
    check_vat_number(vendor, FakeVatNumberValidator(default=engine.VIES_UNAVAILABLE), ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.vies_status == Vendor.ViesStatus.UNAVAILABLE


def test_check_vat_number_needs_a_vat_number() -> None:
    with pytest.raises(ValueError, match="no VAT number"):
        check_vat_number(bare_vendor(), FakeVatNumberValidator(), ctx=CTX)


def test_changing_the_vat_number_resets_the_vies_status() -> None:
    vendor = VendorFactory.create(eu_company=True)
    check_vat_number(vendor, FakeVatNumberValidator(), ctx=CTX)
    vendor = Vendor.objects.get(pk=vendor.pk)
    vendor.phone = "+49 30 123456"
    vendor.save()
    vendor.refresh_from_db()
    assert vendor.vies_status == Vendor.ViesStatus.VALID  # other edits keep it

    vendor.vat_number = "DE999999999"
    vendor.save()
    vendor.refresh_from_db()
    assert vendor.vies_status == Vendor.ViesStatus.UNCHECKED
    assert vendor.vies_checked_at is None


# --- Staff-editable requirements, seeded from the owner decision


def test_seeded_requirements_match_the_owner_decision(seeded: None) -> None:
    rows = set(
        LegalFieldRequirement.objects.filter(active=True, purpose="payout").values_list(
            "country", "region", "legal_form", "field_name"
        )
    )
    italy = {("IT", "", "", field) for field in ("pec", "tax_code", "fiscal_regime")}
    italy |= {("IT", "", "", "sdi_recipient_code"), ("IT", "", "", "registered_province")}
    assert italy <= rows
    for form in ("partnership", "srl", "spa", "cooperative"):
        assert {("IT", "", form, "rea_office"), ("IT", "", form, "rea_number")} <= rows
    # Sole proprietors: only the DAC7 date of birth (VEN.23).
    assert {(c, r, f, n) for c, r, f, n in rows if f == "sole_proprietorship"} == {
        ("", "", "sole_proprietorship", "date_of_birth")
    }
    for country in ("IT", "DE", "FR", "GR", "SE"):
        assert {(country, "", "", "vat_number"), (country, "", "", "iban")} <= rows
    # Non-EU vendors: IBAN through a rule for the region. Their TIN (or, where none is issued,
    # the place of birth) is enforced by the DAC7 check (VEN.23), not by a row.
    assert ("", "non_eu", "", "iban") in rows
    assert ("", "non_eu", "", "tax_code") not in rows
    # DAC7 dataset, every vendor: registered office address and country of tax residence.
    dac7 = {
        ("", "", "", field)
        for field in (
            "registered_street",
            "registered_postal_code",
            "registered_city",
            "registered_country",
            "tax_residence_country",
            "tin_country",
        )
    }
    assert dac7 <= rows
    assert not any(country in ("CH", "US", "GB") for country, _, _, _ in rows)


@pytest.mark.parametrize("trait", TRAITS)
def test_complete_traits_may_request_a_payout(seeded: None, trait: str) -> None:
    vendor = VendorFactory.create(**{trait: True})
    assert legal_profile_completeness(vendor).complete
    assert_payout_allowed(vendor)  # does not raise


def test_an_italian_vendor_missing_required_fields_cannot_request_a_payout(seeded: None) -> None:
    vendor = VendorFactory.create(italian_company=True, iban="", pec="", rea_number="")
    result = legal_profile_completeness(vendor)
    assert result.missing == ("rea_number", "pec", "iban")
    with pytest.raises(engine.PayoutNotAllowed) as excinfo:
        assert_payout_allowed(vendor)
    assert excinfo.value.missing == ("rea_number", "pec", "iban")
    assert "missing rea_number, pec, iban" in str(excinfo.value)


def test_an_italian_vendor_without_legal_data_lists_every_requirement(seeded: None) -> None:
    vendor = bare_vendor(country="IT", legal_form=Vendor.LegalForm.SRL)
    assert legal_profile_completeness(vendor).missing == (
        "vat_number",
        "tax_code",
        "tin_country",
        "tax_residence_country",
        "fiscal_regime",
        "business_registration_number",
        "registered_street",
        "registered_postal_code",
        "registered_city",
        "registered_province",
        "registered_country",
        "rea_office",
        "rea_number",
        "liquidation_status",
        "sdi_recipient_code",
        "pec",
        "iban",
    )


def test_sole_proprietors_do_not_need_the_rea(seeded: None) -> None:
    vendor = VendorFactory.create(italian_sole_proprietor=True)
    assert vendor.rea_number == ""
    assert legal_profile_completeness(vendor).complete


def test_the_pec_delivery_channel_needs_a_pec(seeded: None) -> None:
    vendor = VendorFactory.create(italian_sole_proprietor=True)
    Vendor.objects.filter(pk=vendor.pk).update(pec="")
    with pytest.raises(engine.PayoutNotAllowed) as excinfo:
        assert_payout_allowed(vendor)
    assert excinfo.value.missing == ("sdi_recipient_code", "pec")


def test_a_required_field_with_an_invalid_value_blocks_the_payout(seeded: None) -> None:
    vendor = VendorFactory.create(italian_company=True)
    # Legacy data written around the form validation, e.g. by the migration import.
    Vendor.objects.filter(pk=vendor.pk).update(iban="IT60X0542811101000000123457")
    with pytest.raises(engine.PayoutNotAllowed) as excinfo:
        assert_payout_allowed(vendor)
    assert excinfo.value.invalid == ("iban",)


def test_the_guard_checks_stored_data_not_unsaved_edits(seeded: None) -> None:
    vendor = VendorFactory.create(italian_company=True, iban="")
    vendor.iban = "IT60X0542811101000000123456"  # not saved
    with pytest.raises(engine.PayoutNotAllowed):
        assert_payout_allowed(vendor)


def test_a_non_eu_vendor_needs_its_iban_tin_and_the_dac7_dataset(seeded: None) -> None:
    assert legal_profile_completeness(bare_vendor(country="US")).missing == (
        "legal_form",
        "tax_code",
        "tin_country",
        "tax_residence_country",
        "registered_street",
        "registered_postal_code",
        "registered_city",
        "registered_country",
        "iban",
    )


def test_an_eu_vendor_needs_its_vat_number_iban_and_the_dac7_dataset(seeded: None) -> None:
    assert legal_profile_completeness(bare_vendor(country="DE")).missing == (
        "legal_form",
        "vat_number",
        "tax_code",
        "tin_country",
        "tax_residence_country",
        "registered_street",
        "registered_postal_code",
        "registered_city",
        "registered_country",
        "iban",
    )


DAC7_GATE_FIELDS = (
    "tax_residence_country",
    "registered_street",
    "registered_postal_code",
    "registered_city",
    "registered_country",
    "iban",
)


@pytest.mark.parametrize("field", DAC7_GATE_FIELDS)
@pytest.mark.parametrize("trait", TRAITS)
def test_a_vendor_without_a_dac7_field_cannot_request_a_payout(
    seeded: None, trait: str, field: str
) -> None:
    vendor = VendorFactory.create(**{trait: True})
    Vendor.objects.filter(pk=vendor.pk).update(**{field: ""})
    with pytest.raises(engine.PayoutNotAllowed) as excinfo:
        assert_payout_allowed(vendor)
    assert field in excinfo.value.missing


@pytest.mark.parametrize("trait", TRAITS)
def test_a_vendor_without_vat_number_and_tin_cannot_request_a_payout(
    seeded: None, trait: str
) -> None:
    vendor = VendorFactory.create(**{trait: True})
    Vendor.objects.filter(pk=vendor.pk).update(vat_number="", tax_code="")
    with pytest.raises(engine.PayoutNotAllowed):
        assert_payout_allowed(vendor)


def test_an_italian_vendor_without_the_province_cannot_request_a_payout(seeded: None) -> None:
    vendor = VendorFactory.create(italian_company=True)
    Vendor.objects.filter(pk=vendor.pk).update(registered_province="")
    with pytest.raises(engine.PayoutNotAllowed) as excinfo:
        assert_payout_allowed(vendor)
    assert excinfo.value.missing == ("registered_province",)


def payout_ready_vendors() -> list[Vendor]:
    """Every factory trait, plus a non-EU vendor with a TIN and no VAT number."""
    vendors = [VendorFactory.create(**{trait: True}) for trait in TRAITS]
    vendors.append(VendorFactory.create(non_eu_without_vat=True))
    return vendors


def test_a_vendor_allowed_a_payout_has_the_dac7_dataset_and_a_party_block(seeded: None) -> None:
    """Owner decision: the payout gate includes DAC7 and FatturaPA completeness for everyone."""
    for vendor in payout_ready_vendors():
        assert_payout_allowed(vendor)  # does not raise
        identity = Vendor.objects.get(pk=vendor.pk).legal_identity()
        assert engine.dac7_missing(identity, engine.SEEDED_NO_TIN_COUNTRIES) == (), vendor.country
        engine.fatturapa_party(identity, engine.CESSIONARIO)


def test_staff_rules_add_and_deactivated_rules_relax(seeded: None) -> None:
    vendor = VendorFactory.create(italian_company=True)
    rule = LegalFieldRequirementFactory.create(country="IT", field_name="ateco_code")
    assert legal_profile_completeness(vendor).missing == ("ateco_code",)

    rule.active = False
    rule.save()
    LegalFieldRequirement.objects.filter(country="IT", field_name="pec").update(active=False)
    Vendor.objects.filter(pk=vendor.pk).update(pec="")
    assert legal_profile_completeness(Vendor.objects.get(pk=vendor.pk)).complete


def test_completeness_never_blocks_status_transitions(seeded: None) -> None:
    clear_activation_requirements()  # CMP.25 gates activation with its own rows
    vendor = bare_vendor(country="IT")
    ContractAcceptanceFactory.create(vendor=vendor)  # the contract and the self-certification
    for target in ("onboarding", "active"):
        change_status(vendor, target, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ACTIVE
    assert not legal_profile_completeness(vendor).complete


# --- Requirement rows: constraints and audited changes


def test_a_requirement_is_unique_per_scope_field_and_purpose() -> None:
    LegalFieldRequirementFactory.create(country="FR", field_name="eori")
    with pytest.raises(IntegrityError), transaction.atomic():
        LegalFieldRequirement.objects.create(country="FR", field_name="eori")


def test_a_requirement_must_name_a_legal_field() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        LegalFieldRequirement.objects.create(field_name="password")
    with pytest.raises(ValidationError):
        LegalFieldRequirementFactory.build(field_name="contact_email").full_clean()


def test_requirement_changes_are_audited() -> None:
    rule = LegalFieldRequirement(country="FR", field_name="eori", note="Customs.")
    save_requirement(rule, ctx=CTX)
    created = AuditLog.objects.get(action="vendor.legal_requirement_saved")
    assert created.after == {
        "country": "FR",
        "region": "",
        "legal_form": "",
        "field_name": "eori",
        "purpose": "payout",
        "active": True,
        "note": "Customs.",
    }

    rule.active = False
    save_requirement(rule, ctx=CTX)
    updated = AuditLog.objects.filter(action="vendor.legal_requirement_saved").latest("id")
    assert updated.before is not None
    assert updated.after is not None
    assert (updated.before["active"], updated.after["active"]) == (True, False)

    delete_requirement(rule, ctx=CTX)
    deleted = AuditLog.objects.get(action="vendor.legal_requirement_deleted")
    assert deleted.before is not None
    assert deleted.before["field_name"] == "eori"
    assert not LegalFieldRequirement.objects.filter(country="FR", field_name="eori").exists()


def test_requirement_dry_run_writes_nothing() -> None:
    rule = LegalFieldRequirement(country="FR", field_name="eori")
    changes = save_requirement(rule, ctx=CTX, dry_run=True)
    assert changes.summary()["create"] == 1
    assert not LegalFieldRequirement.objects.filter(country="FR", field_name="eori").exists()
    assert AuditLog.objects.count() == 0


# --- Saving the profile: audited, IBAN masked


def test_saving_legal_changes_writes_an_audit_row_with_the_iban_masked() -> None:
    vendor = VendorFactory.create(italian_company=True)
    old_iban = vendor.iban
    vendor.iban = "IT60X0542811101000000123456"
    vendor.share_capital = Decimal("20000.00")
    save_vendor(vendor, ctx=CTX)

    entry = AuditLog.objects.get(action="vendor.updated")
    assert entry.target_key == str(vendor.public_id)
    assert entry.before is not None
    assert entry.before["iban"] == engine.mask_iban(old_iban)
    assert entry.after is not None
    assert entry.after["iban"] == engine.mask_iban(vendor.iban)
    assert entry.after["share_capital"] == "20000.00"
    assert vendor.iban not in str(entry.before) + str(entry.after)


def test_saving_refuses_present_but_invalid_values() -> None:
    vendor = VendorFactory.create(italian_company=True)
    stored_vat = vendor.vat_number
    vendor.vat_number = "12345678901"  # wrong check digit
    vendor.iban = "IT60X0542811101000000123457"  # wrong checksum
    for dry_run in (True, False):
        with pytest.raises(ValidationError) as excinfo:
            save_vendor(vendor, ctx=CTX, dry_run=dry_run)
        assert set(excinfo.value.message_dict) == {"vat_number", "iban"}
    assert Vendor.objects.get(pk=vendor.pk).vat_number == stored_vat
    assert AuditLog.objects.count() == 0


def test_saving_accepts_an_incomplete_profile_and_stores_identifiers_compact() -> None:
    vendor = bare_vendor(country="IT")
    vendor.iban = "it60 x054 2811 1010 0000 0123 456"
    save_vendor(vendor, ctx=CTX)
    assert Vendor.objects.get(pk=vendor.pk).iban == "IT60X0542811101000000123456"


def test_saving_without_changes_writes_no_audit_row() -> None:
    vendor = VendorFactory.create(italian_company=True)
    save_vendor(Vendor.objects.get(pk=vendor.pk), ctx=CTX)
    assert AuditLog.objects.count() == 0


def test_an_iban_replaced_with_the_same_mask_is_still_audited() -> None:
    vendor = VendorFactory.create(italian_company=True)
    first = vendor.iban
    # Same country and last four characters, different account.
    bban = "X0300003201" + first[-12:]
    vendor.iban = "IT" + engine.iban_check_digits("IT", bban) + bban
    assert engine.mask_iban(vendor.iban) == engine.mask_iban(first)
    save_vendor(vendor, ctx=CTX)
    entry = AuditLog.objects.get(action="vendor.updated")
    assert entry.after is not None
    assert entry.after["iban"].endswith("(replaced)")


# --- Done when: a vendor's data fills a valid FatturaPA party block


# FatturaPA IdCodice of each trait's VAT number: an EU prefix moves to IdPaese, while the Swiss
# "CHE" is part of the number.
ID_CODICE = {
    "italian_company": lambda vat: vat,
    "italian_sole_proprietor": lambda vat: vat,
    "eu_company": lambda vat: vat.removeprefix("DE"),
    "non_eu_company": lambda vat: vat,
}


def test_the_swiss_trait_keeps_che_in_id_codice() -> None:
    vendor = VendorFactory.create(non_eu_company=True)
    assert vendor.vat_number.startswith("CHE")
    cedente = engine.fatturapa_party(vendor.legal_identity())
    assert cedente["DatiAnagrafici"]["IdFiscaleIVA"]["IdCodice"] == vendor.vat_number


@pytest.mark.parametrize("trait", TRAITS)
def test_vendor_data_fills_a_valid_fatturapa_party_block(trait: str) -> None:
    vendor = VendorFactory.create(**{trait: True})
    identity = Vendor.objects.get(pk=vendor.pk).legal_identity()
    cedente = engine.fatturapa_party(identity, engine.CEDENTE)
    assert cedente["DatiAnagrafici"]["IdFiscaleIVA"] == {
        "IdPaese": vendor.country,
        "IdCodice": ID_CODICE[trait](vendor.vat_number),
    }
    assert cedente["DatiAnagrafici"]["RegimeFiscale"] == vendor.fiscal_regime
    assert cedente["Sede"]["Nazione"] == vendor.country
    assert len(cedente["Sede"]["CAP"]) == 5
    engine.fatturapa_party(identity, engine.CESSIONARIO)
    engine.fatturapa_transmission(identity)


def test_italian_company_block_carries_rea_and_codice_fiscale() -> None:
    vendor = VendorFactory.create(italian_company=True)
    cedente = engine.fatturapa_party(vendor.legal_identity())
    assert cedente["DatiAnagrafici"]["CodiceFiscale"] == vendor.tax_code
    assert cedente["IscrizioneREA"]["Ufficio"] == "RM"
    assert cedente["IscrizioneREA"]["CapitaleSociale"] == "10000.00"


def test_non_eu_company_has_an_italian_fiscal_representative() -> None:
    vendor = VendorFactory.create(non_eu_company=True)
    representative = engine.fatturapa_fiscal_representative(vendor.legal_identity())
    assert representative is not None
    assert representative["DatiAnagrafici"]["IdFiscaleIVA"]["IdPaese"] == "IT"
    assert engine.is_valid_italian_vat(vendor.fiscal_representative_vat_number)


def test_generated_identifiers_are_checksum_valid() -> None:
    assert engine.is_valid_italian_vat(italian_vat(42))
    vendor = VendorFactory.create(italian_sole_proprietor=True)
    assert engine.is_valid_codice_fiscale(vendor.tax_code)
    assert len(vendor.tax_code) == 16
    assert engine.is_valid_iban(vendor.iban)
