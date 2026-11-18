"""TRU.1: versioned TrustRules, append-only VendorTrustSnapshot, audit, dry run (PostgreSQL)."""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import InternalError, ProgrammingError, connection, transaction
from django.utils import timezone

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.validity import NoValidRow
from apps.trust import engine, services
from apps.trust.models import TrustRules, VendorTrustSnapshot
from apps.trust.tests.factories import TrustRulesFactory, VendorTrustSnapshotFactory
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:ops", source="admin", ip="127.0.0.1")
AS_OF = dt.datetime(2027, 3, 2, 12, 0, tzinfo=dt.UTC)


def params(**changes: Any) -> dict[str, Any]:
    return {**engine.rules_to_params(engine.TrustRulesData(version=1)), **changes}


def orders(count: int) -> tuple[engine.DeliveredOrder, ...]:
    return tuple(
        engine.DeliveredOrder(
            ref=f"o{i}",
            buyer=f"b{i}",
            delivered_at=AS_OF - dt.timedelta(days=30),
            net_goods=Decimal("40.00"),
            country="IT",
        )
        for i in range(count)
    )


def test_the_first_version_is_1_and_a_change_closes_the_open_one() -> None:
    now = timezone.now()
    services.add_trust_rules(params(), valid_from=now, ctx=CTX)
    later = now + dt.timedelta(days=2)
    services.add_trust_rules(params(orders_tiers=[20, 50]), valid_from=later, ctx=CTX)
    first, second = TrustRules.objects.order_by("version")
    assert (first.version, second.version) == (1, 2)
    assert first.valid_to == later
    assert second.valid_to is None
    assert services.rules_in_force(at=now + dt.timedelta(days=1)).version == 1
    assert services.rules_in_force(at=later).version == 2


def test_no_rules_in_force_is_an_error_never_a_silent_default() -> None:
    with pytest.raises(NoValidRow):
        services.rules_in_force(at=timezone.now())


def test_a_rules_change_writes_audit_rows_with_before_and_after() -> None:
    now = timezone.now()
    services.add_trust_rules(params(), valid_from=now, ctx=CTX)
    later = now + dt.timedelta(days=2)
    services.add_trust_rules(
        params(min_order_value="9.00"), valid_from=later, note="raise floor", ctx=CTX
    )
    entries = AuditLog.objects.filter(
        action=services.RULES_CHANGED, target_model="trust.trustrules"
    )
    assert entries.count() == 3  # create v1, close v1, create v2
    created = entries.filter(after__version=2).get()
    assert created.before is not None
    assert created.after is not None
    assert created.before["version"] == 1
    assert created.after["params"]["min_order_value"] == "9.00"
    assert created.after["note"] == "raise floor"
    assert created.actor_label == "staff:ops"


def test_dry_run_writes_nothing_and_returns_the_change_set() -> None:
    now = timezone.now()
    services.add_trust_rules(params(), valid_from=now, ctx=CTX)
    rows, audit = TrustRules.objects.count(), AuditLog.objects.count()
    changeset = services.add_trust_rules(
        params(orders_tiers=[5]), valid_from=now + dt.timedelta(days=1), ctx=CTX, dry_run=True
    )
    assert changeset.summary() == {"create": 1, "update": 1, "delete": 0}
    assert (TrustRules.objects.count(), AuditLog.objects.count()) == (rows, audit)
    assert TrustRules.objects.get().valid_to is None


def test_the_preview_counts_the_vendors_whose_result_would_change() -> None:
    now = timezone.now()
    services.add_trust_rules(params(), valid_from=now, ctx=CTX)
    vendors = [
        engine.VendorTrustInputs(vendor_ref=f"v{n}", as_of=AS_OF, delivered_orders=orders(count))
        for n, count in enumerate((10, 10, 3, 60))
    ]
    changeset = services.add_trust_rules(
        params(orders_tiers=[20, 50]),
        valid_from=now + dt.timedelta(days=1),
        ctx=CTX,
        dry_run=True,
        impact_inputs=vendors,
    )
    created = next(c for c in changeset.changes if c.op == "create")
    assert created.after is not None
    assert created.after["vendors_checked"] == 4
    assert created.after["vendors_changed"] == 2  # the two with 10 orders lose tier 10
    assert not TrustRules.objects.filter(version=2).exists()


def test_invalid_rules_are_refused() -> None:
    with pytest.raises(ValidationError):
        services.add_trust_rules(
            params(dispatch_earn_percent="80", dispatch_lose_percent="85"),
            valid_from=timezone.now(),
            ctx=CTX,
        )
    with pytest.raises(ValidationError):
        services.add_trust_rules({"nope": 1}, valid_from=timezone.now(), ctx=CTX)
    assert not TrustRules.objects.exists()


