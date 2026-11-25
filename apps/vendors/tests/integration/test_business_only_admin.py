"""VEN.41 (slice 2): staff set or clear the business-only flag from the vendor's admin page,
giving a mandatory reason, behind a fresh second factor like the reopen page."""

from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
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

    response = staff_client.post(flag_url(vendor), {"reason": REASON, "value": "on"}, follow=True)

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

    staff_client.post(flag_url(vendor), {"reason": REASON, "value": "off"})
    vendor.refresh_from_db()
    assert vendor.business_only is False

    html = staff_client.get(change_url(vendor)).content.decode()
    assert "Mark as business-only" in html
    assert REASON in html


def test_a_change_without_a_reason_is_a_form_error(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    response = staff_client.post(flag_url(vendor), {"reason": "   ", "value": "on"})
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
    assert client.post(flag_url(vendor), {"reason": REASON, "value": "on"}).status_code == 403
    vendor.refresh_from_db()
    assert vendor.business_only is False
    assert not AuditLog.objects.filter(action=BUSINESS_ONLY_CHANGED).exists()


def test_a_double_submit_does_not_flip_the_flag_back(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    data = {"reason": REASON, "value": "on"}
    staff_client.post(flag_url(vendor), data)
    response = staff_client.post(flag_url(vendor), data, follow=True)
    assert any("already marked" in str(m) for m in response.context["messages"])
    vendor.refresh_from_db()
    assert vendor.business_only is True
    assert AuditLog.objects.filter(action=BUSINESS_ONLY_CHANGED).count() == 1


def test_the_form_carries_the_value_staff_saw(staff_client: Client) -> None:
    vendor = VendorFactory.create(business_only=True)
    html = staff_client.get(flag_url(vendor)).content.decode()
    assert 'name="value"' in html
    assert 'value="off"' in html


def test_a_missing_or_unknown_value_changes_nothing(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    for data in ({"reason": REASON}, {"reason": REASON, "value": "toggle"}):
        assert staff_client.post(flag_url(vendor), data).status_code == 200
    vendor.refresh_from_db()
    assert vendor.business_only is False
    assert not AuditLog.objects.filter(action=BUSINESS_ONLY_CHANGED).exists()


def test_a_non_staff_user_is_sent_to_the_admin_login(client: Client) -> None:
    vendor = VendorFactory.create()
    client.force_login(UserFactory.create())
    response = client.post(flag_url(vendor), {"reason": REASON, "value": "on"})
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("admin:login"))
    vendor.refresh_from_db()
    assert vendor.business_only is False


def test_the_page_needs_a_fresh_second_factor(client: Client, superuser: User) -> None:
    vendor = VendorFactory.create()
    TOTPDeviceFactory.create(user=superuser)  # enrolled, but not verified in this session
    client.force_login(superuser)
    get = client.get(flag_url(vendor))
    assert get.status_code == 302
    assert get["Location"].startswith(reverse("mfa:verify"))
    post = client.post(flag_url(vendor), {"reason": REASON, "value": "on"})
    assert post.status_code == 302
    assert post["Location"].startswith(reverse("mfa:verify"))
    vendor.refresh_from_db()
    assert vendor.business_only is False
