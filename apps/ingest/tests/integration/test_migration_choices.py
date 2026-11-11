"""MIG.16: the vendor migration choices, stored per vendor and per run, versioned and audited."""

import datetime as dt
from dataclasses import replace

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import QuerySet

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.ingest import engine, services
from apps.ingest.models import MigrationChoice, MigrationRun
from apps.ingest.tests.factories import MigrationChoiceFactory, MigrationRunFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

UTC = dt.UTC
CUTOVER = dt.datetime(2027, 3, 1, 12, 0, tzinfo=UTC)
DEADLINE = CUTOVER - dt.timedelta(days=14)
EARLY = DEADLINE - dt.timedelta(days=30)
CTX = AuditContext(actor_label="user:test", source=AuditLog.Source.VENDOR)
SYSTEM = AuditContext(actor_label="system:migration", source=AuditLog.Source.SYSTEM)


@pytest.fixture
def run() -> MigrationRun:
    return MigrationRunFactory.create(label="cutover-one", cutover_at=CUTOVER)


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create()


def record(
    vendor: Vendor,
    run: MigrationRun,
    values: engine.ChoiceValues = engine.DEFAULT_CHOICE,
    *,
    source: str = engine.SOURCE_VENDOR,
    now: dt.datetime = EARLY,
) -> None:
    services.record_choice(vendor, run, values, source=source, ctx=CTX, now=now)


# --- Values are saved, read back and validated


@pytest.mark.parametrize("window", engine.LOGIN_WINDOWS)
def test_each_login_window_is_saved_and_read_back(
    vendor: Vendor, run: MigrationRun, window: int
) -> None:
    record(vendor, run, replace(engine.DEFAULT_CHOICE, login_window_months=window))
    stored = MigrationChoice.objects.get(vendor=vendor, run=run)
    assert stored.login_window_months == window
    assert services.current_choice(vendor, run).values.login_window_months == window  # type: ignore[union-attr]


@pytest.mark.parametrize("window", [0, 4, 5, 7, 18, 48])
def test_any_other_login_window_is_refused_and_nothing_is_written(
    vendor: Vendor, run: MigrationRun, window: int
) -> None:
    with pytest.raises(ValidationError):
        record(vendor, run, replace(engine.DEFAULT_CHOICE, login_window_months=window))
    assert not MigrationChoice.objects.exists()
    assert not AuditLog.objects.filter(action=services.MIGRATION_CHOICE_RECORDED).exists()


@pytest.mark.parametrize("period", [12, 24, 36, None])
def test_each_period_is_saved_and_read_back(
    vendor: Vendor, run: MigrationRun, period: int | None
) -> None:
    record(vendor, run, replace(engine.DEFAULT_CHOICE, period_months=period))
    assert services.current_choice(vendor, run).period_months == period  # type: ignore[union-attr]


@pytest.mark.parametrize("period", [6, 18, 48])
def test_any_other_period_is_refused(vendor: Vendor, run: MigrationRun, period: int) -> None:
    with pytest.raises(ValidationError):
        record(vendor, run, replace(engine.DEFAULT_CHOICE, period_months=period))


@pytest.mark.parametrize("flag", [True, False])
def test_the_unpublished_products_flag_is_saved_and_read_back(
    vendor: Vendor, run: MigrationRun, flag: bool
) -> None:
    record(vendor, run, replace(engine.DEFAULT_CHOICE, include_unpublished=flag))
    assert services.current_choice(vendor, run).include_unpublished is flag  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "types",
    [
        ("orders",),
        ("products",),
        ("orders", "customers"),
        ("orders", "products", "reviews"),
        engine.DATA_TYPES,
        (),
    ],
)
def test_data_types_are_saved_and_read_back(
    vendor: Vendor, run: MigrationRun, types: tuple[str, ...]
) -> None:
    record(vendor, run, replace(engine.DEFAULT_CHOICE, data_types=types))
    assert services.current_choice(vendor, run).values.data_types == types  # type: ignore[union-attr]


def test_an_unknown_data_type_and_customers_without_orders_are_refused(
    vendor: Vendor, run: MigrationRun
) -> None:
    with pytest.raises(ValidationError):
        record(vendor, run, replace(engine.DEFAULT_CHOICE, data_types=("orders", "x")))
    with pytest.raises(ValidationError):
        record(vendor, run, replace(engine.DEFAULT_CHOICE, data_types=("customers",)))


def test_the_database_refuses_a_login_window_outside_the_list(
    vendor: Vendor, run: MigrationRun
) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        MigrationChoiceFactory.create(vendor=vendor, run=run, login_window_months=5)


