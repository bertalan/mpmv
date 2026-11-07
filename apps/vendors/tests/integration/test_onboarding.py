"""VEN.5: vendor application, versioned contract, contract acceptance and the activation gate.

Services only (the staff admin and the whole journey are in `test_onboarding_admin` and
`test_onboarding_journey`).
"""

import datetime as dt
import threading
import time
from collections.abc import Callable

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog
from apps.core.translations import source_hash
from apps.vendors import engine
from apps.vendors.models import (
    ContractAcceptance,
    Vendor,
    VendorContract,
    VendorContractTranslation,
    VendorMember,
)
from apps.vendors.services import (
    accept_contract,
    assert_payout_allowed,
    change_status,
    contract_offer,
    current_contract,
    onboarding_checklist,
    save_contract,
    submit_application,
)
from apps.vendors.tests.factories import (
    ContractAcceptanceFactory,
    VendorContractFactory,
    VendorContractTranslationFactory,
    VendorFactory,
    VendorMemberFactory,
)

pytestmark = pytest.mark.django_db

STAFF_CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="10.0.0.1")


def vendor_ctx(user_id: int, ip: str = "203.0.113.7") -> AuditContext:
    return AuditContext(actor_id=user_id, actor_label="user:owner", source="vendor", ip=ip)


def application(**fields: str) -> Vendor:
    data = {
        "legal_name": "Vendor A S.r.l.",
        "display_name": "Vendor A",
        "country": "IT",
        "contact_email": "vendor-a@example.com",
        **fields,
    }
    return Vendor(**data)


def onboarding_owner(**vendor_fields: object) -> VendorMember:
    vendor = VendorFactory.create(status=Vendor.Status.ONBOARDING, **vendor_fields)
    return VendorMemberFactory.create(vendor=vendor)


