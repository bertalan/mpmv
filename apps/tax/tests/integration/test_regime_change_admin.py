"""TAX.2: staff review of regime change requests in the admin (parity with the legacy review
screen): pending requests listed with a notice, the vendor's history next to the request,
approval and rejection behind a fresh second factor and the review permission."""

import time
from collections.abc import Callable
from urllib.parse import urlsplit

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.tests.factories import utc
from apps.tax import engine
from apps.tax.models import TaxRegimeChangeRequest, VendorTaxProfile
from apps.tax.tests.factories import TaxRegimeChangeRequestFactory, VendorTaxProfileFactory

pytestmark = pytest.mark.django_db

MfaLogin = Callable[[Client, User], Client]


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def approve_url(request: TaxRegimeChangeRequest) -> str:
    return reverse("admin:tax_taxregimechangerequest_approve", args=[request.pk])


def reject_url(request: TaxRegimeChangeRequest) -> str:
    return reverse("admin:tax_taxregimechangerequest_reject", args=[request.pk])


def change_url(request: TaxRegimeChangeRequest) -> str:
    return reverse("admin:tax_taxregimechangerequest_change", args=[request.pk])


def test_pending_requests_are_listed_with_a_notice(staff_client: Client) -> None:
    pending = TaxRegimeChangeRequestFactory.create()
    TaxRegimeChangeRequestFactory.create(rejected=True)
    response = staff_client.get(reverse("admin:tax_taxregimechangerequest_changelist"))
    assert response.status_code == 200
    content = response.content.decode()
    assert str(pending.vendor) in content
    assert "1 tax regime change request is waiting for review" in content


def test_the_request_page_shows_the_vendor_history_and_the_review_links(
    staff_client: Client,
) -> None:
    earlier = VendorTaxProfileFactory.create(
        vendor__fiscal_regime="RF01", valid_from=utc(2025), valid_to=utc(2026)
    )
    vendor = earlier.vendor
    VendorTaxProfileFactory.create(vendor=vendor, valid_from=utc(2026))
    TaxRegimeChangeRequestFactory.create(
        vendor=vendor, rejected=True, review_reason="Missing registration."
    )
    request = TaxRegimeChangeRequestFactory.create(vendor=vendor)

    response = staff_client.get(change_url(request))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Domestic only" in content  # the current profile and the earlier version
    assert "2025-01-01" in content
    assert "2026-01-01" in content
    assert "Missing registration." in content  # the earlier, rejected request
    assert approve_url(request) in content
    assert reject_url(request) in content


def test_staff_approve_a_request_from_the_admin(staff_client: Client, superuser: User) -> None:
    request = TaxRegimeChangeRequestFactory.create()
    assert staff_client.get(approve_url(request)).status_code == 200

    response = staff_client.post(approve_url(request), {"reason": "Registration checked."})
    assert response.status_code == 302
    assert response["Location"] == change_url(request)
    request.refresh_from_db()
    assert request.status == engine.APPROVED
    assert request.reviewed_by == superuser
    profile = VendorTaxProfile.objects.get(vendor=request.vendor)
    assert profile.approved_by == superuser
    assert profile.regime == "oss"


def test_a_rejection_from_the_admin_needs_a_reason(staff_client: Client) -> None:
    request = TaxRegimeChangeRequestFactory.create()
    response = staff_client.post(reject_url(request), {"reason": ""})
    assert response.status_code == 200
    assert "reason" in response.context["form"].errors
    request.refresh_from_db()
    assert request.status == engine.PENDING

    response = staff_client.post(reject_url(request), {"reason": "No OSS number."})
    assert response.status_code == 302
    request.refresh_from_db()
    assert (request.status, request.review_reason) == (engine.REJECTED, "No OSS number.")


def test_a_refused_approval_is_shown_on_the_review_page(staff_client: Client) -> None:
    request = TaxRegimeChangeRequestFactory.create(  # a fiscal regime change, not due yet
        regime="exempt",
        exempt_reason="flat_rate_forfettario",
        oss_registered_since=None,
        effective_from=utc(2099),
    )

    response = staff_client.post(approve_url(request), {"reason": ""})
    assert response.status_code == 200
    assert "2099-01-01" in response.content.decode()
    request.refresh_from_db()
    assert request.status == engine.PENDING


def test_a_reviewed_request_cannot_be_reviewed_from_the_admin(staff_client: Client) -> None:
    request = TaxRegimeChangeRequestFactory.create(rejected=True)
    response = staff_client.post(approve_url(request), {"reason": ""})
    assert response.status_code == 302
    request.refresh_from_db()
    assert request.status == engine.REJECTED
    assert VendorTaxProfile.objects.count() == 0


def test_reviews_ask_for_the_second_factor_again_after_a_while(
    client: Client, superuser: User
) -> None:
    request = TaxRegimeChangeRequestFactory.create()
    verified_login(client, superuser, verified_at=time.time() - 3600)
    for url in (approve_url(request), reject_url(request)):
        response = client.post(url, {"reason": "Checked."})
        assert response.status_code == 302
        assert urlsplit(response["Location"]).path == reverse("mfa:verify")
    request.refresh_from_db()
    assert request.status == engine.PENDING


def test_staff_without_the_review_permission_cannot_review(client: Client) -> None:
    viewer = UserFactory.create(staff=True, permissions=["tax.view_taxregimechangerequest"])
    verified_login(client, viewer)
    request = TaxRegimeChangeRequestFactory.create()
    assert client.get(change_url(request)).status_code == 200
    assert client.post(approve_url(request), {"reason": ""}).status_code == 403
    assert client.post(reject_url(request), {"reason": "No."}).status_code == 403
    request.refresh_from_db()
    assert request.status == engine.PENDING


def test_requests_cannot_be_added_changed_or_deleted_in_the_admin(staff_client: Client) -> None:
    request = TaxRegimeChangeRequestFactory.create()
    assert staff_client.get(reverse("admin:tax_taxregimechangerequest_add")).status_code == 403
    assert staff_client.post(change_url(request), {"regime": "exempt"}).status_code == 403
    delete = reverse("admin:tax_taxregimechangerequest_delete", args=[request.pk])
    assert staff_client.post(delete, {"post": "yes"}).status_code == 403
