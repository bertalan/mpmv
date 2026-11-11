"""ING.1: run status machine, blocking review items for ambiguous SKU matches, mappings."""

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.tests.factories import UserFactory
from apps.catalog.tests.factories import ProductFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.ingest import services
from apps.ingest.models import ImportRun, MappingTable, ReviewItem
from apps.ingest.tests.factories import (
    ImportRunFactory,
    MappingTableFactory,
    ReviewItemFactory,
    SourceItemFactory,
    SupplierSourceFactory,
)
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:ingest", source="admin", ip="127.0.0.1")


def test_a_write_run_needs_the_per_source_flag() -> None:
    source = SupplierSourceFactory.create(write_enabled=False)
    with pytest.raises(services.WriteNotEnabled):
        services.queue_run(source, ImportRun.Kind.PRICE, ctx=CTX, write=True)
    assert not ImportRun.objects.exists()
    run, _ = services.queue_run(source, ImportRun.Kind.PRICE, ctx=CTX)
    assert run.dry_run is True
    source.write_enabled = True
    source.save()
    run, _ = services.queue_run(source, ImportRun.Kind.PRICE, ctx=CTX, write=True)
    assert run.dry_run is False


def test_queue_run_dry_run_stores_nothing() -> None:
    source = SupplierSourceFactory.create()
    _, changes = services.queue_run(source, ImportRun.Kind.STOCK, ctx=CTX, dry_run=True)
    assert changes.summary()["create"] == 1
    assert not ImportRun.objects.exists()
    assert not AuditLog.objects.filter(action__startswith="ingest.").exists()


def test_a_run_goes_queued_running_succeeded_and_is_audited() -> None:
    run = ImportRunFactory.create()
    services.transition_run(run, ImportRun.Status.RUNNING, ctx=CTX)
    run.refresh_from_db()
    assert run.status == "running"
    assert run.started_at is not None
    assert run.finished_at is None
    services.transition_run(
        run, ImportRun.Status.SUCCEEDED, ctx=CTX, counters={"seen": 5, "created": 2, "unchanged": 3}
    )
    run.refresh_from_db()
    assert run.status == "succeeded"
    assert run.finished_at is not None
    assert (run.seen, run.created, run.unchanged) == (5, 2, 3)
    assert AuditLog.objects.filter(action="ingest.run.status_changed").count() == 2


def test_an_illegal_transition_is_refused_and_changes_nothing() -> None:
    run = ImportRunFactory.create()
    with pytest.raises(services.InvalidTransition):
        services.transition_run(run, ImportRun.Status.SUCCEEDED, ctx=CTX)
    run.refresh_from_db()
    assert run.status == "queued"


def test_a_finished_run_is_final() -> None:
    run = ImportRunFactory.create(status=ImportRun.Status.FAILED)
    with pytest.raises(services.InvalidTransition):
        services.transition_run(run, ImportRun.Status.RUNNING, ctx=CTX)


def test_transition_dry_run_changes_nothing() -> None:
    run = ImportRunFactory.create()
    changes = services.transition_run(run, ImportRun.Status.RUNNING, ctx=CTX, dry_run=True)
    assert changes.summary()["update"] == 1
    run.refresh_from_db()
    assert run.status == "queued"


def test_a_failed_run_keeps_its_log() -> None:
    run = ImportRunFactory.create(status=ImportRun.Status.RUNNING)
    services.transition_run(run, ImportRun.Status.FAILED, ctx=CTX, log="adapter timed out")
    run.refresh_from_db()
    assert run.log == "adapter timed out"


def test_an_ambiguous_sku_match_becomes_a_blocking_review_item() -> None:
    run = ImportRunFactory.create(status=ImportRun.Status.RUNNING)
    vendor = run.source.vendor
    # Two products cannot share a key, so the second one is reached through the alias history.
    first = ProductFactory.create(vendor=vendor, sku="NEW-1")
    second = ProductFactory.create(vendor=vendor, sku="OLD-2")
    second.sku = "NEW-2"
    second.save()
    first.sku = "OLD-2"  # now "old-2" is the live key of `first` and an alias of `second`
    first.save()
    item = SourceItemFactory.create(source=run.source, sku="OLD-2")

    match, _ = services.match_item(run, item, ctx=CTX)

    assert match.kind == "ambiguous"
    assert set(match.product_ids) == {first.pk, second.pk}
    review = ReviewItem.objects.get()
    assert (review.kind, review.blocking, review.decision) == ("duplicate_sku", True, "pending")
    assert review.run == run
    assert set(review.payload["product_ids"]) == {first.pk, second.pk}
    with pytest.raises(services.BlockedByReview):
        services.transition_run(run, ImportRun.Status.SUCCEEDED, ctx=CTX)


def test_a_repeated_ambiguous_match_does_not_duplicate_the_review_item() -> None:
    run = ImportRunFactory.create(status=ImportRun.Status.RUNNING)
    vendor = run.source.vendor
    first = ProductFactory.create(vendor=vendor, sku="NEW-1")
    second = ProductFactory.create(vendor=vendor, sku="OLD-2")
    second.sku = "NEW-2"
    second.save()
    first.sku = "OLD-2"
    first.save()
    item = SourceItemFactory.create(source=run.source, sku="OLD-2")
    services.match_item(run, item, ctx=CTX)
    services.match_item(run, item, ctx=CTX)
    assert ReviewItem.objects.count() == 1


