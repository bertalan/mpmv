"""ING.2: source state, change history and stock state on real PostgreSQL."""

import datetime as dt
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.audit import AuditContext
from apps.ingest import engine, services
from apps.ingest.models import (
    ItemChange,
    SourceMissing,
    SourceState,
    SourceStateVersion,
    SourceVariation,
)
from apps.ingest.tests.factories import (
    ItemChangeFactory,
    SourceItemFactory,
    SourceMissingFactory,
    SourceStateFactory,
    SourceStateVersionFactory,
    SourceVariationFactory,
    SupplierSourceFactory,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:ingest", source="admin", ip="127.0.0.1")
T0 = dt.datetime(2026, 11, 1, 6, 0, tzinfo=dt.UTC)
T1 = T0 + dt.timedelta(days=1)
T2 = T0 + dt.timedelta(days=2)


def child(native_id: int, status: int | None = 1, quantity: int | None = 2) -> dict[str, Any]:
    return {
        "native_id": native_id,
        "sku": f"V-{native_id}",
        "stock_status": status,
        "stock_quantity": quantity,
    }


def variable(*children: dict[str, Any], native_id: int = 7, **extra: Any) -> dict[str, Any]:
    kids = children or (child(11), child(12, 0, 0))
    return {
        "kind": "variable",
        "native_id": native_id,
        "sku": "BAG",
        "source_urls": [],
        "variations": {str(c["native_id"]): c for c in kids},
        **extra,
    }


def simple(status: int | None = 1, quantity: int | None = 3, native_id: int = 5) -> dict[str, Any]:
    return {
        "kind": "simple",
        "native_id": native_id,
        "sku": "BAG-S",
        "source_urls": [],
        "variations": {},
        "stock_status": status,
        "stock_quantity": quantity,
    }


def test_first_observation_creates_state_variations_and_history() -> None:
    item = SourceItemFactory.create()
    changed, changes = services.record_source_state(item, variable(), ctx=CTX, now=T0)
    assert changed
    assert changes.summary()["create"] == 1
    state = SourceState.objects.get(item=item)
    assert (state.kind, state.native_id, state.version, state.observed_at) == ("variable", 7, 1, T0)
    assert state.stock_status is None
    assert state.stock_quantity is None
    kids = {v.native_id: v for v in item.variations.all()}
    assert kids[11].stock_quantity == 2
    assert kids[12].stock_status == 0
    assert all(v.present and v.last_seen_at == T0 for v in kids.values())
    assert SourceStateVersion.objects.get(item=item).version == 1


def test_a_repeat_advances_freshness_only_and_a_change_appends_history() -> None:
    item = SourceItemFactory.create()
    services.record_source_state(item, variable(), ctx=CTX, now=T0)
    changed, changes = services.record_source_state(item, variable(), ctx=CTX, now=T1)
    assert not changed
    assert changes.is_empty
    state = SourceState.objects.get(item=item)
    assert (state.version, state.observed_at) == (1, T1)
    assert item.state_versions.count() == 1
    changed, _ = services.record_source_state(
        item, variable(child(11, 1, 9), child(12, 0, 0)), ctx=CTX, now=T2
    )
    assert changed
    assert SourceState.objects.get(item=item).version == 2
    assert list(item.state_versions.values_list("version", flat=True)) == [1, 2]


def test_return_to_the_baseline_is_a_new_version() -> None:
    item = SourceItemFactory.create()
    services.record_source_state(item, variable(), ctx=CTX, now=T0)
    services.record_source_state(item, variable(child(11, 1, 9), child(12, 0, 0)), ctx=CTX, now=T1)
    services.record_source_state(item, variable(), ctx=CTX, now=T2)
    assert SourceState.objects.get(item=item).version == 3
    versions = list(item.state_versions.order_by("version"))
    assert versions[0].state == versions[2].state


def test_an_unlisted_variation_is_kept_absent_not_zeroed_or_deleted() -> None:
    item = SourceItemFactory.create()
    services.record_source_state(item, variable(), ctx=CTX, now=T0)
    services.record_source_state(item, variable(child(11, 1, 5)), ctx=CTX, now=T1)
    gone = SourceVariation.objects.get(item=item, native_id=12)
    assert not gone.present
    assert gone.last_seen_at == T0
    assert (gone.stock_status, gone.stock_quantity) == (0, 0)
    kept = SourceVariation.objects.get(item=item, native_id=11)
    assert kept.present
    assert kept.last_seen_at == T1


def test_unknown_stock_is_stored_as_null_never_zero() -> None:
    item = SourceItemFactory.create()
    services.record_source_state(item, simple(None, None), ctx=CTX, now=T0)
    state = SourceState.objects.get(item=item)
    assert state.stock_status is None
    assert state.stock_quantity is None


def test_simple_stock_columns_follow_the_observation() -> None:
    item = SourceItemFactory.create()
    services.record_source_state(item, simple(0, 0), ctx=CTX, now=T0)
    assert SourceState.objects.get(item=item).stock_status == 0
    services.record_source_state(item, simple(1, 4), ctx=CTX, now=T1)
    state = SourceState.objects.get(item=item)
    assert (state.stock_status, state.stock_quantity, state.version) == (1, 4, 2)


def test_an_incoherent_observation_stores_nothing() -> None:
    item = SourceItemFactory.create()
    with pytest.raises(engine.StateError):
        services.record_source_state(item, simple(1, 0), ctx=CTX, now=T0)
    assert not SourceState.objects.exists()


def test_dry_run_returns_the_changeset_and_writes_nothing() -> None:
    item = SourceItemFactory.create()
    changed, changes = services.record_source_state(item, variable(), ctx=CTX, dry_run=True)
    assert changed
    assert changes.summary()["create"] == 1
    assert not SourceState.objects.exists()
    assert not SourceVariation.objects.exists()
    assert not SourceStateVersion.objects.exists()


def test_a_native_id_owned_by_another_item_rolls_back_everything() -> None:
    source = SupplierSourceFactory.create()
    first = SourceItemFactory.create(source=source)
    other = SourceItemFactory.create(source=source)
    services.record_source_state(first, variable(), ctx=CTX, now=T0)
    with pytest.raises(services.StateConflict):
        services.record_source_state(other, variable(), ctx=CTX, now=T1)
    with pytest.raises(services.StateConflict):
        services.record_source_state(other, simple(native_id=7), ctx=CTX, now=T1)
    clash = variable(child(11), native_id=9)
    with pytest.raises(services.StateConflict):
        services.record_source_state(other, clash, ctx=CTX, now=T1)
    assert not SourceState.objects.filter(item=other).exists()
    assert not SourceVariation.objects.filter(item=other).exists()
    assert not SourceStateVersion.objects.filter(item=other).exists()


def test_the_same_native_id_in_another_feed_is_fine() -> None:
    first, other = SourceItemFactory.create(), SourceItemFactory.create()
    services.record_source_state(first, variable(), ctx=CTX, now=T0)
    services.record_source_state(other, variable(), ctx=CTX, now=T0)
    assert SourceState.objects.count() == 2


def test_a_parent_native_id_change_requires_reconciliation() -> None:
    item = SourceItemFactory.create()
    services.record_source_state(item, variable(), ctx=CTX, now=T0)
    with pytest.raises(engine.StateError):
        services.record_source_state(item, variable(native_id=8), ctx=CTX, now=T1)
    assert SourceState.objects.get(item=item).native_id == 7


def test_the_database_refuses_incoherent_stock_and_a_variable_without_native_id() -> None:
    item = SourceItemFactory.create()
    services.record_source_state(item, simple(), ctx=CTX, now=T0)
    for bad in (
        {"stock_status": 1, "stock_quantity": 0},
        {"stock_status": 0, "stock_quantity": 3},
        {"stock_status": 1, "stock_quantity": None},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            SourceState.objects.filter(item=item).update(**bad)
    with pytest.raises(IntegrityError), transaction.atomic():
        SourceState.objects.filter(item=item).update(kind="variable", native_id=None)
    with pytest.raises(IntegrityError), transaction.atomic():
        SourceVariation.objects.create(
            item=item,
            source=item.source,
            native_id=1,
            state={},
            stock_status=1,
            stock_quantity=0,
            present=True,
            observed_at=T0,
            last_seen_at=T0,
        )


def test_missing_evidence_is_recorded_updated_and_cleared_by_a_good_read() -> None:
    item = SourceItemFactory.create()
    services.record_missing(item, "https://supplier.example/p/1", 404, ctx=CTX, now=T0)
    services.record_missing(item, "https://supplier.example/p/1", 404, ctx=CTX, now=T1)
    row = SourceMissing.objects.get(item=item)
    assert (row.first_seen_at, row.last_seen_at, row.http_status) == (T0, T1, 404)
    assert services.missing_since([item.pk]) == {item.pk: T1}
    changed, changes = services.record_source_state(item, simple(), ctx=CTX, now=T2)
    assert changed
    assert changes.summary()["delete"] == 1
    assert not SourceMissing.objects.filter(item=item).exists()
    assert services.missing_since([item.pk]) == {}


def test_missing_does_not_touch_the_stored_stock() -> None:
    item = SourceItemFactory.create()
    services.record_source_state(item, simple(1, 3), ctx=CTX, now=T0)
    services.record_missing(item, "https://supplier.example/p/1", 404, ctx=CTX, now=T1)
    assert SourceState.objects.get(item=item).stock_quantity == 3


def test_missing_needs_an_error_status_and_dry_run_writes_nothing() -> None:
    item = SourceItemFactory.create()
    with pytest.raises(Exception, match="ingest_missing_http_error"):
        services.record_missing(item, "https://supplier.example/p/1", 200, ctx=CTX)
    changes = services.record_missing(item, "u", 404, ctx=CTX, dry_run=True)
    assert changes.summary()["create"] == 1
    assert not SourceMissing.objects.exists()


def test_clear_missing_honours_dry_run() -> None:
    item = SourceItemFactory.create()
    assert services.clear_missing(item, ctx=CTX).is_empty
    services.record_missing(item, "u", 404, ctx=CTX, now=T0)
    services.clear_missing(item, ctx=CTX, dry_run=True)
    assert SourceMissing.objects.filter(item=item).exists()
    services.clear_missing(item, ctx=CTX)
    assert not SourceMissing.objects.filter(item=item).exists()


def test_item_changes_are_append_only_and_the_latest_flagged_value_wins() -> None:
    item = SourceItemFactory.create()
    services.record_item_change(item, {"price": "10.00", "title": "A"}, ctx=CTX, now=T0)
    services.record_item_change(item, {"price": "12.00", "title": "A"}, ctx=CTX, now=T1)
    services.record_item_change(item, {"price": "12.00", "title": "A"}, ctx=CTX, now=T2)
    assert list(item.changes.values_list("version", flat=True)) == [1, 2]
    second = item.changes.get(version=2)
    assert second.changed_fields == {"price": True}
    assert second.new_values == {"price": "12.00"}
    assert services.latest_changes([item.pk]) == {item.pk: {"price": "12.00", "title": "A"}}
    assert services.latest_changes([item.pk], fields=["title"]) == {item.pk: {"title": "A"}}


def test_latest_changes_handles_many_items_and_none() -> None:
    a, b = SourceItemFactory.create(), SourceItemFactory.create()
    services.record_item_change(a, {"price": "1"}, ctx=CTX)
    services.record_item_change(b, {"price": "2"}, ctx=CTX)
    assert services.latest_changes([a.pk, b.pk]) == {a.pk: {"price": "1"}, b.pk: {"price": "2"}}
    assert services.latest_changes([]) == {}


def test_item_change_dry_run_writes_nothing() -> None:
    item = SourceItemFactory.create()
    changes = services.record_item_change(item, {"price": "1"}, ctx=CTX, dry_run=True)
    assert changes.summary()["create"] == 1
    assert not ItemChange.objects.exists()


def test_state_rows_follow_their_item_when_it_goes() -> None:
    item = SourceItemFactory.create(last_seen_at=timezone.now())
    services.record_source_state(item, variable(), ctx=CTX, now=T0)
    item.delete()
    assert not SourceState.objects.exists()
    assert not SourceVariation.objects.exists()


def test_state_and_variation_source_mirror_the_item_source() -> None:
    item = SourceItemFactory.create()
    services.record_source_state(item, variable(), ctx=CTX, now=T0)
    assert SourceState.objects.get(item=item).source_id == item.source_id
    assert {v.source_id for v in item.variations.all()} == {item.source_id}


def test_an_item_change_version_is_unique_per_item() -> None:
    item = SourceItemFactory.create()
    services.record_item_change(item, {"price": "1"}, ctx=CTX, now=T0)
    with pytest.raises(IntegrityError), transaction.atomic():
        ItemChange.objects.create(
            item=item, version=1, changed_fields={}, new_values={}, recorded_at=T0
        )


def test_the_state_factories_make_valid_rows_of_one_vendor() -> None:
    state = SourceStateFactory.create()
    variation = SourceVariationFactory.create(item=state.item)
    version = SourceStateVersionFactory.create(item=state.item)
    missing = SourceMissingFactory.create(item=state.item)
    change = ItemChangeFactory.create(item=state.item)
    for row in (state, variation, version, missing, change):
        row.full_clean(validate_unique=False)
        assert row.item.vendor_id == state.item.vendor_id
    assert state.source_id == state.item.source_id
