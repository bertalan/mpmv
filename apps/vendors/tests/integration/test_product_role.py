"""SEO.10 1/2: the vendor's default product role, set by the owner through an audited service."""

import secrets

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.services import set_default_product_role
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="vendor:owner", source=AuditLog.Source.VENDOR)


def test_the_owner_sets_the_role_and_it_is_audited() -> None:
    member = VendorMemberFactory.create(role="owner")
    vendor = member.vendor
    changes = set_default_product_role(vendor, "producer", actor=member.user, ctx=CTX)
    assert not changes.is_empty
    vendor.refresh_from_db()
    assert vendor.default_product_role == "producer"
    entry = AuditLog.objects.get(action="vendor.updated")
    assert entry.before == {"default_product_role": ""}
    assert entry.after == {"default_product_role": "producer"}


def test_dry_run_and_an_unchanged_value_write_nothing() -> None:
    member = VendorMemberFactory.create(role="owner")
    set_default_product_role(member.vendor, "reseller", actor=member.user, ctx=CTX, dry_run=True)
    member.vendor.refresh_from_db()
    assert member.vendor.default_product_role == ""
    assert not AuditLog.objects.exists()


def test_staff_members_and_bad_values_are_refused() -> None:
    staff = VendorMemberFactory.create(role="staff")
    with pytest.raises(PermissionDenied):
        set_default_product_role(staff.vendor, "producer", actor=staff.user, ctx=CTX)
    owner = VendorMemberFactory.create(role="owner")
    with pytest.raises(ValidationError):
        set_default_product_role(owner.vendor, secrets.token_urlsafe(4), actor=owner.user, ctx=CTX)


def test_an_owner_cannot_change_another_vendors_role() -> None:
    owner = VendorMemberFactory.create(role="owner")
    other = VendorMemberFactory.create(role="owner").vendor
    with pytest.raises(PermissionDenied):
        set_default_product_role(other, "producer", actor=owner.user, ctx=CTX)
    assert Vendor.objects.get(pk=other.pk).default_product_role == ""


@pytest.mark.django_db(transaction=True)
def test_the_migrations_are_reversible() -> None:
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    executor.migrate(
        [("vendors", "0035_return_policy_version"), ("catalog", "0018_variant_identifiers")]
    )
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([n for n in latest if n[0] in ("vendors", "catalog")])
