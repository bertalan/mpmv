"""TAX.1: audited services for the versioned tax configuration and the VIES result cache."""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.tests.factories import utc
from apps.tax import engine, services
from apps.tax.models import (
    TaxRate,
    Territory,
    VatValidation,
    VendorTaxProfile,
    VendorTaxProfileTranslation,
)
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

CTX = AuditContext(actor_id=1, actor_label="staff:ops", source="admin", ip="127.0.0.1")


def approver(**values: Any) -> User:
    """Platform staff allowed to change a vendor's VAT regime (the TAX.2 review permission)."""
    values.setdefault("permissions", [services.REVIEW_PERMISSION])
    user: User = UserFactory.create(staff=True, **values)
    return user


# --- Tax rates


def test_the_first_rate_creates_an_audited_open_ended_row() -> None:
    changeset = services.configure_tax_rate(
        TaxRateFactory.build(country="fi", rate=Decimal("25.5"), valid_from=utc(2027)), ctx=CTX
    )
    stored = TaxRate.objects.get()
    assert (stored.country, stored.rate, stored.valid_to) == ("FI", Decimal("25.5000"), None)
    assert changeset.summary() == {"create": 1, "update": 0, "delete": 0}
    entry = AuditLog.objects.get()
    assert entry.action == "tax.rate.configured"
    assert entry.target_model == "tax.taxrate"
    assert entry.target_key == str(stored.public_id)
    assert entry.after is not None
    assert entry.after["rate"] == "25.5000"
    assert entry.vendor_key == ""


def test_a_rate_change_closes_the_current_rate_and_audits_old_and_new() -> None:
    services.configure_tax_rate(
        TaxRateFactory.build(country="EE", rate=Decimal("22"), valid_from=utc(2027)), ctx=CTX
    )
    new = TaxRateFactory.build(country="EE", rate=Decimal("24"), valid_from=utc(2028, 7))
    changeset = services.configure_tax_rate(new, ctx=CTX)

    assert changeset.summary() == {"create": 1, "update": 1, "delete": 0}
    old, current = TaxRate.objects.order_by("valid_from")
    assert old.valid_to == utc(2028, 7)
    assert current.valid_to is None
    entry = AuditLog.objects.get(target_key=str(new.public_id))
    assert entry.before is not None
    assert entry.after is not None
    assert (entry.before["rate"], entry.after["rate"]) == ("22.0000", "24.0000")


def test_rates_are_looked_up_by_country_category_territory_and_date() -> None:
    canary = TerritoryFactory.create(code="ES-CN", country="ES")
    TaxRateFactory.create(
        country="EE", rate=Decimal("22"), valid_from=utc(2027), valid_to=utc(2028)
    )
    TaxRateFactory.create(country="EE", rate=Decimal("24"), valid_from=utc(2028), open_ended=True)
    TaxRateFactory.create(country="ES", rate=Decimal("21"), valid_from=utc(2027), open_ended=True)
    TaxRateFactory.create(
        country="ES",
        territory=canary,
        category=engine.ZERO,
        rate=Decimal("0"),
        valid_from=utc(2027),
        open_ended=True,
    )

    assert services.current_rate("EE", engine.STANDARD, at=utc(2027, 6)).rate == Decimal("22")
    assert services.current_rate("ee", engine.STANDARD, at=utc(2030)).rate == Decimal("24")
    assert services.current_rate("ES", engine.STANDARD, at=utc(2029)).rate == Decimal("21")
    zero = services.current_rate("ES", engine.ZERO, territory=canary, at=utc(2029))
    assert zero.territory == canary


def test_a_missing_rate_is_a_clear_error_never_a_silent_zero() -> None:
    TaxRateFactory.create(country="DE", rate=Decimal("19"), valid_from=utc(2027), open_ended=True)
    with pytest.raises(services.NoTaxRate, match="reduced"):
        services.current_rate("DE", engine.REDUCED, at=utc(2028))


def test_a_country_rate_is_not_a_territory_rate() -> None:
    canary = TerritoryFactory.create(code="ES-CN", country="ES")
    TaxRateFactory.create(country="ES", rate=Decimal("21"), valid_from=utc(2027), open_ended=True)
    with pytest.raises(services.NoTaxRate):
        services.current_rate("ES", engine.STANDARD, territory=canary, at=utc(2028))


