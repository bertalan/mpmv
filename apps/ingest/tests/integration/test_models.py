"""ING.1: the staging schema (uniqueness per vendor, append-only observations, constraints)."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.catalog.tests.factories import ProductFactory
from apps.ingest.models import ImportRun, MappingTable, ReviewItem, SourceItem
from apps.ingest.tests.factories import (
    ImportRunFactory,
    MappingTableFactory,
    ProductDraftFactory,
    ReviewItemFactory,
    SourceItemFactory,
    SourceObservationFactory,
    SupplierSourceFactory,
)
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db


def test_the_sku_match_key_follows_the_sku() -> None:
    item = SourceItemFactory.create(sku="ab 1")
    assert item.sku_key == "ab 1"
    item.sku = "  AB   2 "
    item.save()
    item.refresh_from_db()
    assert item.sku_key == "ab 2"
    item.sku = ""
    item.save()
    assert item.sku_key == ""


def test_two_feed_rows_with_one_sku_are_stored_and_left_to_the_review() -> None:
    vendor = VendorFactory.create()
    SourceItemFactory.create(source__vendor=vendor, sku="ab 1")
    SourceItemFactory.create(source=SupplierSourceFactory.create(vendor=vendor), sku="AB  1")
    assert SourceItem.objects.filter(vendor=vendor, sku_key="ab 1").count() == 2


def test_the_same_sku_may_exist_for_two_vendors() -> None:
    SourceItemFactory.create(sku="SAME-1")
    SourceItemFactory.create(sku="SAME-1")
    assert SourceItem.objects.filter(sku_key="same-1").count() == 2


def test_items_without_a_sku_do_not_collide() -> None:
    vendor = VendorFactory.create()
    SourceItemFactory.create(source__vendor=vendor, sku="", sku_key="")
    SourceItemFactory.create(source__vendor=vendor, sku="", sku_key="")


def test_the_source_url_is_unique_per_source() -> None:
    item = SourceItemFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        SourceItemFactory.create(source=item.source, source_url=item.source_url, sku="OTHER")


def test_an_item_belongs_to_the_vendor_of_its_source() -> None:
    source = SupplierSourceFactory.create()
    item = SourceItemFactory.build(source=source, vendor=VendorFactory.create())
    with pytest.raises(ValidationError):
        item.full_clean()


def test_a_source_code_is_unique_per_vendor() -> None:
    source = SupplierSourceFactory.create(code="main")
    with pytest.raises(IntegrityError), transaction.atomic():
        SupplierSourceFactory.create(vendor=source.vendor, code="main")
    SupplierSourceFactory.create(code="main")  # another vendor: fine


def test_an_observation_version_is_stored_once_per_item() -> None:
    first = SourceObservationFactory.create(payload={"a": 1})
    with pytest.raises(IntegrityError), transaction.atomic():
        SourceObservationFactory.create(item=first.item, payload={"a": 1})
    SourceObservationFactory.create(item=first.item, payload={"a": 2})


def test_a_draft_has_a_status_and_one_per_item() -> None:
    draft = ProductDraftFactory.create()
    assert draft.status == "new"
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductDraftFactory.create(item=draft.item)


def test_a_product_is_referenced_by_identity_and_survives_the_item() -> None:
    product = ProductFactory.create()
    item = SourceItemFactory.create(source__vendor=product.vendor, product=product)
    product.delete()
    item.refresh_from_db()
    assert item.product is None


def test_a_new_run_is_a_queued_dry_run_with_zero_counters() -> None:
    run = ImportRunFactory.create()
    assert (run.status, run.dry_run) == (ImportRun.Status.QUEUED, True)
    assert (run.seen, run.created, run.updated, run.unchanged, run.errors) == (0, 0, 0, 0, 0)
    assert run.started_at is None
    assert run.finished_at is None


def test_run_status_and_counters_are_constrained() -> None:
    run = ImportRunFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        ImportRun.objects.filter(pk=run.pk).update(status="bogus")
    with pytest.raises(IntegrityError), transaction.atomic():
        ImportRun.objects.filter(pk=run.pk).update(seen=-1)


def test_a_decided_review_item_needs_its_decision_time() -> None:
    item = ReviewItemFactory.create()
    assert item.decision == ReviewItem.Decision.PENDING
    with pytest.raises(IntegrityError), transaction.atomic():
        ReviewItem.objects.filter(pk=item.pk).update(decision="accepted")


def test_a_mapping_key_is_unique_per_vendor_and_kind_after_normalising() -> None:
    entry = MappingTableFactory.create(key="Rosso")
    assert entry.key_norm == "rosso"
    with pytest.raises(IntegrityError), transaction.atomic():
        MappingTableFactory.create(vendor=entry.vendor, kind=entry.kind, key=" ROSSO ")
    MappingTableFactory.create(vendor=entry.vendor, kind=MappingTable.Kind.MATERIAL, key="rosso")
    MappingTableFactory.create(key="rosso")  # another vendor


def test_a_global_mapping_key_is_unique_too() -> None:
    MappingTableFactory.create(vendor=None, key="Red")
    with pytest.raises(IntegrityError), transaction.atomic():
        MappingTableFactory.create(vendor=None, key="red")


def test_a_mapping_has_a_target_value_or_a_target_object_not_neither() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        MappingTableFactory.create(target_value="")


def test_the_review_and_run_lookups_are_indexed() -> None:
    names = {i.name for model in (ReviewItem, ImportRun) for i in model._meta.indexes}
    assert {"ingest_review_run_block_dec", "ingest_run_source_status"} <= names