# --- Versions and audit


def test_a_change_adds_a_version_and_one_audit_entry_with_the_old_and_new_value(
    vendor: Vendor, run: MigrationRun
) -> None:
    record(vendor, run, replace(engine.DEFAULT_CHOICE, period_months=12))
    record(vendor, run, replace(engine.DEFAULT_CHOICE, period_months=36))
    assert [c.version for c in services.choice_history(vendor, run)] == [1, 2]
    entries = AuditLog.objects.filter(action=services.MIGRATION_CHOICE_RECORDED).order_by("id")
    assert entries.count() == 2  # one entry per change
    assert entries[0].before is None
    entry = entries[1]
    assert entry.before is not None
    assert entry.after is not None
    assert (entry.before["period_months"], entry.after["period_months"]) == (12, 36)
    assert (entry.before["version"], entry.after["version"]) == (1, 2)
    assert entry.vendor_key == str(vendor.public_id)


def test_the_same_values_again_change_nothing(vendor: Vendor, run: MigrationRun) -> None:
    record(vendor, run)
    changes = services.record_choice(
        vendor, run, engine.DEFAULT_CHOICE, source=engine.SOURCE_VENDOR, ctx=CTX, now=EARLY
    )
    assert changes.is_empty
    assert MigrationChoice.objects.count() == 1


def test_a_dry_run_writes_nothing_and_returns_the_change(vendor: Vendor, run: MigrationRun) -> None:
    changes = services.record_choice(
        vendor, run, engine.DEFAULT_CHOICE, source="vendor", ctx=CTX, now=EARLY, dry_run=True
    )
    assert not changes.is_empty
    assert not MigrationChoice.objects.exists()
    assert not AuditLog.objects.filter(action=services.MIGRATION_CHOICE_RECORDED).exists()


def test_choices_are_per_vendor_and_per_run(vendor: Vendor, run: MigrationRun) -> None:
    other_vendor = VendorFactory.create()
    other_run = MigrationRunFactory.create(cutover_at=CUTOVER)
    record(vendor, run, replace(engine.DEFAULT_CHOICE, period_months=12))
    record(other_vendor, run, replace(engine.DEFAULT_CHOICE, period_months=36))
    record(vendor, other_run, replace(engine.DEFAULT_CHOICE, period_months=None))
    assert services.current_choice(vendor, run).period_months == 12  # type: ignore[union-attr]
    assert services.current_choice(other_vendor, run).period_months == 36  # type: ignore[union-attr]
    assert services.current_choice(vendor, other_run).period_months is None  # type: ignore[union-attr]


def test_staff_enter_a_choice_on_behalf_after_the_deadline(
    vendor: Vendor, run: MigrationRun
) -> None:
    staff = UserFactory.create(staff=True)
    late = DEADLINE + dt.timedelta(days=1)
    services.record_choice(
        vendor,
        run,
        replace(engine.DEFAULT_CHOICE, period_months=12),
        source=engine.SOURCE_STAFF,
        actor=staff,
        ctx=replace(CTX, source=AuditLog.Source.ADMIN),
        now=late,
    )
    stored = services.current_choice(vendor, run)
    assert stored is not None
    assert (stored.source, stored.decided_by_id) == ("staff", staff.pk)
    entry = AuditLog.objects.get(action=services.MIGRATION_CHOICE_RECORDED)
    assert entry.after is not None
    assert entry.after["source"] == "staff"


def test_a_vendor_cannot_record_after_the_deadline(vendor: Vendor, run: MigrationRun) -> None:
    with pytest.raises(services.ChoiceClosed):
        record(vendor, run, now=DEADLINE)
    assert not MigrationChoice.objects.exists()


# --- The guard of the dry run


def test_the_dry_run_guard_refuses_without_a_recorded_choice(
    vendor: Vendor, run: MigrationRun
) -> None:
    with pytest.raises(services.ChoiceMissing):
        services.require_recorded_choice(vendor, run)
    record(vendor, run)
    assert services.require_recorded_choice(vendor, run).vendor == vendor


# --- Deadline, defaults and reminders


