"""VEN.40 2/3: an approved return-policy version is frozen by the database itself."""

import pytest
from django.db import InternalError, ProgrammingError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import DatabaseError

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.vendors import return_services as rs
from apps.vendors.models import ReturnPolicyVersion
from apps.vendors.tests.factories import ReturnPolicyVersionFactory, VendorFactory
from apps.vendors.tests.integration.test_return_policy import (
    owner_of,
    staff,
    values,
)

CTX = AuditContext(actor_label="user:test", source="vendor")
REFUSED = (DatabaseError, InternalError, ProgrammingError)
MIGRATION = "0037_return_policy_version_frozen_once_approved"


def _approved() -> ReturnPolicyVersion:
    version = ReturnPolicyVersionFactory.create()
    rs.review_return_policy(version, approve=True, actor=staff(), ctx=CTX)
    version.refresh_from_db()
    assert version.status == "approved"
    return version


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change",
    [
        {"return_window_days": 99},
        {"return_shipping_paid_by": "vendor"},
        {"return_methods": ["store"]},
        {"returns_page_url": "https://other.example/r"},
        {"return_country": "DE"},
        {"status": "pending"},
        {"status": "rejected"},
        {"number": 77},
        {"review_note": "edited later"},
    ],
)
def test_a_raw_update_of_an_approved_version_is_refused(change: dict[str, object]) -> None:
    version = _approved()
    with pytest.raises(REFUSED, match="approved"), transaction.atomic():
        ReturnPolicyVersion.objects.filter(pk=version.pk).update(**change)


@pytest.mark.django_db
def test_a_delete_of_an_approved_version_is_refused() -> None:
    version = _approved()
    with pytest.raises(REFUSED, match="approved"), transaction.atomic():
        ReturnPolicyVersion.objects.filter(pk=version.pk).delete()
    assert ReturnPolicyVersion.objects.filter(pk=version.pk).exists()


@pytest.mark.django_db
def test_deleting_the_reviewer_only_clears_the_reviewer_column() -> None:
    """The GDPR/account deletion path: reviewed_by is SET_NULL, so that one update is allowed."""
    version = _approved()
    reviewer = version.reviewed_by
    assert reviewer is not None
    reviewer.delete()
    version.refresh_from_db()
    assert version.reviewed_by is None
    assert version.status == "approved"


@pytest.mark.django_db
def test_approve_and_a_newer_proposal_still_work_through_the_service() -> None:
    vendor = VendorFactory.create()
    actor = owner_of(vendor)
    rs.propose_return_policy(vendor, values(return_window_days=30), actor=actor, ctx=CTX)
    first = ReturnPolicyVersion.objects.get()
    rs.review_return_policy(first, approve=True, actor=staff(), ctx=CTX)
    rs.propose_return_policy(vendor, values(return_window_days=45), actor=actor, ctx=CTX)
    second = ReturnPolicyVersion.objects.get(number=2)
    rs.review_return_policy(second, approve=True, actor=staff(), ctx=CTX)
    public = rs.public_return_policy(vendor)
    assert public is not None
    assert public.return_window_days == 45
    assert ReturnPolicyVersion.objects.get(number=1).return_window_days == 30


@pytest.mark.django_db
def test_pending_and_rejected_versions_stay_editable_and_deletable() -> None:
    pending = ReturnPolicyVersionFactory.create()
    ReturnPolicyVersion.objects.filter(pk=pending.pk).update(return_window_days=20)
    rejected = ReturnPolicyVersionFactory.create()
    rs.review_return_policy(rejected, approve=False, actor=staff(), ctx=CTX, note="no")
    ReturnPolicyVersion.objects.filter(pk=rejected.pk).update(review_note="changed")
    ReturnPolicyVersion.objects.filter(pk__in=[pending.pk, rejected.pk]).delete()
    assert not ReturnPolicyVersion.objects.filter(pk__in=[pending.pk, rejected.pk]).exists()


@pytest.mark.django_db(transaction=True)
def test_the_migration_reverses_cleanly_and_applies_again() -> None:
    def migrate(target: str) -> None:
        MigrationExecutor(connection).migrate([("vendors", target)])

    def trigger_count() -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_trigger"
                " WHERE tgrelid = 'vendors_returnpolicyversion'::regclass AND NOT tgisinternal"
            )
            return int(cursor.fetchone()[0])

    assert trigger_count() == 1
    try:
        migrate("0036_vendor_role_fields")
        assert trigger_count() == 0
        user = UserFactory.create()
        assert user.pk
    finally:
        migrate(MIGRATION)
    assert trigger_count() == 1
