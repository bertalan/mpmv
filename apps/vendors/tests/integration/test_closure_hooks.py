"""CORE.14: closing or reopening a vendor lets apps above `vendors` revoke what it held.

`vendors` cannot import them (spec 01 §1.3), so each one names a `hook(vendor, *, ctx,
dry_run) -> ChangeSet` in `settings.VENDOR_CLOSURE_HOOKS` (the API keys:
`apps.api.services.revoke_vendor_keys`). Its changes join the closure's ChangeSet.
"""

from typing import Any

import pytest
from django.test import override_settings

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.vendors.models import Vendor
from apps.vendors.services import change_status, reopen_vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

SYSTEM = AuditContext(actor_label="system:test", source="system")
HOOK = f"{__name__}.held_rows"
CALLS: list[tuple[str, bool]] = []


def held_rows(vendor: Vendor, *, ctx: AuditContext, dry_run: bool) -> ChangeSet:
    """A stand-in for an upper app: one row the vendor held, revoked on closure."""
    CALLS.append((str(vendor.public_id), dry_run))
    changes = ChangeSet()
    changes.update("tests.held", "row-1", {"is_active": True}, {"is_active": False})
    return changes


@pytest.fixture(autouse=True)
def _reset_calls() -> Any:
    CALLS.clear()
    yield
    CALLS.clear()


@override_settings(VENDOR_CLOSURE_HOOKS=[HOOK])
@pytest.mark.parametrize("dry_run", [False, True])
def test_a_closure_runs_each_hook_and_lists_its_changes(dry_run: bool) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)

    changes = change_status(vendor, Vendor.Status.CLOSED, ctx=SYSTEM, dry_run=dry_run)

    assert [(str(vendor.public_id), dry_run)] == CALLS
    assert [c.key for c in changes.changes if c.model == "tests.held"] == ["row-1"]


@override_settings(VENDOR_CLOSURE_HOOKS=[HOOK])
def test_other_transitions_run_no_hook() -> None:
    change_status(
        VendorFactory.create(status=Vendor.Status.ACTIVE), Vendor.Status.SUSPENDED, ctx=SYSTEM
    )
    assert CALLS == []


@override_settings(VENDOR_CLOSURE_HOOKS=[HOOK])
def test_a_reopen_runs_each_hook_again() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.CLOSED)
    VendorMemberFactory.create(vendor=vendor)
    staff = UserFactory.create(staff=True, permissions=["vendors.change_vendor"])

    changes = reopen_vendor(vendor, actor=staff, reason="Back after a break", ctx=SYSTEM)

    assert [(str(vendor.public_id), False)] == CALLS
    assert [c.key for c in changes.changes if c.model == "tests.held"] == ["row-1"]
