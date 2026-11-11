"""factory_boy factories for ingest (ING.1). Vendors are pseudonyms, URLs use example.* domains."""

import datetime as dt
from typing import Any

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.catalog import engine as catalog_engine
from apps.ingest import engine
from apps.ingest.models import (
    ImportRun,
    MappingTable,
    MigrationChoice,
    MigrationRun,
    ProductDraft,
    ReviewItem,
    SourceItem,
    SourceObservation,
    SupplierSource,
)
from apps.vendors.tests.factories import VendorFactory


class SupplierSourceFactory(DjangoModelFactory[SupplierSource]):
    class Meta:
        model = SupplierSource

    vendor = factory.SubFactory(VendorFactory)
    code = factory.Sequence(lambda n: f"source-{n}")
    adapter_code = "csv_generic"
    config = factory.LazyFunction(dict)


class SourceItemFactory(DjangoModelFactory[SourceItem]):
    class Meta:
        model = SourceItem

    source = factory.SubFactory(SupplierSourceFactory)
    vendor = factory.SelfAttribute("source.vendor")
    source_url = factory.Sequence(lambda n: f"https://supplier.example/p/{n}")
    sku = factory.Sequence(lambda n: f"SUP-{n:05d}")
    sku_key = factory.LazyAttribute(lambda o: catalog_engine.sku_match_key(o.sku))
    last_seen_at = factory.LazyFunction(timezone.now)


class SourceObservationFactory(DjangoModelFactory[SourceObservation]):
    class Meta:
        model = SourceObservation

    item = factory.SubFactory(SourceItemFactory)
    observed_at = factory.LazyFunction(timezone.now)
    payload = factory.Sequence(lambda n: {"title": f"Item {n}", "price": "9.90"})
    payload_hash = factory.LazyAttribute(lambda o: engine.payload_hash(o.payload))


class ProductDraftFactory(DjangoModelFactory[ProductDraft]):
    class Meta:
        model = ProductDraft

    item = factory.SubFactory(SourceItemFactory)
    extracted = factory.LazyFunction(lambda: {"title": "Sample"})


class ImportRunFactory(DjangoModelFactory[ImportRun]):
    class Meta:
        model = ImportRun

    source = factory.SubFactory(SupplierSourceFactory)
    kind = ImportRun.Kind.SCRAPE


class ReviewItemFactory(DjangoModelFactory[ReviewItem]):
    class Meta:
        model = ReviewItem

    run = factory.SubFactory(ImportRunFactory)
    kind = ReviewItem.Kind.PRICE_JUMP
    blocking = False
    payload = factory.LazyFunction(lambda: {"old": "10.00", "new": "20.00"})


class MappingTableFactory(DjangoModelFactory[MappingTable]):
    class Meta:
        model = MappingTable

    vendor = factory.SubFactory(VendorFactory)
    kind = MappingTable.Kind.COLOR
    key = factory.Sequence(lambda n: f"colour-{n}")
    target_value = "blue"

    @classmethod
    def _adjust_kwargs(cls, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("aliases", [])
        return kwargs


def make_stale(item: SourceItem, days: int = 3) -> SourceItem:
    """An item last seen `days` ago."""
    item.last_seen_at = timezone.now() - dt.timedelta(days=days)
    item.save(update_fields=["last_seen_at"])
    return item


class MigrationRunFactory(DjangoModelFactory[MigrationRun]):
    """A cutover far enough away that the choice deadline is still open."""

    class Meta:
        model = MigrationRun

    label = factory.Sequence(lambda n: f"cutover-{n}")
    cutover_at = factory.LazyFunction(lambda: timezone.now() + dt.timedelta(days=60))


class MigrationChoiceFactory(DjangoModelFactory[MigrationChoice]):
    class Meta:
        model = MigrationChoice

    vendor = factory.SubFactory(VendorFactory)
    run = factory.SubFactory(MigrationRunFactory)
    version = 1
    period_months = 24
    data_types = factory.LazyFunction(lambda: list(engine.DATA_TYPES))
    include_unpublished = False
    login_window_months = 12
    source = engine.SOURCE_VENDOR
