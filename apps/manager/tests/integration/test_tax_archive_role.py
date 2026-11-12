"""MIG.4 done-when: only the Tax/Audit role sees the restricted tax-only order archive."""

from typing import Any

import pytest
from django.contrib.auth.models import Group

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.manager import roles
from apps.manager.role_groups import user_roles
from apps.orders import archive
from apps.orders.tests.legacy_records import record
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)


def holder(key: str) -> User:
    user = UserFactory.create(staff=True)
    user.groups.add(Group.objects.get(name=roles.role(key).group))
    return User.objects.get(pk=user.pk)


@pytest.fixture
def loaded() -> Any:
    order, _ = archive.load_archive_order(record(), vendors={"A": VendorFactory.create()}, ctx=CTX)
    assert order is not None
    return order


@pytest.mark.parametrize("key", roles.ROLE_KEYS)
def test_only_the_tax_audit_role_sees_the_archive(key: str, loaded: Any) -> None:
    keys = user_roles(holder(key))
    assert keys == {key}
    assert list(archive.archive_orders(keys)) == ([loaded] if key == "tax_audit" else [])
    assert (archive.find_archive_order(keys, 4711) == loaded) is (key == "tax_audit")


def test_a_user_without_a_role_sees_nothing(loaded: Any) -> None:
    assert not archive.archive_orders(user_roles(UserFactory.create())).exists()


def test_a_vendor_member_in_the_tax_audit_group_sees_nothing(loaded: Any) -> None:
    from apps.vendors.tests.factories import VendorMemberFactory

    member = VendorMemberFactory.create().user
    member.groups.add(Group.objects.get(name=roles.role("tax_audit").group))
    assert not archive.archive_orders(user_roles(User.objects.get(pk=member.pk))).exists()


def test_the_archive_role_key_matches_the_roles_matrix() -> None:
    assert archive.ARCHIVE_ROLE == roles.TAX_AUDIT
