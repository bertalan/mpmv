"""VEN.21: staff reopen a closed vendor from its admin page, giving a mandatory reason."""

from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

MfaLogin = Callable[[Client, User], Client]
REASON = "The owner asked to sell again"


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def closed_vendor() -> Vendor:
    vendor = VendorFactory.create(status=Vendor.Status.CLOSED)
    VendorMemberFactory.create(vendor=vendor)
    return vendor


def reopen_url(vendor: Vendor) -> str:
    return reverse("admin:vendors_vendor_reopen", args=[vendor.pk])


def test_the_page_of_a_closed_vendor_links_to_the_reopen_page(staff_client: Client) -> None:
    vendor = closed_vendor()
    page = staff_client.get(reverse("admin:vendors_vendor_change", args=[vendor.pk]))
    assert reopen_url(vendor) in page.content.decode()

    active = VendorFactory.create(status=Vendor.Status.ACTIVE)
    page = staff_client.get(reverse("admin:vendors_vendor_change", args=[active.pk]))
    assert reopen_url(active) not in page.content.decode()


def test_staff_reopen_a_closed_vendor_with_a_reason(staff_client: Client) -> None:
    vendor = closed_vendor()
    form = staff_client.get(reopen_url(vendor))
    assert form.status_code == 200
    assert 'name="reason"' in form.content.decode()

    response = staff_client.post(reopen_url(vendor), {"reason": REASON}, follow=True)

    assert response.status_code == 200
    assert any("onboarding" in str(m) for m in response.context["messages"])
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ONBOARDING
    entry = AuditLog.objects.get(action="vendor.reopened")
    assert entry.after is not None
    assert entry.after["reason"] == REASON
    assert entry.source == "admin"
    assert entry.actor_label == "staff:staff@example.com"


def test_a_reopen_without_a_reason_is_a_form_error(staff_client: Client) -> None:
    vendor = closed_vendor()
    response = staff_client.post(reopen_url(vendor), {"reason": "   "})
    assert response.status_code == 200
    assert "errorlist" in response.content.decode()
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.CLOSED
    assert not AuditLog.objects.filter(target_model="vendors.vendor").exists()


def test_a_vendor_that_is_not_closed_is_not_reopened(staff_client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    response = staff_client.post(reopen_url(vendor), {"reason": REASON}, follow=True)
    assert response.status_code == 200
    assert any("not closed" in str(m) for m in response.context["messages"])
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.SUSPENDED
    assert not AuditLog.objects.filter(target_model="vendors.vendor").exists()


def test_view_only_staff_cannot_reopen(client: Client) -> None:
    vendor = closed_vendor()
    viewer = UserFactory.create(staff=True, permissions=["vendors.view_vendor"])
    verified_login(client, viewer)
    assert client.get(reopen_url(vendor)).status_code == 403
    assert client.post(reopen_url(vendor), {"reason": REASON}).status_code == 403
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.CLOSED


def test_the_onboarding_status_action_does_not_reopen_a_closed_vendor(
    staff_client: Client,
) -> None:
    vendor = closed_vendor()
    response = staff_client.post(
        reverse("admin:vendors_vendor_changelist"),
        {"action": "move_to_onboarding", "_selected_action": [vendor.pk]},
        follow=True,
    )
    assert response.status_code == 200
    assert any("reason" in str(m) for m in response.context["messages"])
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.CLOSED
    assert not AuditLog.objects.filter(target_model="vendors.vendor").exists()
