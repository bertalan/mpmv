"""AuditLog is append-only at the database level; dry-run never writes (spec 01, SEC.4)."""

from decimal import Decimal

import pytest
from django.db import DatabaseError, transaction

from apps.core.audit import AuditContext, record
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog
from apps.core.services import run_mutation
from apps.core.tests.factories import AuditLogFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=7, actor_label="staff:alice", source="admin", ip="127.0.0.1")


def _changeset() -> ChangeSet:
    cs = ChangeSet()
    cs.update("tax.taxrate", "IT-standard", {"rate": Decimal("22.00")}, {"rate": Decimal("23.00")})
    return cs


def test_record_writes_who_what_before_after_and_where() -> None:
    entry = record(
        CTX, "tax.rate.updated", "tax.taxrate", "IT-standard", {"rate": "22.00"}, {"rate": "23.00"}
    )
    stored = AuditLog.objects.get(pk=entry.pk)
    assert stored.actor_label == "staff:alice"
    assert stored.actor_id == 7
    assert stored.source == "admin"
    assert stored.ip == "127.0.0.1"
    assert stored.before == {"rate": "22.00"}
    assert stored.after == {"rate": "23.00"}
    assert stored.public_id is not None


def test_audit_rows_cannot_be_updated_even_with_a_raw_queryset() -> None:
    entry = AuditLogFactory.create()
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        AuditLog.objects.filter(pk=entry.pk).update(action="tampered")


def test_audit_rows_cannot_be_deleted() -> None:
    entry = AuditLogFactory.create()
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        AuditLog.objects.filter(pk=entry.pk).delete()


def test_saving_an_existing_audit_row_is_refused_in_python_too() -> None:
    entry = AuditLogFactory.create()
    entry.action = "tampered"
    with pytest.raises(PermissionError):
        entry.save()


def test_dry_run_returns_the_changeset_without_applying_or_auditing() -> None:
    applied: list[bool] = []
    result = run_mutation(
        _changeset(), lambda: applied.append(True), ctx=CTX, action="tax.rate.updated", dry_run=True
    )
    assert result.summary()["update"] == 1
    assert applied == []
    assert AuditLog.objects.count() == 0


def test_real_run_applies_and_writes_one_audit_row_per_change() -> None:
    applied: list[bool] = []
    run_mutation(
        _changeset(),
        lambda: applied.append(True),
        ctx=CTX,
        action="tax.rate.updated",
        dry_run=False,
    )
    assert applied == [True]
    entry = AuditLog.objects.get()
    assert entry.action == "tax.rate.updated"
    assert entry.target_model == "tax.taxrate"
    assert entry.target_key == "IT-standard"
    assert entry.before == {"rate": "22.00"}
    assert entry.after == {"rate": "23.00"}


def test_failed_apply_leaves_no_audit_trace() -> None:
    def boom() -> None:
        raise RuntimeError("apply failed")

    with pytest.raises(RuntimeError):
        run_mutation(_changeset(), boom, ctx=CTX, action="tax.rate.updated", dry_run=False)
    assert AuditLog.objects.count() == 0
