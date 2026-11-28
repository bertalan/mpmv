"""SEO.6 2b: vendor saves, deletes, business-only and vendor flags purge the public pages."""

from collections.abc import Callable
from typing import Any

import pytest

from apps.accounts.tests.factories import UserFactory
from apps.core import public_cache
from apps.core.audit import AuditContext
from apps.core.tests.factories import FeatureFlagFactory
from apps.vendors.models import VendorFeatureFlag
from apps.vendors.services import (
    delete_vendor,
    delete_vendor_flag,
    save_vendor,
    save_vendor_flag,
    set_business_only,
)
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def purges(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(public_cache, "_listeners", [seen.append])
    return seen


CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="10.0.0.1")
Commit = Callable[..., Any]


def test_save_vendor_purges_but_a_dry_run_does_not(
    purges: list[str], django_capture_on_commit_callbacks: Commit
) -> None:
    vendor = VendorFactory.create()
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        save_vendor(vendor, ctx=CTX, dry_run=True)
    assert purges == []
    with django_capture_on_commit_callbacks(execute=True):
        save_vendor(vendor, ctx=CTX)
    assert purges == ["vendor"]


def test_delete_vendor_purges(
    purges: list[str], django_capture_on_commit_callbacks: Commit
) -> None:
    vendor = VendorFactory.create()
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        delete_vendor(vendor, ctx=CTX)
    assert "vendor" in purges


def test_set_business_only_purges(
    purges: list[str], django_capture_on_commit_callbacks: Commit
) -> None:
    vendor = VendorFactory.create()
    staff = UserFactory.create(staff=True, permissions=["vendors.change_vendor"])
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        set_business_only(vendor, True, actor=staff, reason="VAT number checked", ctx=CTX)
    assert purges == ["vendor"]


def test_vendor_flags_purge(purges: list[str], django_capture_on_commit_callbacks: Commit) -> None:
    vendor = VendorFactory.create()
    override = VendorFeatureFlag(vendor=vendor, flag=FeatureFlagFactory.create(), enabled=False)
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        save_vendor_flag(override, ctx=CTX)
    assert purges == ["vendor flag"]
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        delete_vendor_flag(override, ctx=CTX)
    assert purges == ["vendor flag"]