def accept(
    vendor: Vendor, user: User, *, language: str, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """What the onboarding page does: show the offer in `language`, then accept what it showed."""
    offer = contract_offer(language)
    return accept_contract(
        vendor,
        user,
        contract=offer.contract,
        language=offer.text.language,
        text_hash=offer.text_hash,
        ctx=ctx,
        dry_run=dry_run,
    )


# --- Application


def test_an_application_creates_an_applicant_vendor_owned_by_the_applicant() -> None:
    user = UserFactory.create()
    vendor = application()
    changes = submit_application(vendor, user, ctx=vendor_ctx(user.pk))

    assert changes.summary() == {"create": 2, "update": 0, "delete": 0}
    stored = Vendor.objects.get()
    assert stored.status == Vendor.Status.APPLICANT
    assert stored.slug == "vendor-a"
    member = VendorMember.objects.get()
    assert (member.vendor, member.user, member.role) == (stored, user, VendorMember.Role.OWNER)

    entries = AuditLog.objects.filter(action="vendor.application_submitted")
    assert {entry.target_model for entry in entries} == {"vendors.vendor", "vendors.vendormember"}
    assert {entry.vendor_key for entry in entries} == {str(stored.public_id)}
    assert {entry.ip for entry in entries} == {"203.0.113.7"}


def test_an_application_starts_as_applicant_whatever_the_instance_says() -> None:
    user = UserFactory.create()
    vendor = application()
    vendor.status = Vendor.Status.ACTIVE
    submit_application(vendor, user, ctx=vendor_ctx(user.pk))
    assert Vendor.objects.get().status == Vendor.Status.APPLICANT


def test_an_application_dry_run_creates_nothing() -> None:
    user = UserFactory.create()
    changes = submit_application(application(), user, ctx=vendor_ctx(user.pk), dry_run=True)
    assert changes.summary()["create"] == 2
    assert not Vendor.objects.exists()
    assert not VendorMember.objects.exists()
    assert not AuditLog.objects.exists()


def test_an_invalid_application_is_refused_and_nothing_is_created() -> None:
    user = UserFactory.create()
    with pytest.raises(ValidationError) as exc_info:
        submit_application(application(vat_number="12345"), user, ctx=vendor_ctx(user.pk))
    assert "vat_number" in exc_info.value.message_dict
    assert not Vendor.objects.exists()
    assert not AuditLog.objects.exists()


def test_an_existing_vendor_cannot_be_submitted_again() -> None:
    vendor = VendorFactory.create()
    user = UserFactory.create()
    with pytest.raises(ValueError, match="new vendor"):
        submit_application(vendor, user, ctx=vendor_ctx(user.pk))


def test_an_inactive_user_cannot_apply() -> None:
    user = UserFactory.create(is_active=False)
    with pytest.raises(ValueError, match="active"):
        submit_application(application(), user, ctx=vendor_ctx(user.pk))
    assert not Vendor.objects.exists()


# --- Versioned contract


def test_staff_publish_contract_versions_one_after_the_other() -> None:
    now = timezone.now()
    first = VendorContract(valid_from=now - dt.timedelta(days=10))
    save_contract(first, ctx=STAFF_CTX)
    second = VendorContract(valid_from=now + dt.timedelta(days=5), note="New fees")
    save_contract(second, ctx=STAFF_CTX)

    assert (first.version, second.version) == (1, 2)
    entry = AuditLog.objects.filter(action="vendor.contract_saved").order_by("id").last()
    assert entry is not None
    assert entry.target_model == "vendors.vendorcontract"
    assert entry.target_key == str(second.public_id)
    assert entry.after is not None
    assert entry.after["version"] == 2
    assert entry.after["note"] == "New fees"
    assert entry.vendor_key == ""  # platform configuration, not one vendor's data


def test_a_new_version_must_start_after_the_latest_one() -> None:
    now = timezone.now()
    save_contract(VendorContract(valid_from=now), ctx=STAFF_CTX)
    with pytest.raises(ValidationError) as exc_info:
        save_contract(VendorContract(valid_from=now - dt.timedelta(days=1)), ctx=STAFF_CTX)
    assert "valid_from" in exc_info.value.message_dict
    assert VendorContract.objects.count() == 1


def test_a_draft_cannot_move_before_the_previous_or_after_the_next_version() -> None:
    now = timezone.now()
    first = VendorContractFactory.create(valid_from=now - dt.timedelta(days=10))
    draft = VendorContractFactory.create(valid_from=now + dt.timedelta(days=10))
    VendorContractFactory.create(valid_from=now + dt.timedelta(days=20))
    for start in (first.valid_from, now + dt.timedelta(days=20)):
        draft.valid_from = start
        with pytest.raises(ValidationError) as exc_info:
            save_contract(draft, ctx=STAFF_CTX)
        assert "valid_from" in exc_info.value.message_dict
    draft.valid_from = now + dt.timedelta(days=15)
    save_contract(draft, ctx=STAFF_CTX)


def test_a_contract_dry_run_saves_nothing() -> None:
    changes = save_contract(VendorContract(valid_from=timezone.now()), ctx=STAFF_CTX, dry_run=True)
    assert changes.summary()["create"] == 1
    assert not VendorContract.objects.exists()
    assert not AuditLog.objects.exists()


def test_the_current_contract_is_the_latest_version_already_in_force() -> None:
    assert current_contract() is None
    now = timezone.now()
    old = VendorContractFactory.create(valid_from=now - dt.timedelta(days=30))
    current = VendorContractFactory.create(valid_from=now - dt.timedelta(days=1))
    VendorContractFactory.create(valid_from=now + dt.timedelta(days=30))  # not in force yet

    assert current_contract() == current
    assert current_contract(now - dt.timedelta(days=10)) == old


def test_a_draft_version_can_be_edited_and_the_edit_is_audited() -> None:
    contract = VendorContractFactory.create(note="Draft")
    contract.note = "Final"
    save_contract(contract, ctx=STAFF_CTX)
    entry = AuditLog.objects.get(action="vendor.contract_saved")
    assert entry.before is not None
    assert entry.after is not None
    assert (entry.before["note"], entry.after["note"]) == ("Draft", "Final")


def test_an_accepted_version_can_no_longer_change() -> None:
    acceptance = ContractAcceptanceFactory.create()
    contract = acceptance.contract
    contract.note = "Rewritten"
    with pytest.raises(engine.ContractLocked):
        save_contract(contract, ctx=STAFF_CTX)
    with pytest.raises(engine.ContractLocked):
        contract.save()
    text = contract.translations.get(language="en")
    text.body = "Rewritten terms"
    with pytest.raises(engine.ContractLocked):
        text.save()
    contract.refresh_from_db()
    assert contract.note != "Rewritten"


def test_the_texts_of_an_accepted_version_cannot_be_deleted_or_rewritten() -> None:
    acceptance = ContractAcceptanceFactory.create()
    contract = acceptance.contract
    text = contract.translations.get(language="en")
    with pytest.raises(engine.ContractLocked):
        text.delete()
    with pytest.raises(engine.ContractLocked):
        VendorContractTranslationFactory.create(contract=contract, language="it")

    texts = VendorContractTranslation.objects.filter(contract=contract)
    with pytest.raises(DatabaseError, match="accepted"), transaction.atomic():
        texts.update(body="Rewritten terms")
    with pytest.raises(DatabaseError, match="accepted"), transaction.atomic():
        texts.delete()
    with pytest.raises(DatabaseError, match="accepted"), transaction.atomic():
        VendorContractTranslation.objects.bulk_create(
            [VendorContractTranslation(contract=contract, language="de", title="T", body="B")]
        )
    stored = texts.get()
    assert source_hash(stored.source_values()) == acceptance.text_hash


def test_an_accepted_version_cannot_be_deleted_or_rewritten_in_the_database() -> None:
    acceptance = ContractAcceptanceFactory.create()
    contract = acceptance.contract
    with pytest.raises(engine.ContractLocked):
        contract.delete()
    versions = VendorContract.objects.filter(pk=contract.pk)
    with pytest.raises(DatabaseError, match="accepted"), transaction.atomic():
        versions.update(valid_from=contract.valid_from - dt.timedelta(days=1))
    with (
        pytest.raises(DatabaseError, match="accepted"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM vendors_vendorcontract WHERE id = %s", [contract.pk])
    assert versions.get().valid_from == contract.valid_from


def test_a_version_nobody_accepted_can_still_be_edited_and_deleted() -> None:
    contract = VendorContractFactory.create()
    VendorContractTranslation.objects.filter(contract=contract).update(body="Edited draft")
    text = contract.translations.get(language="en")
    text.delete()
    contract.delete()
    assert not VendorContract.objects.exists()
    assert not VendorContractTranslation.objects.exists()


# --- Acceptance


def test_an_owner_accepts_the_current_contract_from_the_request_ip() -> None:
    member = onboarding_owner()
    contract = VendorContractFactory.create()
    italian = VendorContractTranslationFactory.create(contract=contract, language="it")

    changes = accept(member.vendor, member.user, language="it", ctx=vendor_ctx(member.user.pk))

    assert changes.summary() == {"create": 1, "update": 0, "delete": 0}
    acceptance = ContractAcceptance.objects.get()
    assert acceptance.vendor == member.vendor
    assert acceptance.contract == contract
    assert acceptance.user == member.user
    assert acceptance.language == "it"
    assert acceptance.ip == "203.0.113.7"
    assert acceptance.text_hash == source_hash(italian.source_values())
    assert timezone.now() - acceptance.accepted_at < dt.timedelta(minutes=1)

    entry = AuditLog.objects.get(action="vendor.contract_accepted")
    assert entry.target_model == "vendors.contractacceptance"
    assert entry.vendor_key == str(member.vendor.public_id)
    assert entry.after is not None
    assert entry.after["contract_version"] == contract.version
    assert entry.after["user"] == str(member.user.public_id)
    assert entry.after["ip"] == "203.0.113.7"


def test_the_english_text_is_accepted_when_the_language_has_none() -> None:
    member = onboarding_owner()
    contract = VendorContractFactory.create()
    accept(member.vendor, member.user, language="de", ctx=vendor_ctx(member.user.pk))
    acceptance = ContractAcceptance.objects.get()
    assert acceptance.language == "en"
    english = contract.translations.get(language="en")
    assert acceptance.text_hash == source_hash(english.source_values())


def test_accepting_the_same_version_twice_records_it_once() -> None:
    member = onboarding_owner()
    VendorContractFactory.create()
    ctx = vendor_ctx(member.user.pk)
    accept(member.vendor, member.user, language="en", ctx=ctx)
    again = accept(member.vendor, member.user, language="en", ctx=ctx)
    assert again.is_empty
    assert ContractAcceptance.objects.count() == 1
    assert AuditLog.objects.filter(action="vendor.contract_accepted").count() == 1


def test_an_acceptance_dry_run_records_nothing() -> None:
    member = onboarding_owner()
    VendorContractFactory.create()
    changes = accept(
        member.vendor, member.user, language="en", ctx=vendor_ctx(member.user.pk), dry_run=True
    )
    assert changes.summary()["create"] == 1
    assert not ContractAcceptance.objects.exists()
    assert not AuditLog.objects.exists()


def test_vendor_staff_and_strangers_cannot_accept_for_the_vendor() -> None:
    member = onboarding_owner()
    VendorContractFactory.create()
    staff = VendorMemberFactory.create(vendor=member.vendor, role=VendorMember.Role.STAFF).user
    stranger = UserFactory.create()
    for user in (staff, stranger):
        with pytest.raises(engine.ContractNotAcceptable, match="owner"):
            accept(member.vendor, user, language="en", ctx=vendor_ctx(user.pk))
    assert not ContractAcceptance.objects.exists()


@pytest.mark.parametrize("status", [Vendor.Status.APPLICANT, Vendor.Status.CLOSED])
def test_applicants_and_closed_vendors_cannot_accept(status: str) -> None:
    member = VendorMemberFactory.create(vendor=VendorFactory.create(status=status))
    VendorContractFactory.create()
    with pytest.raises(engine.ContractNotAcceptable):
        accept(member.vendor, member.user, language="en", ctx=vendor_ctx(member.user.pk))


def test_nothing_can_be_accepted_without_a_contract_in_force() -> None:
    member = onboarding_owner()
    future = VendorContractFactory.create(valid_from=timezone.now() + dt.timedelta(days=1))
    with pytest.raises(engine.NoContract):
        accept(member.vendor, member.user, language="en", ctx=vendor_ctx(member.user.pk))
    text = future.translations.get(language="en")
    with pytest.raises(engine.NoContract):
        accept_contract(
            member.vendor,
            member.user,
            contract=future,
            language="en",
            text_hash=source_hash(text.source_values()),
            ctx=vendor_ctx(member.user.pk),
        )


def test_a_contract_without_a_published_text_cannot_be_accepted() -> None:
    member = onboarding_owner()
    VendorContractFactory.create(languages=[])
    with pytest.raises(engine.NoContract, match="published"):
        accept(member.vendor, member.user, language="en", ctx=vendor_ctx(member.user.pk))


def test_acceptances_are_append_only_in_the_database() -> None:
    acceptance = ContractAcceptanceFactory.create()
    with pytest.raises(PermissionError):
        acceptance.save()
    rows = ContractAcceptance.objects.filter(pk=acceptance.pk)
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        rows.update(language="de")
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        rows.delete()
    assert rows.get().language == acceptance.language


# --- Acceptance of what the owner was shown


def test_the_offer_is_the_version_in_force_with_the_hash_of_its_text() -> None:
    contract = VendorContractFactory.create()
    italian = VendorContractTranslationFactory.create(contract=contract, language="it")
    offer = contract_offer("it")
    assert (offer.contract, offer.text) == (contract, italian)
    assert offer.text_hash == source_hash(italian.source_values())
    assert contract_offer("de").text.language == "en"


def test_a_version_that_is_no_longer_in_force_cannot_be_accepted() -> None:
    member = onboarding_owner()
    VendorContractFactory.create(valid_from=timezone.now() - dt.timedelta(days=10))
    shown = contract_offer("en")
    VendorContractFactory.create(valid_from=timezone.now() - dt.timedelta(seconds=1))

    with pytest.raises(engine.ContractChanged, match="no longer in force"):
        accept_contract(
            member.vendor,
            member.user,
            contract=shown.contract,
            language=shown.text.language,
            text_hash=shown.text_hash,
            ctx=vendor_ctx(member.user.pk),
        )
    assert not ContractAcceptance.objects.exists()
    assert not AuditLog.objects.exists()


def test_a_text_edited_after_it_was_shown_cannot_be_accepted() -> None:
    member = onboarding_owner()
    VendorContractFactory.create()
    shown = contract_offer("en")
    text = shown.contract.translations.get(language="en")
    text.body = "Terms edited by staff while the owner reads"
    text.save()

    def accept_shown(text_hash: str) -> ChangeSet:
        return accept_contract(
            member.vendor,
            member.user,
            contract=shown.contract,
            language="en",
            text_hash=text_hash,
            ctx=vendor_ctx(member.user.pk),
        )

    for stale in (shown.text_hash, "0" * 64):
        with pytest.raises(engine.ContractChanged, match="text"):
            accept_shown(stale)
    assert not ContractAcceptance.objects.exists()

    reread = contract_offer("en")  # the page shows the edited text again
    accept_shown(reread.text_hash)
    acceptance = ContractAcceptance.objects.get()
    assert acceptance.text_hash == source_hash(text.source_values())


def test_a_text_unpublished_after_it_was_shown_cannot_be_accepted() -> None:
    member = onboarding_owner()
    contract = VendorContractFactory.create()
    italian = VendorContractTranslationFactory.create(contract=contract, language="it")
    shown = contract_offer("it")
    italian.status = VendorContractTranslation.Status.MACHINE
    italian.save()
    with pytest.raises(engine.ContractChanged):
        accept_contract(
            member.vendor,
            member.user,
            contract=contract,
            language="it",
            text_hash=shown.text_hash,
            ctx=vendor_ctx(member.user.pk),
        )


def _wait_for_a_blocked_query() -> None:
    """Return once another connection waits for a lock (fail after 10 seconds)."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_stat_clear_snapshot()")  # fresh view inside a transaction
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND pid <> pg_backend_pid() AND wait_event_type = 'Lock'"
            )
            row = cursor.fetchone()
        if row and row[0]:
            return
        time.sleep(0.05)
    pytest.fail("the other transaction never waited for the contract lock")


def _in_thread(work: Callable[[], object]) -> tuple[threading.Thread, list[object]]:
    """Run `work` on its own database connection; the list gets its result or its error."""
    outcome: list[object] = []

    def run() -> None:
        try:
            outcome.append(work())
        except Exception as exc:
            outcome.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome


@pytest.mark.django_db(transaction=True)
def test_an_acceptance_waits_for_a_text_edit_in_progress_then_refuses_the_old_text() -> None:
    member = onboarding_owner()
    VendorContractFactory.create()
    shown = contract_offer("en")
    with transaction.atomic():
        text = shown.contract.translations.get(language="en")
        text.body = "Terms edited at the same moment"
        text.save()
        thread, outcome = _in_thread(
            lambda: accept_contract(
                member.vendor,
                member.user,
                contract=shown.contract,
                language="en",
                text_hash=shown.text_hash,
                ctx=vendor_ctx(member.user.pk),
            )
        )
        _wait_for_a_blocked_query()
    thread.join(timeout=10)
    assert len(outcome) == 1
    assert isinstance(outcome[0], engine.ContractChanged)
    assert not ContractAcceptance.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_a_text_edit_waits_for_an_acceptance_in_progress_then_is_refused() -> None:
    member = onboarding_owner()
    VendorContractFactory.create()
    shown = contract_offer("en")

    def edit() -> None:
        text = VendorContractTranslation.objects.get(pk=shown.text.pk)
        text.body = "Terms edited at the same moment"
        text.save()

    with transaction.atomic():
        accept_contract(
            member.vendor,
            member.user,
            contract=shown.contract,
            language="en",
            text_hash=shown.text_hash,
            ctx=vendor_ctx(member.user.pk),
        )
        thread, outcome = _in_thread(edit)
        _wait_for_a_blocked_query()
    thread.join(timeout=10)
    assert len(outcome) == 1
    assert isinstance(outcome[0], engine.ContractLocked)
    stored = VendorContractTranslation.objects.get(pk=shown.text.pk)
    assert source_hash(stored.source_values()) == ContractAcceptance.objects.get().text_hash


# --- Checklist and activation gate


def test_the_checklist_of_a_new_vendor_lists_what_is_missing() -> None:
    VendorContractFactory.create()
    vendor = VendorFactory.create(status=Vendor.Status.ONBOARDING)
    result = onboarding_checklist(vendor)
    assert not result.ready
    assert result.blocking_missing == (engine.CONTRACT,)
    dac7 = {item.key: item for item in result.items}[engine.DAC7]
    assert "iban" in dac7.missing
    assert "tax_residence_country" in dac7.missing


def test_a_vendor_cannot_be_activated_before_accepting_the_contract() -> None:
    VendorContractFactory.create()
    vendor = VendorFactory.create(status=Vendor.Status.ONBOARDING, italian_company=True)
    with pytest.raises(engine.ActivationNotAllowed, match="contract"):
        change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)
    with pytest.raises(engine.ActivationNotAllowed):
        change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX, dry_run=True)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ONBOARDING
    assert not AuditLog.objects.exists()


def test_incomplete_dac7_or_payout_data_does_not_block_activation() -> None:
    member = onboarding_owner()  # a bare vendor: no IBAN, no address, no tax residence
    VendorContractFactory.create()
    accept(member.vendor, member.user, language="en", ctx=vendor_ctx(member.user.pk))

    change_status(member.vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)

    member.vendor.refresh_from_db()
    assert member.vendor.status == Vendor.Status.ACTIVE
    with pytest.raises(engine.PayoutNotAllowed):  # payouts stay gated by VEN.12
        assert_payout_allowed(member.vendor)


def test_a_new_contract_version_must_be_accepted_before_activation() -> None:
    old = VendorContractFactory.create(valid_from=timezone.now() - dt.timedelta(days=30))
    acceptance = ContractAcceptanceFactory.create(
        contract=old, vendor=VendorFactory.create(status=Vendor.Status.ONBOARDING)
    )
    VendorContractFactory.create(valid_from=timezone.now() - dt.timedelta(days=1))
    with pytest.raises(engine.ActivationNotAllowed):
        change_status(acceptance.vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)


def test_reinstating_a_suspended_vendor_is_not_an_onboarding_step() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    VendorContractFactory.create()  # never accepted by this (legacy) vendor
    change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ACTIVE
