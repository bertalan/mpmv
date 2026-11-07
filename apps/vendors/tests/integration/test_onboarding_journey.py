"""VEN.5 "Done when": a test vendor is activated end to end.

Application → staff approval → the owner is forced to enrol a second factor (CORE.4) →
the owner accepts the current contract → staff activation, with every step in the audit log.
The vendor-facing pages (public application form, onboarding page) belong to the vendor area
(CORE.9): the vendor steps call the services those pages will call, with the context a request
gives (`user_context` with the client IP).
"""

from collections.abc import Callable
from urllib.parse import urlsplit

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import password_login, verified_login
from apps.core.audit import user_context
from apps.core.models import AuditLog
from apps.vendors.models import ContractAcceptance, Vendor
from apps.vendors.services import accept_contract, submit_application
from apps.vendors.tests.factories import VendorContractFactory

pytestmark = pytest.mark.django_db

MfaLogin = Callable[[Client, User], Client]
APPLICANT_IP = "203.0.113.7"


def run_action(staff: Client, action: str, vendor: Vendor) -> list[str]:
    response = staff.post(
        reverse("admin:vendors_vendor_changelist"),
        {"action": action, "_selected_action": [vendor.pk]},
        follow=True,
    )
    assert response.status_code == 200
    return [str(message) for message in response.context["messages"]]


def test_a_test_vendor_is_activated_end_to_end(
    client: Client, superuser: User, mfa_login: MfaLogin
) -> None:
    contract = VendorContractFactory.create(languages=["en", "it"])
    applicant = UserFactory.create()

    # 1. The application: an applicant vendor owned by the applicant.
    submit_application(
        Vendor(
            legal_name="Vendor A S.r.l.",
            display_name="Vendor A",
            country="IT",
            contact_email="vendor-a@example.com",
        ),
        applicant,
        ctx=user_context(applicant, APPLICANT_IP),
    )
    vendor = Vendor.objects.get()
    assert vendor.status == Vendor.Status.APPLICANT

    # 2. As an owner, the applicant cannot use the site without a second factor.
    owner = Client()
    password_login(owner, applicant)
    response = owner.get("/")
    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == reverse("mfa:setup")
    verified_login(owner, applicant)
    assert owner.get("/").status_code == 200

    # 3. Staff approve the application.
    staff = mfa_login(client, superuser)
    run_action(staff, "move_to_onboarding", vendor)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ONBOARDING

    # 4. Activation waits for the contract.
    messages = run_action(staff, "move_to_active", vendor)
    assert any("contract" in message for message in messages)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ONBOARDING

    # 5. The owner accepts the current contract, in Italian.
    accept_contract(vendor, applicant, language="it", ctx=user_context(applicant, APPLICANT_IP))
    acceptance = ContractAcceptance.objects.get(vendor=vendor)
    assert (acceptance.contract, acceptance.language, acceptance.ip) == (contract, "it", APPLICANT_IP)

    # 6. Staff activate the vendor.
    run_action(staff, "move_to_active", vendor)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ACTIVE

    trail = AuditLog.objects.filter(vendor_key=str(vendor.public_id)).order_by("id")
    assert [(entry.action, entry.target_model) for entry in trail] == [
        ("vendor.application_submitted", "vendors.vendor"),
        ("vendor.application_submitted", "vendors.vendormember"),
        ("vendor.status_changed", "vendors.vendor"),
        ("vendor.contract_accepted", "vendors.contractacceptance"),
        ("vendor.status_changed", "vendors.vendor"),
    ]
    assert [entry.after["status"] for entry in trail if entry.action.endswith("status_changed")] == [
        "onboarding",
        "active",
    ]
