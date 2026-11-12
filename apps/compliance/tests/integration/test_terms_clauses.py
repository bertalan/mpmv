"""CMP.5: the contract clauses (a) to (h) as versioned terms, and the acceptance they need."""

import datetime as dt
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.cms.tests.factories import LegalPageFactory
from apps.compliance import services, terms_clauses
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors import engine
from apps.vendors.models import ContractAcceptance, Vendor, VendorContract
from apps.vendors.services import (
    accept_contract,
    change_status,
    contract_offer,
    current_contract,
    onboarding_checklist,
)
from apps.vendors.tests.factories import (
    ContractAcceptanceFactory,
    VendorContractFactory,
    VendorFactory,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:legal", source="admin", ip="127.0.0.1")


def publish(**kwargs: object) -> VendorContract:
    services.publish_vendor_terms(valid_from=timezone.now(), ctx=CTX, **kwargs)  # type: ignore[arg-type]
    contract = VendorContract.objects.order_by("-version").first()
    assert contract is not None
    return contract


def contract_item(vendor: Vendor):  # type: ignore[no-untyped-def]
    return next(i for i in onboarding_checklist(vendor).items if i.key == engine.CONTRACT)


def test_the_published_version_carries_clauses_d_to_h_in_english_and_italian() -> None:
    contract = publish()
    assert contract.version == 1
    assert services.vendor_terms_problems(contract) == []
    for language in ("en", "it"):
        body = contract.translations.get(language=language).body
        assert terms_clauses.missing_clauses(terms_clauses.VENDOR, body, language) == []
    assert "EUR 2.00" in contract.translations.get(language="en").body
    assert "EUR 2,00" in contract.translations.get(language="it").body
    entry = AuditLog.objects.get(action="vendor.contract_saved")
    assert entry.actor_label == "staff:legal"


def test_the_new_version_keeps_the_previous_text_before_the_clause_block() -> None:
    VendorContractFactory.create(valid_from=timezone.now() - dt.timedelta(days=30), languages=[])
    old = VendorContract.objects.get()
    old.translations.create(language="en", title="Old title", body="Old terms.", status="published")
    contract = publish()
    text = contract.translations.get(language="en")
    assert text.title == "Old title"
    assert text.body.startswith("Old terms.")
    assert text.body.count(terms_clauses.BLOCK_MARKER) == 1


def test_a_dry_run_publishes_nothing() -> None:
    changes = services.publish_vendor_terms(valid_from=timezone.now(), ctx=CTX, dry_run=True)
    assert changes
    assert not VendorContract.objects.exists()


def test_a_version_without_the_clauses_is_reported() -> None:
    VendorContractFactory.create()
    problems = services.vendor_terms_problems()
    assert "vendor_terms_clause_missing:en:f" in problems
    assert "vendor_terms_clause_missing:it:d" in problems  # no Italian text at all
    VendorContract.objects.all().delete()
    assert services.vendor_terms_problems() == ["vendor_terms_missing"]


def test_an_old_acceptance_does_not_satisfy_the_new_version() -> None:
    old = VendorContractFactory.create(valid_from=timezone.now() - dt.timedelta(days=30))
    acceptance = ContractAcceptanceFactory.create(
        contract=old, vendor=VendorFactory.create(status=Vendor.Status.ONBOARDING)
    )
    vendor = acceptance.vendor
    assert contract_item(vendor).done
    new = publish()
    assert current_contract() == new
    assert not contract_item(vendor).done
    assert ContractAcceptance.objects.filter(vendor=vendor, contract=old).exists()


def test_a_vendor_cannot_be_activated_before_accepting_the_version_with_the_clauses() -> None:
    old = VendorContractFactory.create(valid_from=timezone.now() - dt.timedelta(days=30))
    acceptance = ContractAcceptanceFactory.create(
        contract=old, vendor=VendorFactory.create(status=Vendor.Status.ONBOARDING)
    )
    vendor = acceptance.vendor
    publish()
    with pytest.raises(engine.ActivationNotAllowed, match="contract"):
        change_status(vendor, Vendor.Status.ACTIVE, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ONBOARDING


@pytest.mark.parametrize("language", ["en", "it"])
def test_accepting_the_new_version_records_the_hash_of_the_clause_text(language: str) -> None:
    old = VendorContractFactory.create(valid_from=timezone.now() - dt.timedelta(days=30))
    acceptance = ContractAcceptanceFactory.create(
        contract=old, vendor=VendorFactory.create(status=Vendor.Status.ONBOARDING)
    )
    vendor, owner = acceptance.vendor, acceptance.user
    publish()
    offer = contract_offer(language)
    assert "art. 1252" in offer.text.body
    accept_contract(
        vendor,
        owner,
        contract=offer.contract,
        language=offer.text.language,
        text_hash=offer.text_hash,
        ctx=AuditContext(actor_id=owner.pk, actor_label="user:owner", source="vendor"),
    )
    assert contract_item(vendor).done
    newest = ContractAcceptance.objects.filter(contract=offer.contract).get()
    assert newest.text_hash == offer.text_hash
    assert newest.language == language


def test_an_accepted_clause_version_is_locked_and_the_next_one_is_a_new_version() -> None:
    first = publish()
    ContractAcceptanceFactory.create(
        contract=first,
        vendor=VendorFactory.create(status=Vendor.Status.ONBOARDING),
        language="en",
    )
    with pytest.raises(engine.ContractLocked):
        first.translations.get(language="en").delete()
    later = services.publish_vendor_terms(valid_from=timezone.now() + dt.timedelta(days=1), ctx=CTX)
    assert later
    assert VendorContract.objects.count() == 2


# --- Customer terms: clauses (a) to (c) on the live terms page


def publish_terms(language_body: str) -> None:
    page = LegalPageFactory.create(
        title="Terms", slug="terms", body=[("paragraph", f"<p>{language_body}</p>")]
    )
    page.save_revision().publish()


def test_customer_terms_without_the_clauses_are_reported() -> None:
    publish_terms("Sample terms.")
    problems = services.customer_terms_problems(["en"])
    assert problems == [f"customer_terms_clause_missing:en:{c}" for c in "abc"]
    assert services.customer_terms_problems(["fr"]) == ["customer_terms_missing:fr"]


def test_customer_terms_with_the_clauses_pass() -> None:
    block = terms_clauses.clause_block(terms_clauses.CUSTOMER, "en").replace("\n\n", "</p><p>")
    publish_terms(block)
    assert services.customer_terms_problems(["en"]) == []


# --- The command


def test_the_command_publishes_the_vendor_version_and_check_reports_the_customer_gap() -> None:
    out = StringIO()
    call_command("publish_terms_clauses", stdout=out)
    assert VendorContract.objects.count() == 1
    with pytest.raises(CommandError, match="customer_terms_missing:en"):
        call_command("publish_terms_clauses", "--check")
    assert services.vendor_terms_problems() == []
