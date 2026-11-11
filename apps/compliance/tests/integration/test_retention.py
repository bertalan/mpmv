"""CMP.4 (2/2), D-29 and D-30: the retention job. It applies the lifecycle through the ORD.2 scrub
services (never rewriting an order itself), completes pending erasures, supports a dry run that
lists the candidates, and audits one entry per run with counts only."""

import datetime as dt

import pytest
from django.core.management import call_command

from apps.accounts.tests.factories import UserFactory
from apps.compliance import retention
from apps.compliance.models import ErasureRequest
from apps.compliance.tests.factories import ErasureRequestFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders.models import Order
from apps.orders.tests.integration.test_retention import ago, place

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
DAYS = 365


def test_an_order_past_the_active_phase_is_restricted_and_a_young_one_is_not() -> None:
    old = place(created=ago(days=26 * 30), state="closed", legacy_wp_order_id=8001)
    young = place(created=ago(days=20 * 30), state="closed", legacy_wp_order_id=8002)

    result = retention.run_retention(ctx=CTX)

    old.refresh_from_db()
    young.refresh_from_db()
    assert old.privacy_status == "restricted_tax_only"
    assert young.privacy_status == "active"
    assert result.restricted == 1


def test_an_order_is_anonymised_only_the_day_after_its_retention_date() -> None:
    due = place(created=ago(days=11 * DAYS + 30), state="closed", legacy_wp_order_id=8003)
    not_due = place(created=ago(days=9 * DAYS), state="closed", legacy_wp_order_id=8004)

    result = retention.run_retention(ctx=CTX)

    due.refresh_from_db()
    not_due.refresh_from_db()
    assert due.privacy_status == "anonymized"
    assert due.customer_name == "ANONYMISED"
    assert not_due.privacy_status == "restricted_tax_only"
    assert result.anonymised == 1


def test_a_run_never_changes_the_fiscal_snapshot_of_an_order_not_yet_due() -> None:
    order = place(created=ago(days=26 * 30), state="closed", legacy_wp_order_id=8005)
    totals = (order.total_gross, order.tax_retention_until, order.customer_name)

    retention.run_retention(ctx=CTX)

    order.refresh_from_db()
    assert (order.total_gross, order.tax_retention_until, order.customer_name) == totals


def test_dry_run_lists_the_candidates_and_changes_nothing() -> None:
    order = place(created=ago(days=26 * 30), state="closed", legacy_wp_order_id=8006)
    pending = ErasureRequestFactory.create()

    result = retention.run_retention(ctx=CTX, dry_run=True)

    order.refresh_from_db()
    assert order.privacy_status == "active"
    assert str(order.public_id) in result.candidates
    assert ErasureRequest.objects.get(pk=pending.pk).completed_at is None
    assert not AuditLog.objects.filter(action="compliance.retention_run").exists()


def test_a_pending_erasure_completes_when_its_last_order_has_closed() -> None:
    user = UserFactory.create()
    request = ErasureRequestFactory.create(user=user)

    result = retention.run_retention(ctx=CTX)

    request.refresh_from_db()
    assert request.completed_at is not None
    assert result.erasures_completed == 1


def test_a_pending_erasure_with_an_open_order_keeps_waiting() -> None:
    user = UserFactory.create()
    place(state="paid", customer=user, legacy_wp_order_id=None, paid_at=ago(days=1))
    request = ErasureRequestFactory.create(user=user)

    retention.run_retention(ctx=CTX)

    request.refresh_from_db()
    assert request.completed_at is None


def test_the_run_is_idempotent() -> None:
    place(created=ago(days=26 * 30), state="closed", legacy_wp_order_id=8007)
    first = retention.run_retention(ctx=CTX)
    second = retention.run_retention(ctx=CTX)
    assert first.restricted == 1
    assert (second.restricted, second.anonymised, second.erasures_completed) == (0, 0, 0)


def test_each_run_writes_one_audit_entry_with_counts_only() -> None:
    place(
        created=ago(days=26 * 30),
        state="closed",
        legacy_wp_order_id=8008,
        email="buyer.private@example.com",
    )
    retention.run_retention(ctx=CTX)

    [entry] = AuditLog.objects.filter(action="compliance.retention_run")
    assert entry.after["restricted"] == 1
    assert "buyer.private" not in str(entry.after) + str(entry.before) + entry.actor_label


def test_the_command_dry_run_prints_candidates_and_the_job_is_scheduled(capsys) -> None:  # type: ignore[no-untyped-def]
    order = place(created=ago(days=26 * 30), state="closed", legacy_wp_order_id=8009)
    call_command("run_retention", "--dry-run")
    assert str(order.public_id) in capsys.readouterr().out
    assert Order.objects.get(pk=order.pk).privacy_status == "active"

    from apps.compliance import tasks

    assert tasks.run_retention is not None


def test_frozen_clock_ten_years_on_anonymises_a_restricted_order() -> None:
    order = place(created=ago(days=3 * DAYS), state="closed", legacy_wp_order_id=8010)
    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=9 * DAYS)  # only for dry-run listing
    result = retention.run_retention(ctx=CTX, dry_run=True, now=later)
    assert str(order.public_id) in result.candidates
