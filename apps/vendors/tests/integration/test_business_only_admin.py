"""VEN.41 (slice 2): staff set or clear the business-only flag from the vendor's admin page,
giving a mandatory reason, behind a fresh second factor like the reopen page."""

from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.services import BUSINESS_ONLY_CHANGED
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

MfaLogin = Callable[[Client, User], Client]
REASON = "VAT number checked in VIES, the seller is a company"


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def flag_url(vendor: Vendor) -> str:
    return reverse("admin:vendors_vendor_business_only", args=[vendor.pk])


def change_url(vendor: Vendor) -> str:
    return reverse("admin:vendors_vendor_change", args=[vendor.pk])


def test_the_vendor_page_links_to_the_flag_page_and_shows_the_state(
    staff_client: Client,
) -> None:
    vendor = VendorFactory.create()
    html = staff_client.get(change_url(vendor)).content.decode()
    assert flag_url(vendor) in html
    assert "Mark as business-only" in html
    assert "No change recorded" in html


def test_staff_set_the_flag_with_a_reason(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    form = staff_client.get(flag_url(vendor))
    assert form.status_code == 200
    assert 'name="reason"' in form.content.decode()

    response = staff_client.post(flag_url(vendor), {"reason": REASON}, follow=True)

    assert response.status_code == 200
    assert any("business-only" in str(m) for m in response.context["messages"])
    vendor.refresh_from_db()
    assert vendor.business_only is True
    entry = AuditLog.objects.get(action=BUSINESS_ONLY_CHANGED)
    assert entry.after is not None
    assert entry.after["reason"] == REASON
    assert entry.source == "admin"
    assert entry.actor_label.startswith("staff:")


def test_staff_clear_the_flag_and_the_page_shows_the_last_change(staff_client: Client) -> None:
    vendor = VendorFactory.create(business_only=True)
    page = staff_client.get(flag_url(vendor)).content.decode()
    assert "Unmark" in page

    staff_client.post(flag_url(vendor), {"reason": REASON})
    vendor.refresh_from_db()
    assert vendor.business_only is False

    html = staff_client.get(change_url(vendor)).content.decode()
    assert "Mark as business-only" in html
    assert REASON in html


def test_a_change_without_a_reason_is_a_form_error(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    response = staff_client.post(flag_url(vendor), {"reason": "   "})
    assert response.status_code == 200
    assert "errorlist" in response.content.decode()
    vendor.refresh_from_db()
    assert vendor.business_only is False
    assert not AuditLog.objects.filter(action=BUSINESS_ONLY_CHANGED).exists()


def test_view_only_staff_cannot_change_the_flag(client: Client) -> None:
    vendor = VendorFactory.create()
    viewer = UserFactory.create(staff=True, permissions=["vendors.view_vendor"])
    verified_login(client, viewer)
    assert client.get(flag_url(vendor)).status_code == 403
    assert client.post(flag_url(vendor), {"reason": REASON}).status_code == 403
    vendor.refresh_from_db()
    assert vendor.business_only is False
    assert not AuditLog.objects.filter(action=BUSINESS_ONLY_CHANGED).exists()


def test_a_non_staff_user_is_refused(client: Client) -> None:
    vendor = VendorFactory.create()
    client.force_login(UserFactory.create())
    response = client.post(flag_url(vendor), {"reason": REASON})
    assert response.status_code in (302, 403)
    vendor.refresh_from_db()
    assert vendor.business_only is False


def test_the_page_needs_a_fresh_second_factor(client: Client, superuser: User) -> None:
    vendor = VendorFactory.create()
    client.force_login(superuser)  # signed in, no second factor verified in this session
    response = client.post(flag_url(vendor), {"reason": REASON})
    assert response.status_code in (302, 403)
    vendor.refresh_from_db()
    assert vendor.business_only is False
