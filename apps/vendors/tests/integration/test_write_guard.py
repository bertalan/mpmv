"""CORE.14 (cross-review fixes): `ensure_vendor_can_write` re-reads the vendor status inside the
write transaction, with the vendor row read `FOR SHARE`.

Only an active vendor passes. A status change committed before the read is seen even through a
stale instance; a status change that starts during the write waits for it to commit; two writes
of the same vendor do not wait for each other.
"""

import threading
import time
from collections.abc import Callable

import pytest
from django.db import connection, transaction

from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.vendors.engine import VendorCannotWrite
from apps.vendors.models import Vendor
from apps.vendors.services import change_status, ensure_vendor_can_write
from apps.vendors.tests.factories import VendorFactory

CTX = AuditContext(actor_label="staff:reviewer", source="admin")


@pytest.mark.django_db
def test_an_active_vendor_can_write() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    with transaction.atomic():
        assert ensure_vendor_can_write(vendor) == Vendor.Status.ACTIVE


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [
        Vendor.Status.APPLICANT,
        Vendor.Status.ONBOARDING,
        Vendor.Status.SUSPENDED,
        Vendor.Status.CLOSED,
    ],
)
def test_a_vendor_that_is_not_active_cannot_write(status: str) -> None:
    vendor = VendorFactory.create(status=status)
    with transaction.atomic(), pytest.raises(VendorCannotWrite) as refused:
        ensure_vendor_can_write(vendor)
    assert refused.value.status == status


@pytest.mark.django_db
def test_the_stored_status_counts_not_the_instance() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.SUSPENDED)
    assert vendor.status == Vendor.Status.ACTIVE  # stale, as after authentication
    with transaction.atomic(), pytest.raises(VendorCannotWrite):
        ensure_vendor_can_write(vendor)


@pytest.mark.django_db(transaction=True)
def test_it_must_run_inside_the_write_transaction() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    with pytest.raises(RuntimeError, match="inside the write transaction"):
        ensure_vendor_can_write(vendor)


# --- Locking


def _in_thread(work: Callable[[], object]) -> tuple[threading.Thread, list[object]]:
    """Run `work` on its own database connection; the list gets its result or its error."""
    outcome: list[object] = []

    def run() -> None:
        try:
            outcome.append(work())
        except Exception as exc:
            outcome.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome


def _wait_for_a_blocked_query() -> None:
    """Return once another connection waits for a lock (fail after 10 seconds)."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_stat_clear_snapshot()")  # fresh view inside a transaction
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND pid <> pg_backend_pid() AND wait_event_type = 'Lock'"
            )
            row = cursor.fetchone()
        if row and row[0]:
            return
        time.sleep(0.05)
    pytest.fail("the status change never waited for the write")


@pytest.mark.django_db(transaction=True)
def test_a_suspension_waits_for_a_write_in_progress() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    with transaction.atomic():
        ensure_vendor_can_write(vendor)
        thread, outcome = _in_thread(
            lambda: change_status(vendor, Vendor.Status.SUSPENDED, ctx=CTX)
        )
        _wait_for_a_blocked_query()
        assert not outcome  # still waiting while the write is open
    thread.join(timeout=10)
    assert len(outcome) == 1
    assert isinstance(outcome[0], ChangeSet), outcome[0]
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.SUSPENDED


@pytest.mark.django_db(transaction=True)
def test_two_writes_of_the_same_vendor_do_not_wait_for_each_other() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)

    def other_write() -> object:
        with transaction.atomic():
            return ensure_vendor_can_write(vendor)

    with transaction.atomic():
        ensure_vendor_can_write(vendor)
        thread, outcome = _in_thread(other_write)
        thread.join(timeout=10)
        assert outcome == [Vendor.Status.ACTIVE]  # done while this write is still open