def test_a_rate_change_dry_run_writes_nothing() -> None:
    changeset = services.configure_tax_rate(TaxRateFactory.build(), ctx=CTX, dry_run=True)
    assert changeset.summary()["create"] == 1
    assert TaxRate.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_a_new_rate_starts_after_the_current_one() -> None:
    services.configure_tax_rate(TaxRateFactory.build(valid_from=utc(2028)), ctx=CTX)
    with pytest.raises(ValidationError) as exc:
        services.configure_tax_rate(TaxRateFactory.build(valid_from=utc(2028)), ctx=CTX)
    assert "valid_from" in exc.value.message_dict
    assert TaxRate.objects.count() == 1


def test_a_new_rate_is_validated_before_writing() -> None:
    with pytest.raises(ValidationError):
        services.configure_tax_rate(
            TaxRateFactory.build(category=engine.ZERO, rate=Decimal("5")), ctx=CTX
        )
    assert TaxRate.objects.count() == 0


def test_saved_rates_cannot_be_configured_again() -> None:
    with pytest.raises(ValueError, match="unsaved"):
        services.configure_tax_rate(TaxRateFactory.create(), ctx=CTX)


# --- Territories


def test_territories_are_saved_with_an_audit_entry() -> None:
    territory = TerritoryFactory.build(code="IT-LIVIGNO", country="IT", postcode_patterns=["23041"])
    services.save_territory(territory, ctx=CTX)
    territory.name = "Livigno"
    services.save_territory(territory, ctx=CTX)

    created, updated = AuditLog.objects.order_by("id")
    assert (created.action, updated.action) == ("tax.territory.created", "tax.territory.updated")
    assert updated.before is not None
    assert updated.after is not None
    assert updated.after["name"] == "Livigno"
    assert Territory.objects.get().name == "Livigno"


def test_a_territory_without_rates_can_change_its_country_and_code() -> None:
    territory = TerritoryFactory.create(code="ES-CN", country="ES")
    territory.country, territory.code = "PT", "PT-30"
    services.save_territory(territory, ctx=CTX)
    assert Territory.objects.values_list("country", "code").get() == ("PT", "PT-30")


@pytest.mark.parametrize(("field", "value"), [("country", "PT"), ("code", "ES-XX")])
def test_a_territory_with_rates_keeps_its_country_and_code(field: str, value: str) -> None:
    """Rates, rate snapshots and audit entries name the territory by its code and country."""
    territory = TerritoryFactory.create(code="ES-CN", country="ES")
    TaxRateFactory.create(country="ES", territory=territory)
    setattr(territory, field, value)
    with pytest.raises(ValidationError) as exc:
        services.save_territory(territory, ctx=CTX)
    assert field in exc.value.message_dict
    assert Territory.objects.values_list("country", "code").get() == ("ES", "ES-CN")
    assert not AuditLog.objects.exists()


def test_a_territory_with_rates_can_still_change_its_other_fields() -> None:
    territory = TerritoryFactory.create(code="ES-CN", country="ES")
    TaxRateFactory.create(country="ES", territory=territory)
    territory.name = "Canary Islands"
    services.save_territory(territory, ctx=CTX)
    assert Territory.objects.get().name == "Canary Islands"


# --- Vendor tax profiles


def test_the_first_profile_of_a_vendor_is_audited_under_the_vendor() -> None:
    vendor = VendorFactory.create()
    services.configure_vendor_tax_profile(
        VendorTaxProfileFactory.build(vendor=vendor, oss=True), approver=approver(), ctx=CTX
    )
    profile = VendorTaxProfile.objects.get()
    assert profile.regime == "oss"
    entry = AuditLog.objects.get()
    assert entry.action == "tax.vendor_tax_profile.configured"
    assert entry.target_model == "tax.vendortaxprofile"
    assert entry.vendor_key == str(vendor.public_id)


