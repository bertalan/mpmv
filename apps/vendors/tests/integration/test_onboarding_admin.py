"""VEN.5: staff manage contract versions, see acceptances and the onboarding checklist."""

from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.vendors.models import VendorContract
from apps.vendors.tests.factories import (
    ContractAcceptanceFactory,
    VendorContractFactory,
    VendorFactory,
)

pytestmark = pytest.mark.django_db

MfaLogin = Callable[[Client, User], Client]


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def test_staff_add_a_contract_version_with_its_texts(staff_client: Client) -> None:
    response = staff_client.post(
        reverse("admin:vendors_vendorcontract_add"),
        {
            "valid_from_0": "2026-01-01",
            "valid_from_1": "00:00:00",
            "note": "Launch terms",
            "translations-TOTAL_FORMS": "1",
            "translations-INITIAL_FORMS": "0",
            "translations-MIN_NUM_FORMS": "0",
            "translations-MAX_NUM_FORMS": "1000",
            "translations-0-language": "en",
            "translations-0-status": "published",
            "translations-0-title": "Vendor agreement",
            "translations-0-body": "The terms.",
        },
    )
    assert response.status_code == 302, response.context["errors"] if response.context else ""
    contract = VendorContract.objects.get()
    assert contract.version == 1
    assert contract.translations.get().title == "Vendor agreement"
    entry = AuditLog.objects.get(action="vendor.contract_saved")
    assert entry.source == "admin"
    assert entry.after == {
        "version": 1,
        "valid_from": "2026-01-01T00:00:00+00:00",
        "note": "Launch terms",
    }


def test_a_version_starting_before_the_latest_one_is_a_form_error(staff_client: Client) -> None:
    VendorContractFactory.create()  # in force since yesterday
    response = staff_client.post(
        reverse("admin:vendors_vendorcontract_add"),
        {
            "valid_from_0": "2020-01-01",
            "valid_from_1": "00:00:00",
            "translations-TOTAL_FORMS": "0",
            "translations-INITIAL_FORMS": "0",
            "translations-MIN_NUM_FORMS": "0",
            "translations-MAX_NUM_FORMS": "1000",
        },
    )
    assert response.status_code == 200
    assert "the start of the latest version" in response.content.decode()
    assert VendorContract.objects.count() == 1


def test_an_accepted_contract_is_read_only_in_the_admin(staff_client: Client) -> None:
    contract = ContractAcceptanceFactory.create().contract
    url = reverse("admin:vendors_vendorcontract_change", args=[contract.pk])
    page = staff_client.get(url)
    assert page.status_code == 200
    assert 'name="note"' not in page.content.decode()
    assert staff_client.post(url, {"note": "Rewritten"}).status_code == 403
    delete = reverse("admin:vendors_vendorcontract_delete", args=[contract.pk])
    assert staff_client.post(delete, {"post": "yes"}).status_code == 403


def test_acceptances_are_listed_and_never_written_from_the_admin(staff_client: Client) -> None:
    acceptance = ContractAcceptanceFactory.create()
    listing = staff_client.get(reverse("admin:vendors_contractacceptance_changelist"))
    assert listing.status_code == 200
    assert acceptance.vendor.display_name in listing.content.decode()
    assert staff_client.get(reverse("admin:vendors_contractacceptance_add")).status_code == 403
    change = reverse("admin:vendors_contractacceptance_change", args=[acceptance.pk])
    assert staff_client.get(change).status_code == 200  # view only
    assert staff_client.post(change, {"language": "de"}).status_code == 403
    delete = reverse("admin:vendors_contractacceptance_delete", args=[acceptance.pk])
    assert staff_client.post(delete, {"post": "yes"}).status_code == 403


def test_the_vendor_page_shows_the_onboarding_checklist(staff_client: Client) -> None:
    contract = VendorContractFactory.create()
    vendor = VendorFactory.create(status="onboarding")
    url = reverse("admin:vendors_vendor_change", args=[vendor.pk])
    checklist = staff_client.get(url).content.decode()
    assert f"Contract version {contract.version}: not accepted" in checklist
    assert "Blocks activation" in checklist
    assert "DAC7 data: missing" in checklist

    ContractAcceptanceFactory.create(vendor=vendor, contract=contract)
    checklist = staff_client.get(url).content.decode()
    assert f"Contract version {contract.version}: accepted" in checklist
    assert "Ready to activate" in checklist
