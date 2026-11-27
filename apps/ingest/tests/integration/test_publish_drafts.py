"""ING.7: `publish_drafts` goes through `catalog.services`: dry-run, audit, EN only (D-18 bis)."""

from decimal import Decimal

import pytest

from apps.catalog import publishing
from apps.catalog.models import Product, ProductTranslation
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.core.audit import AuditContext
from apps.core.i18n import primary_language
from apps.core.models import AuditLog
from apps.ingest import services
from apps.ingest.models import ProductDraft
from apps.ingest.tests.factories import ProductDraftFactory, SourceItemFactory
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import other_language

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="staff:ingest", source="admin", ip="127.0.0.1")


@pytest.fixture(autouse=True)
def _no_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publishing, "_CHECKS", {})


def ready_draft(**overrides: object) -> ProductDraft:
    data: dict[str, object] = {
        "status": "ready",
        "extracted": {"title": "Raw", "price": "10.00", "weight_kg": "0.5"},
        "enriched": {primary_language(): {"title": "Nice title", "description": "Long text"}},
    }
    data.update(overrides)
    return ProductDraftFactory.create(**data)


def test_a_ready_draft_becomes_a_published_product_in_one_step() -> None:
    draft = ready_draft()
    report = services.publish_drafts([draft], ctx=CTX, dry_run=False)
    assert report.published == [draft.item.sku]
    product = Product.objects.get(vendor=draft.item.vendor, sku=draft.item.sku)
    assert product.status == "published"
    assert product.price_net == Decimal("10.00")
    assert product.translations.get().title == "Nice title"
    draft.refresh_from_db()
    assert draft.status == "published"
    assert draft.item.product_id == product.pk
    assert AuditLog.objects.filter(action=services.DRAFT_PUBLISHED).count() == 1
    assert AuditLog.objects.filter(action="catalog.product.status_changed").count() == 1


def test_a_dry_run_writes_nothing_and_reports_the_changes() -> None:
    draft = ready_draft()
    report = services.publish_drafts([draft], ctx=CTX, dry_run=True)
    assert report.published == [draft.item.sku]
    assert not report.changes.is_empty
    assert not Product.objects.exists()
    draft.refresh_from_db()
    assert draft.status == "ready"
    assert not AuditLog.objects.filter(action__startswith="ingest.draft").exists()


def test_a_new_draft_publishes_english_only() -> None:
    draft = ready_draft(
        enriched={
            primary_language(): {"title": "Nice title"},
            other_language(): {"title": "Titolo"},
        }
    )
    services.publish_drafts([draft], ctx=CTX, dry_run=False)
    product = Product.objects.get(sku=draft.item.sku)
    assert list(product.translations.values_list("language", flat=True)) == [primary_language()]


def test_a_migrated_translation_stays_published_and_is_kept() -> None:
    vendor = VendorFactory.create()
    item = SourceItemFactory.create(source__vendor=vendor)
    migrated = ProductFactory.create(vendor=vendor, sku=item.sku, status="published")
    text = ProductTranslationFactory.create(
        product=migrated, language=other_language(), title="Titolo migrato", slug="titolo-migrato"
    )
    draft = ready_draft(item=item)
    services.publish_drafts([draft], ctx=CTX, dry_run=False)
    migrated.refresh_from_db()
    assert migrated.status == "published"
    kept = ProductTranslation.objects.get(pk=text.pk)
    assert (kept.title, kept.slug) == ("Titolo migrato", "titolo-migrato")
    assert set(migrated.translations.values_list("language", flat=True)) == {
        primary_language(),
        other_language(),
    }
    draft.refresh_from_db()
    assert draft.item.product_id == migrated.pk


def test_drafts_that_are_not_ready_or_not_valid_are_skipped_with_a_reason() -> None:
    new = ready_draft(status="new")
    broken = ready_draft(extracted={"price": "10"}, enriched={})
    report = services.publish_drafts([new, broken], ctx=CTX, dry_run=False)
    assert report.published == []
    assert set(report.skipped) == {new.item.sku, broken.item.sku}
    assert not Product.objects.exists()


def test_a_product_that_fails_the_publication_rules_stays_a_draft_and_the_ingest_draft_ready() -> (
    None
):
    draft = ready_draft(extracted={"title": "Raw", "price": "10.00"})  # a buy product needs weight
    report = services.publish_drafts([draft], ctx=CTX, dry_run=False)
    assert report.published == []
    assert draft.item.sku in report.skipped
    draft.refresh_from_db()
    assert draft.status == "ready"
    assert not Product.objects.filter(status="published").exists()