def test_a_regime_change_closes_the_current_profile_and_keeps_history() -> None:
    vendor = VendorFactory.create()
    services.configure_vendor_tax_profile(
        VendorTaxProfileFactory.build(vendor=vendor, valid_from=utc(2027)),
        approver=approver(),
        ctx=CTX,
    )
    services.configure_vendor_tax_profile(
        VendorTaxProfileFactory.build(vendor=vendor, oss=True, valid_from=utc(2028)),
        approver=approver(),
        ctx=CTX,
    )
    old, new = VendorTaxProfile.objects.filter(vendor=vendor).order_by("valid_from")
    assert (old.regime, old.valid_to) == ("domestic_only", utc(2028))
    assert (new.regime, new.valid_to) == ("oss", None)
    assert services.current_tax_profile(vendor, at=utc(2027, 6)) == old
    assert services.current_tax_profile(vendor, at=utc(2029)) == new
    entry = AuditLog.objects.get(target_key=str(new.public_id))
    assert entry.before is not None
    assert entry.after is not None
    assert (entry.before["regime"], entry.after["regime"]) == ("domestic_only", "oss")


def test_another_vendor_profile_is_not_closed() -> None:
    other = VendorTaxProfileFactory.create(valid_from=utc(2027))
    services.configure_vendor_tax_profile(
        VendorTaxProfileFactory.build(vendor=VendorFactory.create(), valid_from=utc(2028)),
        approver=approver(),
        ctx=CTX,
    )
    other.refresh_from_db()
    assert other.valid_to is None


def test_a_vendor_without_a_profile_is_a_clear_error() -> None:
    with pytest.raises(services.NoVendorTaxProfile):
        services.current_tax_profile(VendorFactory.create(), at=utc(2028))


def test_a_profile_inconsistent_with_the_fiscal_regime_is_refused() -> None:
    vendor = VendorFactory.create(fiscal_regime=FLAT_RATE_REGIME)
    with pytest.raises(ValidationError) as exc:
        services.configure_vendor_tax_profile(
            VendorTaxProfileFactory.build(vendor=vendor), approver=approver(), ctx=CTX
        )
    assert "regime" in exc.value.message_dict
    assert VendorTaxProfile.objects.count() == 0


def test_an_oss_registration_date_on_another_regime_is_refused() -> None:
    profile = VendorTaxProfileFactory.build(
        vendor=VendorFactory.create(), oss_registered_since=dt.date(2026, 7, 1)
    )
    with pytest.raises(ValidationError) as exc:
        services.configure_vendor_tax_profile(profile, approver=approver(), ctx=CTX)
    assert "oss_registered_since" in exc.value.message_dict
    assert VendorTaxProfile.objects.count() == 0


def test_a_profile_change_dry_run_writes_nothing() -> None:
    changeset = services.configure_vendor_tax_profile(
        VendorTaxProfileFactory.build(vendor=VendorFactory.create()),
        approver=approver(),
        ctx=CTX,
        dry_run=True,
    )
    assert changeset.summary()["create"] == 1
    assert VendorTaxProfile.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_a_direct_profile_change_is_approved_in_the_acting_staff_member_name() -> None:
    staff = approver()
    other = approver()
    profile = VendorTaxProfileFactory.build(vendor=VendorFactory.create(), approved_by=other)
    services.configure_vendor_tax_profile(profile, approver=staff, ctx=CTX)
    stored = VendorTaxProfile.objects.get()
    assert (stored.approved_by, stored.requested_by) == (staff, None)  # staff-initiated


def test_a_direct_profile_change_needs_the_review_permission() -> None:
    """Changing a vendor's VAT regime directly needs what approving a request needs (TAX.2)."""
    vendor = VendorFactory.create()
    refused = [
        approver(permissions=["tax.add_vendortaxprofile", "tax.view_vendortaxprofile"]),
        UserFactory.create(permissions=[services.REVIEW_PERMISSION]),  # not staff
        approver(is_active=False),
    ]
    for user in refused:
        for dry_run in (False, True):
            with pytest.raises(engine.RegimeChangeNotAllowed):
                services.configure_vendor_tax_profile(
                    VendorTaxProfileFactory.build(vendor=vendor),
                    approver=user,
                    ctx=CTX,
                    dry_run=dry_run,
                )
    assert VendorTaxProfile.objects.count() == 0
    assert not AuditLog.objects.exists()


def test_a_direct_profile_change_is_not_approved_by_the_person_who_asked_for_it() -> None:
    staff = approver()
    profile = VendorTaxProfileFactory.build(vendor=VendorFactory.create(), requested_by=staff)
    with pytest.raises(engine.RegimeChangeNotAllowed, match="someone other"):
        services.configure_vendor_tax_profile(profile, approver=staff, ctx=CTX)
    assert VendorTaxProfile.objects.count() == 0