def test_match_item_finds_an_exact_a_renamed_and_a_new_product() -> None:
    run = ImportRunFactory.create(status=ImportRun.Status.RUNNING)
    vendor = run.source.vendor
    product = ProductFactory.create(vendor=vendor, sku="KEEP-1")
    other = ProductFactory.create(vendor=vendor, sku="OLD-7")
    other.sku = "NEW-7"
    other.save()
    exact = SourceItemFactory.create(source=run.source, sku="keep-1")
    renamed = SourceItemFactory.create(source=run.source, sku="old-7")
    fresh = SourceItemFactory.create(source=run.source, sku="UNSEEN")
    assert services.match_item(run, exact, ctx=CTX)[0].product_ids == (product.pk,)
    assert services.match_item(run, renamed, ctx=CTX)[0].kind == "alias"
    assert services.match_item(run, fresh, ctx=CTX)[0].kind == "new"
    assert not ReviewItem.objects.exists()


def test_match_item_never_looks_at_another_vendors_products() -> None:
    run = ImportRunFactory.create(status=ImportRun.Status.RUNNING)
    ProductFactory.create(vendor=VendorFactory.create(), sku="SHARED-1")
    item = SourceItemFactory.create(source=run.source, sku="SHARED-1")
    assert services.match_item(run, item, ctx=CTX)[0].kind == "new"


def test_a_decision_is_recorded_audited_and_final() -> None:
    review = ReviewItemFactory.create(blocking=True)
    user = UserFactory.create(platform_staff=True)
    services.decide_review(
        review, ReviewItem.Decision.ACCEPTED, user=user, note="same product", ctx=CTX
    )
    review.refresh_from_db()
    assert review.decision == "accepted"
    assert review.resolved_by == user
    assert review.resolved_at is not None
    assert review.note == "same product"
    assert AuditLog.objects.filter(action="ingest.review.decided").count() == 1
    with pytest.raises(services.InvalidDecision):
        services.decide_review(review, ReviewItem.Decision.REJECTED, user=user, ctx=CTX)


def test_a_deferred_item_still_blocks_and_can_be_decided_later() -> None:
    run = ImportRunFactory.create(status=ImportRun.Status.RUNNING)
    review = ReviewItemFactory.create(run=run, blocking=True)
    user = UserFactory.create(platform_staff=True)
    services.decide_review(review, ReviewItem.Decision.DEFERRED, user=user, ctx=CTX)
    with pytest.raises(services.BlockedByReview):
        services.transition_run(run, ImportRun.Status.SUCCEEDED, ctx=CTX)
    services.decide_review(review, ReviewItem.Decision.REJECTED, user=user, ctx=CTX)
    services.transition_run(run, ImportRun.Status.SUCCEEDED, ctx=CTX)


def test_a_non_blocking_item_does_not_stop_the_run() -> None:
    run = ImportRunFactory.create(status=ImportRun.Status.RUNNING)
    ReviewItemFactory.create(run=run, blocking=False)
    services.transition_run(run, ImportRun.Status.SUCCEEDED, ctx=CTX)


def test_decide_dry_run_changes_nothing() -> None:
    review = ReviewItemFactory.create()
    services.decide_review(
        review, ReviewItem.Decision.REJECTED, user=UserFactory.create(), ctx=CTX, dry_run=True
    )
    review.refresh_from_db()
    assert review.decision == "pending"


def test_lookup_mapping_prefers_the_vendors_entry_over_the_platform_one() -> None:
    vendor = VendorFactory.create()
    MappingTableFactory.create(vendor=None, key="rosso", target_value="red")
    assert services.lookup_mapping(vendor, MappingTable.Kind.COLOR, " Rosso ").target_value == "red"
    MappingTableFactory.create(vendor=vendor, key="rosso", target_value="crimson")
    assert (
        services.lookup_mapping(vendor, MappingTable.Kind.COLOR, "ROSSO").target_value == "crimson"
    )
    other = VendorFactory.create()
    assert services.lookup_mapping(other, MappingTable.Kind.COLOR, "rosso").target_value == "red"
    assert services.lookup_mapping(vendor, MappingTable.Kind.COLOR, "verde") is None


def test_lookup_mapping_matches_an_alias() -> None:
    vendor = VendorFactory.create()
    MappingTableFactory.create(vendor=vendor, key="navy", aliases=["Blu Notte"], target_value="n")
    found = services.lookup_mapping(vendor, MappingTable.Kind.COLOR, "blu  notte")
    assert found is not None
    assert found.key == "navy"


def test_a_mapping_is_saved_through_the_audited_service() -> None:
    vendor = VendorFactory.create()
    entry = MappingTable(vendor=vendor, kind=MappingTable.Kind.MATERIAL, key="Cotone")
    entry.target_value = "cotton"
    services.save_mapping(entry, ctx=CTX)
    stored = MappingTable.objects.get()
    assert stored.key_norm == "cotone"
    assert AuditLog.objects.filter(action="ingest.mapping.created").count() == 1
    stored.target_value = "organic cotton"
    services.save_mapping(stored, ctx=CTX)
    assert AuditLog.objects.filter(action="ingest.mapping.updated").count() == 1
    services.delete_mapping(stored, ctx=CTX)
    assert not MappingTable.objects.exists()
    assert AuditLog.objects.filter(action="ingest.mapping.deleted").count() == 1


def test_a_mapping_target_is_a_catalog_object_or_a_value() -> None:
    product = ProductFactory.create()
    entry = MappingTable(vendor=product.vendor, kind=MappingTable.Kind.CATEGORY, key="lamps")
    entry.set_target(product)
    services.save_mapping(entry, ctx=CTX)
    assert MappingTable.objects.get().target == product
    with pytest.raises(ValidationError):
        services.save_mapping(
            MappingTable(vendor=product.vendor, kind=MappingTable.Kind.TERM, key="k"), ctx=CTX
        )
