"""SEO.6 2/3: logo and profile revert purge the public page cache (the storefront shows them)."""

from collections.abc import Callable
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core import public_cache
from apps.core.audit import AuditContext
from apps.core.i18n import primary_language
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.services import (
    PROFILE_REVERT_PERMISSION,
    remove_vendor_logo,
    revert_profile_change,
    save_storefront_profile,
    set_vendor_logo,
)
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.upload_corpus import png

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="user:owner@example.com", source="vendor")
STAFF_CTX = AuditContext(actor_label="user:staff@example.com", source="admin")


@pytest.fixture
def purges(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(public_cache, "_listeners", [seen.append])
    return seen


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create()


@pytest.fixture
def owner(vendor: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor, role="owner").user


def test_setting_and_removing_the_logo_purge(
    vendor: Vendor,
    owner: User,
    purges: list[str],
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        set_vendor_logo(
            vendor, upload=SimpleUploadedFile("logo.png", png()), alt="Mark", actor=owner, ctx=CTX
        )
    assert purges
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        remove_vendor_logo(vendor, actor=owner, ctx=CTX)
    assert purges


def test_a_logo_dry_run_purges_nothing(
    vendor: Vendor,
    owner: User,
    purges: list[str],
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        set_vendor_logo(
            vendor,
            upload=SimpleUploadedFile("logo.png", png()),
            alt="Mark",
            actor=owner,
            ctx=CTX,
            dry_run=True,
        )
    assert purges == []


def test_a_profile_revert_purges(
    vendor: Vendor,
    owner: User,
    purges: list[str],
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    staff = UserFactory.create(permissions=[PROFILE_REVERT_PERMISSION])
    with django_capture_on_commit_callbacks(execute=True):
        save_storefront_profile(
            vendor, primary_language(), {"tagline": "New tagline"}, actor=owner, ctx=CTX
        )
    entry = AuditLog.objects.filter(action="vendor.profile_changed").latest("id")
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        revert_profile_change(entry.public_id, "Spam link.", actor=staff, ctx=STAFF_CTX)
    assert purges