def test_after_the_deadline_the_defaults_are_recorded_and_audited_as_a_default(
    vendor: Vendor, run: MigrationRun
) -> None:
    chosen = VendorFactory.create()
    record(chosen, run, replace(engine.DEFAULT_CHOICE, period_months=12))
    closed = VendorFactory.create(status=Vendor.Status.CLOSED)
    services.apply_defaults(run, now=DEADLINE - dt.timedelta(minutes=1), ctx=SYSTEM)
    assert not MigrationChoice.objects.filter(vendor=vendor).exists()  # not yet due

    services.apply_defaults(run, now=DEADLINE, ctx=SYSTEM)
    default = services.current_choice(vendor, run)
    assert default is not None
    assert default.source == "default"
    assert default.values == engine.ChoiceValues(
        period_months=24,
        data_types=("orders", "customers", "products", "reviews"),
        include_unpublished=False,
        login_window_months=12,
    )
    assert services.current_choice(chosen, run).period_months == 12  # type: ignore[union-attr]
    assert not MigrationChoice.objects.filter(vendor=closed).exists()
    entry = AuditLog.objects.get(
        action=services.MIGRATION_CHOICE_DEFAULTED, vendor_key=str(vendor.public_id)
    )
    assert entry.after["source"] == "default"  # type: ignore[index]
    assert entry.before is None
    # A recorded default satisfies the dry-run guard, and a second pass adds nothing.
    assert services.require_recorded_choice(vendor, run) == default
    count = MigrationChoice.objects.count()
    services.apply_defaults(run, now=DEADLINE + dt.timedelta(days=1), ctx=SYSTEM)
    assert MigrationChoice.objects.count() == count


def test_reminders_go_to_the_owners_of_vendors_without_a_choice_and_are_audited(
    vendor: Vendor, run: MigrationRun
) -> None:
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    VendorMemberFactory.create(vendor=vendor, role="staff")
    chosen = VendorFactory.create()
    VendorMemberFactory.create(vendor=chosen, role="owner")
    record(chosen, run)

    assert not services.send_reminders(
        run, now=DEADLINE - dt.timedelta(days=20), ctx=SYSTEM
    ).changes
    assert not OutboxEmail.objects.exists()

    now = DEADLINE - dt.timedelta(days=7, hours=1)  # the 14-day reminder is the closest reached
    services.send_reminders(run, now=now, ctx=SYSTEM)

    def mails() -> QuerySet[OutboxEmail]:
        return OutboxEmail.objects.filter(template_key=services.MIGRATION_REMINDER_TEMPLATE)

    assert [m.recipient for m in mails()] == [owner.email]
    entry = AuditLog.objects.get(action=services.MIGRATION_CHOICE_REMINDER)
    assert entry.after["offset_days"] == 14  # type: ignore[index]
    assert entry.after["recipients"] == 1  # type: ignore[index]
    assert owner.email not in str(entry.after)
    assert entry.vendor_key == str(vendor.public_id)

    services.send_reminders(run, now=now + dt.timedelta(minutes=30), ctx=SYSTEM)  # same offset
    assert mails().count() == 1

    services.send_reminders(run, now=DEADLINE - dt.timedelta(hours=5), ctx=SYSTEM)  # 1 day left
    services.send_reminders(run, now=DEADLINE - dt.timedelta(hours=4), ctx=SYSTEM)
    offsets = [
        e.after["offset_days"]  # type: ignore[index]
        for e in AuditLog.objects.filter(action=services.MIGRATION_CHOICE_REMINDER).order_by("id")
    ]
    assert offsets == [14, 1]
    assert mails().count() == 2


def test_no_reminder_after_the_deadline_or_in_a_dry_run(vendor: Vendor, run: MigrationRun) -> None:
    VendorMemberFactory.create(vendor=vendor, role="owner")
    services.send_reminders(run, now=DEADLINE + dt.timedelta(hours=1), ctx=SYSTEM)
    changes = services.send_reminders(
        run, now=DEADLINE - dt.timedelta(days=2), ctx=SYSTEM, dry_run=True
    )
    assert not changes.is_empty
    assert not OutboxEmail.objects.exists()
    assert not AuditLog.objects.filter(action=services.MIGRATION_CHOICE_REMINDER).exists()


def test_a_run_is_planned_with_its_deadline_and_audited() -> None:
    changes = services.create_run("launch", CUTOVER, ctx=SYSTEM)
    assert not changes.is_empty
    entry = AuditLog.objects.get(action=services.MIGRATION_RUN_CREATED)
    assert entry.after["choice_deadline"] == DEADLINE.isoformat()  # type: ignore[index]
    with pytest.raises(ValidationError):
        services.create_run("launch", CUTOVER, ctx=SYSTEM)
    assert services.active_run(CUTOVER - dt.timedelta(days=1)).label == "launch"  # type: ignore[union-attr]
    assert services.active_run(CUTOVER + dt.timedelta(days=1)) is None