def test_a_start_in_the_past_is_refused() -> None:
    with pytest.raises(ValidationError) as excinfo:
        services.add_trust_rules(
            params(), valid_from=timezone.now() - dt.timedelta(days=1), ctx=CTX
        )
    assert "valid_from" in excinfo.value.message_dict


def test_a_rules_row_is_never_edited_or_deleted() -> None:
    rules = TrustRulesFactory.create(valid_to=None)
    rules.note = "changed"
    with pytest.raises(PermissionError):
        rules.save()
    with (
        pytest.raises((ProgrammingError, InternalError)),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("UPDATE trust_trustrules SET note = 'x'")
    with (
        pytest.raises((ProgrammingError, InternalError)),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM trust_trustrules")
    assert TrustRules.objects.count() == 1


def test_rules_periods_cannot_overlap() -> None:
    from django.db import IntegrityError

    now = timezone.now()
    TrustRulesFactory.create(version=1, valid_from=now, valid_to=None)
    with pytest.raises(IntegrityError), transaction.atomic():
        TrustRulesFactory.create(version=2, valid_from=now + dt.timedelta(days=1), valid_to=None)


def test_a_snapshot_is_append_only() -> None:
    snapshot = VendorTrustSnapshotFactory.create()
    snapshot.metrics = {"changed": True}
    with pytest.raises(PermissionError):
        snapshot.save()
    with pytest.raises(PermissionError):
        snapshot.delete()
    with (
        pytest.raises((ProgrammingError, InternalError)),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("UPDATE trust_vendortrustsnapshot SET metrics = '{}'")
    with pytest.raises((ProgrammingError, InternalError)), transaction.atomic():
        VendorTrustSnapshot.objects.all().delete()
    with (
        pytest.raises((ProgrammingError, InternalError)),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM trust_vendortrustsnapshot")
    assert VendorTrustSnapshot.objects.count() == 1


def test_a_new_rules_version_changes_a_result_and_the_old_snapshot_keeps_its_version() -> None:
    now = timezone.now()
    vendor = VendorFactory.create()
    services.add_trust_rules(params(), valid_from=now, ctx=CTX)
    data = engine.VendorTrustInputs(
        vendor_ref=str(vendor.public_id), as_of=AS_OF, delivered_orders=orders(10)
    )

    first = engine.compute_vendor_trust(data, services.rules_data(services.rules_in_force(at=now)))
    services.record_snapshot(vendor, first)

    later = now + dt.timedelta(days=1)
    services.add_trust_rules(params(orders_tiers=[20, 50]), valid_from=later, ctx=CTX)
    second = engine.compute_vendor_trust(
        data, services.rules_data(services.rules_in_force(at=later))
    )
    services.record_snapshot(vendor, second)

    assert first.badges[0].earned
    assert not second.badges[0].earned
    old, new = VendorTrustSnapshot.objects.filter(vendor=vendor).order_by("id")
    assert old.rules_version == 1
    assert new.rules_version == 2
    assert old.rules.version == 1
    assert new.rules.version == 2
    assert old.badges[0]["earned"] is True  # the old snapshot is untouched
    assert services.latest_snapshot(vendor) == new


def test_record_snapshot_dry_run_writes_nothing() -> None:
    now = timezone.now()
    services.add_trust_rules(params(), valid_from=now, ctx=CTX)
    vendor = VendorFactory.create()
    result = engine.compute_vendor_trust(
        engine.VendorTrustInputs(vendor_ref="x", as_of=AS_OF),
        services.rules_data(services.rules_in_force(at=now)),
    )
    changeset = services.record_snapshot(vendor, result, dry_run=True)
    assert changeset.summary()["create"] == 1
    assert not VendorTrustSnapshot.objects.exists()


def test_the_public_pages_read_only_the_latest_snapshot() -> None:
    vendor = VendorFactory.create()
    VendorTrustSnapshotFactory.create(vendor=vendor, as_of=AS_OF - dt.timedelta(days=1))
    newest = VendorTrustSnapshotFactory.create(vendor=vendor, as_of=AS_OF)
    VendorTrustSnapshotFactory.create(as_of=AS_OF + dt.timedelta(days=5))  # another vendor
    assert services.latest_snapshot(vendor) == newest
    assert services.latest_snapshot(VendorFactory.create()) is None


def test_ensure_default_rules_creates_version_1_once() -> None:
    services.ensure_default_rules(ctx=CTX)
    services.ensure_default_rules(ctx=CTX)
    rules = TrustRules.objects.get()
    assert rules.version == 1
    assert services.rules_data(rules) == engine.TrustRulesData(version=1)
