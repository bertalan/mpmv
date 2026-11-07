"""CORE.14: apps above `vendors` add the rows a vendor deletion drops to its audited ChangeSet.

`vendors` cannot import them (spec 01 §1.3), so each one names a hook in
`settings.VENDOR_DELETION_AUDIT_HOOKS` (the API keys: `apps.api.services.vendor_key_deletions`).
"""

import pytest
from django.test import override_settings

from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.services import DELETED, delete_vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

SYSTEM = AuditContext(actor_label="system:test", source="system")
HOOK = f"{__name__}.dependent_rows"


def dependent_rows(vendor: Vendor, changes: ChangeSet) -> None:
    """A stand-in for an upper app: two rows that the vendor's deletion cascades to."""
    for n in (1, 2):
        changes.delete("tests.dependent", f"row-{n}", {"vendor": str(vendor.public_id)})


@override_settings(VENDOR_DELETION_AUDIT_HOOKS=[HOOK])
def test_each_hooked_row_gets_its_own_audit_entry() -> None:
    vendor = VendorFactory.create()
    changes = delete_vendor(vendor, ctx=SYSTEM)

    assert [c.key for c in changes.changes if c.model == "tests.dependent"] == ["row-1", "row-2"]
    entries = AuditLog.objects.filter(target_model="tests.dependent", action=DELETED)
    assert sorted(entries.values_list("target_key", flat=True)) == ["row-1", "row-2"]
    assert set(entries.values_list("vendor_key", flat=True)) == {str(vendor.public_id)}


@override_settings(VENDOR_DELETION_AUDIT_HOOKS=[])
def test_without_hooks_only_the_vendor_is_audited() -> None:
    delete_vendor(VendorFactory.create(), ctx=SYSTEM)
    assert list(AuditLog.objects.values_list("target_model", flat=True)) == ["vendors.vendor"]