def test_legal_notes_are_saved_with_an_audit_entry_under_the_vendor() -> None:
    profile = VendorTaxProfileFactory.create(art74=True)
    note = VendorTaxProfileTranslationFactory.build(profile=profile, language="it")
    services.save_legal_note(note, ctx=CTX)
    entry = AuditLog.objects.get()
    assert entry.action == "tax.legal_note.created"
    assert entry.vendor_key == str(profile.vendor.public_id)
    assert entry.after is not None
    assert entry.after["legal_note"] == note.legal_note


def ended_profile() -> VendorTaxProfile:
    profile: VendorTaxProfile = VendorTaxProfileFactory.create(
        art74=True, valid_from=utc(2025), valid_to=utc(2026)
    )
    return profile


def test_a_legal_note_of_an_ended_profile_cannot_be_changed() -> None:
    note = VendorTaxProfileTranslationFactory.create(profile=ended_profile(), language="it")
    note.legal_note = "Rewritten history."
    with pytest.raises(ValidationError) as exc:
        services.save_legal_note(note, ctx=CTX)
    assert "profile" in exc.value.message_dict
    assert VendorTaxProfileTranslation.objects.get(pk=note.pk).legal_note != "Rewritten history."
    assert AuditLog.objects.count() == 0


def test_a_legal_note_cannot_be_added_to_an_ended_profile() -> None:
    note = VendorTaxProfileTranslationFactory.build(profile=ended_profile(), language="it")
    with pytest.raises(ValidationError):
        services.save_legal_note(note, ctx=CTX)
    assert VendorTaxProfileTranslation.objects.count() == 0


def test_a_legal_note_of_a_profile_that_ends_later_can_still_be_changed() -> None:
    profile = VendorTaxProfileFactory.create(
        art74=True, valid_from=utc(2025), valid_to=timezone.now() + dt.timedelta(days=30)
    )
    note = VendorTaxProfileTranslationFactory.create(profile=profile, language="it")
    note.legal_note = "Corrected wording."
    services.save_legal_note(note, ctx=CTX)
    assert VendorTaxProfileTranslation.objects.get(pk=note.pk).legal_note == "Corrected wording."


def test_a_legal_note_stays_with_its_profile() -> None:
    note = VendorTaxProfileTranslationFactory.create(language="it")
    original = note.profile
    note.profile = VendorTaxProfileFactory.create(art74=True)
    with pytest.raises(ValidationError) as exc:
        services.save_legal_note(note, ctx=CTX)
    assert "profile" in exc.value.message_dict
    assert VendorTaxProfileTranslation.objects.get(pk=note.pk).profile == original


# --- VIES result cache


def test_a_vies_answer_is_recorded_without_the_country_prefix() -> None:
    check = services.record_vat_validation(
        "GR", "el 123 456 789", engine.VAT_VALID, name="Example AE", request_id="WAPIAAAA"
    )
    stored = VatValidation.objects.get(pk=check.pk)
    assert (stored.country, stored.vat_number) == ("GR", "123456789")
    assert (stored.result, stored.name) == ("valid", "Example AE")


def test_a_vies_answer_cannot_be_recorded_as_a_staff_claim() -> None:
    with pytest.raises(TypeError):
        services.record_vat_validation(  # type: ignore[call-arg]
            "DE", "123456789", engine.VAT_VALID, source="manual"
        )
    assert VatValidation.objects.count() == 0


def test_an_unknown_vies_result_is_refused() -> None:
    with pytest.raises(ValidationError):
        services.record_vat_validation("DE", "123456789", "probably")
    assert VatValidation.objects.count() == 0


def test_the_latest_vies_answer_is_found_by_country_and_number() -> None:
    now = timezone.now()
    VatValidationFactory.create(
        country="DE", vat_number="123456789", checked_at=now - dt.timedelta(days=3)
    )
    newest = VatValidationFactory.create(
        country="DE", vat_number="123456789", result=engine.VAT_UNAVAILABLE, checked_at=now
    )
    VatValidationFactory.create(country="AT", vat_number="123456789", checked_at=now)

    assert services.latest_vat_validation("de", "DE123456789") == newest
    assert services.latest_vat_validation("DE", "999999999") is None
    since = now + dt.timedelta(seconds=1)
    assert services.latest_vat_validation("DE", "123456789", since=since) is None
