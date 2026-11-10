"""CORE.9 part 3: the platform space page where staff review and revert storefront changes."""

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.models import Vendor, VendorTranslation
from apps.vendors.services import PROFILE_REVERT_PERMISSION, save_storefront_profile
from apps.vendors.tests.factories import VendorTranslationFactory

from .conftest import PLATFORM, SHOP

pytestmark = pytest.mark.django_db

PAGE = f"{PLATFORM}profile-changes/"
CTX = AuditContext(actor_label="user:owner@example.com", source="vendor")


@pytest.fixture
def reviewer(db: None) -> User:
    return UserFactory.create(permissions=["core.platform_owner", PROFILE_REVERT_PERMISSION])


@pytest.fixture
def changed(vendor: Vendor, owner: User) -> AuditLog:
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="Old tagline")
    save_storefront_profile(vendor, "en", {"tagline": "New tagline"}, actor=owner, ctx=CTX)
    return AuditLog.objects.get(action="vendor.profile_changed")


def revert_url(entry: AuditLog) -> str:
    return f"{PAGE}{entry.public_id}/revert/"


def test_the_reviewer_sees_who_when_and_the_before_and_after(
    client: Client, reviewer: User, changed: AuditLog, vendor: Vendor
) -> None:
    verified_login(client, reviewer)
    page = client.get(PAGE).text
    assert "Old tagline" in page
    assert "New tagline" in page
    assert vendor.display_name in page
    assert changed.actor_label in page
    assert f'action="{revert_url(changed)}"' in page
    assert f'href="{PAGE}"' in client.get(PLATFORM).text


def test_the_page_can_be_filtered_by_vendor(
    client: Client, reviewer: User, changed: AuditLog, other_vendor: Vendor
) -> None:
    verified_login(client, reviewer)
    assert "New tagline" not in client.get(PAGE, {"vendor": str(other_vendor.public_id)}).text


def test_staff_without_the_revert_permission_get_404(
    client: Client, platform_staff: User, changed: AuditLog
) -> None:
    verified_login(client, platform_staff)
    assert client.get(PAGE).status_code == 404
    assert client.post(revert_url(changed), {"reason": "x"}).status_code == 404
    assert PAGE not in client.get(PLATFORM).text


def test_a_vendor_member_cannot_reach_the_page_or_the_revert(
    client: Client, owner: User, changed: AuditLog
) -> None:
    verified_login(client, owner)
    assert client.get(PAGE).status_code == 404
    assert client.post(revert_url(changed), {"reason": "x"}).status_code == 404
    assert "New tagline" not in client.get(SHOP).text
    assert PAGE not in client.get(SHOP).text


def test_the_revert_needs_a_reason(
    client: Client, reviewer: User, changed: AuditLog, vendor: Vendor
) -> None:
    verified_login(client, reviewer)
    response = client.post(revert_url(changed), {"reason": " "}, follow=True)
    assert response.status_code == 200
    assert VendorTranslation.objects.get(vendor=vendor, language="en").tagline == "New tagline"
    assert not AuditLog.objects.filter(action="vendor.profile_reverted").exists()


def test_the_reviewer_reverts_with_a_reason(
    client: Client, reviewer: User, changed: AuditLog, vendor: Vendor
) -> None:
    verified_login(client, reviewer)
    response = client.post(revert_url(changed), {"reason": "Misleading policy"})
    assert response.status_code == 302
    assert VendorTranslation.objects.get(vendor=vendor, language="en").tagline == "Old tagline"
    log = AuditLog.objects.get(action="vendor.profile_reverted")
    assert log.actor_id == reviewer.pk
    assert log.source == AuditLog.Source.ADMIN
    assert "reverted" in client.get(PAGE).text.lower()


def test_the_revert_is_post_only(client: Client, reviewer: User, changed: AuditLog) -> None:
    verified_login(client, reviewer)
    assert client.get(revert_url(changed)).status_code == 405
