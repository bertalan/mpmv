"""CMP.3 part 2: GPSR article 22 safety cases with working-day deadlines (spec 11 CMP.3).

Done when (this part): a safety notice and an authority order show their working-day deadline
and an overdue one alerts staff. Staff record the case; the deadline is stored when it is opened;
the nightly job flags each overdue open case once, with a warning in the log and an audit entry.
"""

import datetime as dt
import logging

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.catalog.tests.factories import ProductFactory
from apps.compliance import services, tasks
from apps.compliance.models import SafetyCase
from apps.compliance.tests.factories import SafetyCaseFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.tasks import schedules
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:compliance", source="admin", ip="127.0.0.1")
ROME = dt.timezone(dt.timedelta(hours=1))
FRIDAY_NOON = dt.datetime(2026, 11, 13, 12, 0, tzinfo=ROME)


def test_opening_an_authority_order_stores_its_two_working_day_deadline() -> None:
    product = ProductFactory.create()
    services.open_safety_case(
        vendor=product.vendor,
        product=product,
        kind=SafetyCase.Kind.AUTHORITY_ORDER,
        received_at=FRIDAY_NOON,
        reference="ORDER-1",
        authority="Example market surveillance authority",
        summary="Withdraw the product from sale.",
        ctx=CTX,
    )
    case = SafetyCase.objects.get()
    assert case.due_on == dt.date(2026, 11, 17)
    assert case.status == SafetyCase.Status.OPEN
    assert case.product_sku == product.sku
    entry = AuditLog.objects.get(action=services.SAFETY_CASE_OPENED)
    assert entry.vendor_key == str(product.vendor.public_id)
    assert entry.after is not None
    assert entry.after["due_on"] == "2026-11-17"


def test_a_safety_notice_has_three_working_days() -> None:
    vendor = VendorFactory.create()
    services.open_safety_case(
        vendor=vendor,
        kind=SafetyCase.Kind.SAFETY_NOTICE,
        received_at=FRIDAY_NOON,
        summary="A buyer reports the charger overheating.",
        ctx=CTX,
    )
    assert SafetyCase.objects.get().due_on == dt.date(2026, 11, 18)


def test_the_deadline_counts_in_business_time() -> None:
    # 23:30 UTC on Friday is already Saturday in Rome: received on Saturday.
    late = dt.datetime(2026, 11, 13, 23, 30, tzinfo=dt.UTC)
    vendor = VendorFactory.create()
    services.open_safety_case(
        vendor=vendor,
        kind=SafetyCase.Kind.AUTHORITY_ORDER,
        received_at=late,
        summary="Order.",
        ctx=CTX,
    )
    assert SafetyCase.objects.get().due_on == dt.date(2026, 11, 17)


def test_dry_run_writes_nothing() -> None:
    vendor = VendorFactory.create()
    changes = services.open_safety_case(
        vendor=vendor,
        kind=SafetyCase.Kind.SAFETY_NOTICE,
        received_at=FRIDAY_NOON,
        summary="Notice.",
        ctx=CTX,
        dry_run=True,
    )
    assert [c.op for c in changes.changes] == ["create"]
    assert not SafetyCase.objects.exists()
    assert not AuditLog.objects.exists()


def test_the_product_must_belong_to_the_vendor() -> None:
    product = ProductFactory.create()
    with pytest.raises(ValidationError) as exc:
        services.open_safety_case(
            vendor=VendorFactory.create(),
            product=product,
            kind=SafetyCase.Kind.SAFETY_NOTICE,
            received_at=FRIDAY_NOON,
            summary="Notice.",
            ctx=CTX,
            dry_run=True,
        )
    assert "product" in exc.value.message_dict


def test_a_summary_is_required() -> None:
    with pytest.raises(ValidationError) as exc:
        services.open_safety_case(
            vendor=VendorFactory.create(),
            kind=SafetyCase.Kind.SAFETY_NOTICE,
            received_at=FRIDAY_NOON,
            summary="  ",
            ctx=CTX,
        )
    assert "summary" in exc.value.message_dict


def test_closing_a_case_records_the_resolution() -> None:
    case = SafetyCaseFactory.create()
    now = timezone.now()
    services.close_safety_case(case, resolution="Product withdrawn.", ctx=CTX, now=now)
    case.refresh_from_db()
    assert case.status == SafetyCase.Status.CLOSED
    assert case.closed_at == now
    assert case.resolution == "Product withdrawn."
    assert AuditLog.objects.filter(action=services.SAFETY_CASE_CLOSED).count() == 1
    with pytest.raises(ValidationError):
        services.close_safety_case(case, resolution="Again.", ctx=CTX)


def test_closing_needs_a_resolution() -> None:
    case = SafetyCaseFactory.create()
    with pytest.raises(ValidationError) as exc:
        services.close_safety_case(case, resolution="", ctx=CTX)
    assert "resolution" in exc.value.message_dict


def test_an_overdue_case_alerts_staff_once(caplog: pytest.LogCaptureFixture) -> None:
    due = dt.date(2026, 11, 17)
    overdue = SafetyCaseFactory.create(due_on=due)
    SafetyCaseFactory.create(due_on=dt.date(2026, 11, 18))  # due tomorrow: not yet
    SafetyCaseFactory.create(
        due_on=due, status=SafetyCase.Status.CLOSED, closed_at=timezone.now(), resolution="Done."
    )
    wednesday = dt.datetime(2026, 11, 18, 9, 0, tzinfo=ROME)

    preview = services.flag_overdue_safety_cases(now=wednesday, dry_run=True)
    assert [c.key for c in preview.changes] == [str(overdue.public_id)]
    assert not AuditLog.objects.exists()

    with caplog.at_level(logging.WARNING, logger="apps.compliance.services"):
        services.flag_overdue_safety_cases(now=wednesday)
    overdue.refresh_from_db()
    assert overdue.overdue_alerted_at == wednesday
    assert str(overdue.public_id) in caplog.text
    assert AuditLog.objects.filter(action=services.SAFETY_CASE_OVERDUE).count() == 1

    assert services.flag_overdue_safety_cases(now=wednesday).changes == []


def test_the_deadline_shown_to_staff() -> None:
    case = SafetyCaseFactory.create(due_on=dt.date(2026, 11, 17))
    friday = dt.datetime(2026, 11, 13, 9, 0, tzinfo=ROME)
    assert services.safety_case_deadline(case, now=friday) == services.Deadline(
        due_on=dt.date(2026, 11, 17), working_days_left=2, overdue=False
    )
    thursday = dt.datetime(2026, 11, 19, 9, 0, tzinfo=ROME)
    assert services.safety_case_deadline(case, now=thursday).overdue


def test_safety_cases_are_vendor_scoped() -> None:
    mine = SafetyCaseFactory.create()
    SafetyCaseFactory.create()
    assert list(SafetyCase.objects.for_vendor(mine.vendor)) == [mine]


def test_the_overdue_job_runs_every_hour_and_flags(caplog: pytest.LogCaptureFixture) -> None:
    assert "compliance.flag_overdue_safety_cases" in {s.task_name for s in schedules()}
    case = SafetyCaseFactory.create(due_on=dt.date(2020, 1, 1))
    with caplog.at_level(logging.ERROR, logger="apps.compliance"):
        tasks.flag_overdue_safety_cases.func(timestamp=0)
    case.refresh_from_db()
    assert case.overdue_alerted_at is not None
    assert str(case.public_id) in caplog.text
