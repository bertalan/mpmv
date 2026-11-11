"""CORE.9 part 3: the platform space page where staff review and revert storefront changes."""

import pytest
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.manager import roles
from apps.vendors.models import Vendor, VendorTranslation
from apps.vendors.services import (
    PROFILE_REVERT_PERMISSION,
    save_storefront_profile,
    set_vendor_logo,
)
from apps.vendors.tests.factories import VendorTranslationFactory
from tests.upload_corpus import png

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
    assert client.get(PAGE).status_code == 403
    assert client.post(revert_url(changed), {"reason": "x"}).status_code == 403
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


def test_the_revert_asks_for_a_recent_second_factor(
    client: Client, reviewer: User, changed: AuditLog, vendor: Vendor
) -> None:
    verified_login(client, reviewer, verified_at=1.0)  # verified, but long ago
    response = client.post(revert_url(changed), {"reason": "Misleading policy"})
    assert response.status_code == 302
    assert "/mfa/" in response["Location"]
    assert VendorTranslation.objects.get(vendor=vendor, language="en").tagline == "New tagline"
    assert not AuditLog.objects.filter(action="vendor.profile_reverted").exists()


def test_a_malformed_entry_does_not_break_the_page(client: Client, reviewer: User) -> None:
    AuditLog.objects.create(
        actor_label="x",
        source="vendor",
        action="vendor.profile_changed",
        target_model="vendors.vendortranslation",
        target_key="k",
        before=None,
        after={"tagline": "x"},
        vendor_key="not-a-uuid",
    )
    verified_login(client, reviewer)
    assert client.get(PAGE).status_code == 200


def test_a_revert_marks_the_change_reverted_without_a_limit_on_the_list(
    client: Client, reviewer: User, changed: AuditLog
) -> None:
    verified_login(client, reviewer)
    client.post(revert_url(changed), {"reason": "Misleading policy"})
    page = client.get(PAGE).text
    assert "Reverted" in page
    assert f'action="{revert_url(changed)}"' not in page


def test_the_reviewer_reverts_a_logo_change(
    client: Client, reviewer: User, vendor: Vendor, owner: User
) -> None:
    set_vendor_logo(
        vendor,
        upload=SimpleUploadedFile("logo.png", png()),
        alt="Shop mark",
        actor=owner,
        ctx=CTX,
    )
    entry = AuditLog.objects.get(action="vendor.logo_changed")
    verified_login(client, reviewer)
    page = client.get(PAGE).text
    assert "Shop mark" in page
    assert f'action="{revert_url(entry)}"' in page
    client.post(revert_url(entry), {"reason": "Not the vendor's own logo."}, follow=True)
    vendor.refresh_from_db()
    assert vendor.logo is None
    assert AuditLog.objects.filter(action="vendor.logo_reverted").count() == 1


@pytest.mark.parametrize("key", [roles.CATALOG, roles.OWNER])
def test_the_catalog_and_owner_roles_review_and_revert(
    client: Client, changed: AuditLog, vendor: Vendor, key: str
) -> None:
    member = UserFactory.create(staff=True)
    member.groups.add(Group.objects.get(name=roles.role(key).group))
    verified_login(client, member)
    assert client.get(PAGE).status_code == 200
    client.post(revert_url(changed), {"reason": "Not acceptable."}, follow=True)
    assert VendorTranslation.objects.get(vendor=vendor, language="en").tagline == "Old tagline"


@pytest.mark.parametrize("key", [roles.CONFIG, roles.FINANCE, roles.SUPPORT])
def test_the_other_platform_roles_get_404(client: Client, changed: AuditLog, key: str) -> None:
    member = UserFactory.create(staff=True)
    member.groups.add(Group.objects.get(name=roles.role(key).group))
    verified_login(client, member)
    assert client.get(PAGE).status_code == 404
    assert client.post(revert_url(changed), {"reason": "x"}).status_code == 404


# --- CORE.25: the revert is a group 2 action (needs operational mode)


@pytest.fixture
def logo_entry(vendor: Vendor, owner: User) -> AuditLog:
    set_vendor_logo(
        vendor,
        upload=SimpleUploadedFile("logo.png", png()),
        alt="Shop mark",
        actor=owner,
        ctx=CTX,
    )
    return AuditLog.objects.get(action="vendor.logo_changed")


def test_a_restricted_session_cannot_revert_a_text_change(
    client: Client, reviewer: User, changed: AuditLog, vendor: Vendor
) -> None:
    verified_login(client, reviewer, level=levels.RESTRICTED)
    response = client.post(revert_url(changed), {"reason": "Not acceptable."})
    assert response.status_code == 403
    assert VendorTranslation.objects.get(vendor=vendor, language="en").tagline == "New tagline"
    assert not AuditLog.objects.filter(action="vendor.profile_reverted").exists()


def test_a_restricted_session_cannot_revert_a_logo_change(
    client: Client, reviewer: User, logo_entry: AuditLog, vendor: Vendor
) -> None:
    verified_login(client, reviewer, level=levels.RESTRICTED)
    assert client.post(revert_url(logo_entry), {"reason": "Not theirs."}).status_code == 403
    vendor.refresh_from_db()
    assert vendor.logo is not None
    assert not AuditLog.objects.filter(action="vendor.logo_reverted").exists()


def test_an_operational_session_reverts_both_kinds(
    client: Client, reviewer: User, changed: AuditLog, logo_entry: AuditLog, vendor: Vendor
) -> None:
    verified_login(client, reviewer, level=levels.OPERATIONAL)
    client.post(revert_url(changed), {"reason": "Not acceptable."})
    client.post(revert_url(logo_entry), {"reason": "Not theirs."})
    assert AuditLog.objects.filter(action="vendor.profile_reverted").count() == 1
    assert AuditLog.objects.filter(action="vendor.logo_reverted").count() == 1


def test_the_reason_field_warns_against_personal_data(
    client: Client, reviewer: User, changed: AuditLog
) -> None:
    verified_login(client, reviewer)
    assert "personal data" in client.get(PAGE).text
