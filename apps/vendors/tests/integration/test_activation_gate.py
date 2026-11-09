"""CMP.25: the VEN.5 activation also checks the DSA article 30 trader data (`activation`
requirements) and the article 30(1)(e) self-certification, next to the CMP.13 gate."""

import pytest
from django.db import DatabaseError, IntegrityError, transaction

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog
from apps.vendors import engine
from apps.vendors.models import LegalFieldRequirement, SelfCertification, Vendor
from apps.vendors.services import (
    certify_platform_duties,
    change_status,
    is_self_certified,
    onboarding_checklist,
)
from apps.vendors.tests.factories import (
    ContractAcceptanceFactory,
    SelfCertificationFactory,
    VendorFactory,
    VendorMemberFactory,
)
from apps.vendors.tests.fakes import seed_legal_requirements

pytestmark = pytest.mark.django_db

STAFF_CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="10.0.0.1")
PHONE = "+49 30 1234567"


def owner_ctx(user_id: int) -> AuditContext:
    return AuditContext(
        actor_id=user_id, actor_label="user:owner", source="vendor", ip="203.0.113.7"
    )


def onboarding(**fields: object) -> Vendor:
    """An onboarding vendor that accepted the contract; the acceptance's user is its owner."""
    seed_legal_requirements()
    vendor = VendorFactory.create(status=Vendor.Status.ONBOARDING, **fields)
    ContractAcceptanceFactory.create(vendor=vendor, certified=False)
    return vendor


def certify(vendor: Vendor, *, language: str = "en", dry_run: bool = False) -> ChangeSet:
    owner = vendor.members.get().user
    return certify_platform_duties(
        vendor, owner, language=language, ctx=owner_ctx(owner.pk), dry_run=dry_run
    )


def test_the_seed_requires_telephone_address_and_trade_register_entry_for_eu_vendors() -> None:
    seed_legal_requirements()
    rules = LegalFieldRequirement.objects.filter(purpose="activation", region="eu")
    fields = set(rules.values_list("field_name", flat=True))
    assert {"phone", "registered_street", "registered_postal_code", "registered_city"} <= fields
    register = rules.filter(field_name="business_registration_number")
    assert register.exists()
    assert not register.filter(legal_form="").exists()
    assert not register.filter(legal_form="sole_proprietorship").exists()


def test_an_eu_vendor_without_telephone_cannot_be_activated_and_gets_the_reason() -> None:
    vendor = onboarding(eu_company=True, phone="")
    certify(vendor)
    with pytest.raises(engine.ActivationNotAllowed, match=r"platform_duties \(phone\)"):
        change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)
    with pytest.raises(engine.ActivationNotAllowed):
        change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX, dry_run=True)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ONBOARDING
    assert not AuditLog.objects.filter(action="vendor.status_changed").exists()


def test_a_registered_company_without_trade_register_entry_cannot_be_activated() -> None:
    vendor = onboarding(
        eu_company=True,
        legal_form=Vendor.LegalForm.SRL,
        phone=PHONE,
        business_registration_number="",
    )
    certify(vendor)
    with pytest.raises(engine.ActivationNotAllowed, match="business_registration_number"):
        change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)


def test_a_sole_proprietor_is_not_asked_for_a_trade_register_entry() -> None:
    vendor = onboarding(
        eu_company=True,
        legal_form=Vendor.LegalForm.SOLE_PROPRIETORSHIP,
        phone=PHONE,
        business_registration_number="",
    )
    certify(vendor)
    change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ACTIVE


def test_a_vendor_without_self_certification_cannot_be_activated() -> None:
    vendor = onboarding(eu_company=True, phone=PHONE)
    with pytest.raises(engine.ActivationNotAllowed, match=engine.SELF_CERTIFICATION):
        change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)


def test_a_complete_vendor_is_activated() -> None:
    vendor = onboarding(eu_company=True, phone=PHONE)
    certify(vendor)
    changes = change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ACTIVE
    assert changes.summary()["update"] == 1


def test_missing_dac7_data_does_not_block_activation() -> None:
    vendor = onboarding(
        eu_company=True, phone=PHONE, iban="", tax_residence_country="", tin_country=""
    )
    certify(vendor)
    checklist = onboarding_checklist(vendor)
    dac7 = {item.key: item for item in checklist.items}[engine.DAC7]
    assert not dac7.done
    assert checklist.ready
    change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)


def test_the_self_certification_row_stores_version_language_and_hash() -> None:
    vendor = onboarding(eu_company=True)
    certify(vendor, language="it")
    row = SelfCertification.objects.get()
    assert row.version == engine.SELF_CERTIFICATION_VERSION
    assert row.language == "it"
    assert row.text_hash == engine.self_certification_hash("it")
    assert row.vendor == vendor
    assert row.onboarding_round == vendor.onboarding_round


def test_certifying_is_audited_dry_run_writes_nothing_and_repeating_changes_nothing() -> None:
    vendor = onboarding(eu_company=True)
    changes = certify(vendor, dry_run=True)
    assert changes.summary()["create"] == 1
    assert not SelfCertification.objects.exists()
    assert not AuditLog.objects.filter(action="vendor.self_certified").exists()

    certify(vendor)
    entry = AuditLog.objects.get(action="vendor.self_certified")
    assert entry.vendor_key == str(vendor.public_id)
    assert certify(vendor).summary() == {"create": 0, "update": 0, "delete": 0}
    assert SelfCertification.objects.count() == 1


def test_only_an_owner_of_the_vendor_can_certify() -> None:
    vendor = onboarding(eu_company=True)
    stranger = UserFactory.create()
    with pytest.raises(engine.SelfCertificationError):
        certify_platform_duties(vendor, stranger, language="en", ctx=owner_ctx(stranger.pk))
    VendorMemberFactory.create(vendor=vendor, user=stranger, role="staff")
    with pytest.raises(engine.SelfCertificationError):
        certify_platform_duties(vendor, stranger, language="en", ctx=owner_ctx(stranger.pk))


def test_a_certification_of_an_older_round_does_not_count_after_a_reopen() -> None:
    vendor = onboarding(eu_company=True, phone=PHONE)
    certify(vendor)
    contract = vendor.contract_acceptances.get().contract
    Vendor.objects.filter(pk=vendor.pk).update(onboarding_round=vendor.onboarding_round + 1)
    ContractAcceptanceFactory.create(
        vendor=Vendor.objects.get(pk=vendor.pk), contract=contract, certified=False
    )
    with pytest.raises(engine.ActivationNotAllowed, match=engine.SELF_CERTIFICATION):
        change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)


def test_a_self_certification_is_unique_per_version_and_round() -> None:
    vendor = onboarding(eu_company=True)
    SelfCertificationFactory.create(vendor=vendor)
    with pytest.raises(IntegrityError), transaction.atomic():
        SelfCertificationFactory.create(vendor=vendor)
    assert is_self_certified(vendor)


def test_the_self_certification_table_is_append_only() -> None:
    vendor = onboarding(eu_company=True)
    certify(vendor)
    row = SelfCertification.objects.get()
    with pytest.raises(PermissionError):
        row.save()
    with pytest.raises(DatabaseError), transaction.atomic():
        SelfCertification.objects.filter(pk=row.pk).update(language="en")
    with pytest.raises(DatabaseError), transaction.atomic():
        SelfCertification.objects.filter(pk=row.pk).delete()
