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


# --- review fixes: dry-run parity, locking, retired products


def _outcome(report: services.PublishReport) -> tuple[list[str], list[str]]:
    return report.published, sorted(report.skipped)


def _existing(status: str, language: str) -> ProductDraft:
    vendor = VendorFactory.create()
    item = SourceItemFactory.create(source__vendor=vendor)
    product = ProductFactory.create(vendor=vendor, sku=item.sku, status=status)
    ProductTranslationFactory.create(product=product, language=language, slug="kept-text")
    return ready_draft(item=item)


@pytest.mark.parametrize("status", ["draft", "published", "retired"])
def test_a_dry_run_and_the_real_run_agree_for_an_existing_product(status: str) -> None:
    draft = _existing(status, other_language())
    dry = services.publish_drafts([draft], ctx=CTX, dry_run=True)
    real = services.publish_drafts([draft], ctx=CTX, dry_run=False)
    assert _outcome(dry) == _outcome(real)
    assert (draft.item.sku in real.published) == (status != "retired")
    if status == "retired":
        assert draft.item.sku in real.refused
        assert Product.objects.get(sku=draft.item.sku).status == "retired"


def test_a_dry_run_and_the_real_run_agree_for_a_new_and_a_refused_draft() -> None:
    good = ready_draft()
    bad = ready_draft(extracted={"title": "Raw", "price": "10.00"})  # no weight
    dry = services.publish_drafts([good, bad], ctx=CTX, dry_run=True)
    real = services.publish_drafts([good, bad], ctx=CTX, dry_run=False)
    assert _outcome(dry) == _outcome(real) == ([good.item.sku], [bad.item.sku])
    assert real.refused == {bad.item.sku}


def test_a_stale_draft_object_is_not_published_when_the_row_changed() -> None:
    draft = ready_draft()
    ProductDraft.objects.filter(pk=draft.pk).update(status="needs_review")
    report = services.publish_drafts([draft], ctx=CTX, dry_run=False)  # still says ready
    assert report.published == []
    assert not Product.objects.exists()


def test_a_concurrent_creation_is_a_skip_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from django.db import IntegrityError

    from apps.catalog import services as catalog_services

    def clash(*args: object, **kwargs: object) -> None:
        raise IntegrityError("duplicate key")

    monkeypatch.setattr(catalog_services, "upsert_products", clash)
    first, second = ready_draft(), ready_draft()
    report = services.publish_drafts([first, second], ctx=CTX, dry_run=False)
    assert report.published == []
    assert set(report.skipped) == {first.item.sku, second.item.sku}  # the batch went on
    assert report.refused == set()


def test_two_vendors_with_the_same_sku_each_run_publishes_only_its_own_draft() -> None:
    a_draft = ready_draft(item=SourceItemFactory.create(sku="SAME-1"))
    b_draft = ready_draft(item=SourceItemFactory.create(sku="SAME-1"))
    report = services.publish_drafts([a_draft], ctx=CTX, dry_run=False)
    assert report.published == ["SAME-1"]
    a_draft.refresh_from_db()
    b_draft.refresh_from_db()
    assert (a_draft.status, b_draft.status) == ("published", "ready")
    assert Product.objects.get().vendor_id == a_draft.item.vendor_id
    services.publish_drafts([b_draft], ctx=CTX, dry_run=False)
    assert {p.vendor_id for p in Product.objects.all()} == {
        a_draft.item.vendor_id,
        b_draft.item.vendor_id,
    }
    a_draft.item.refresh_from_db()
    b_draft.item.refresh_from_db()
    assert a_draft.item.product is not None
    assert b_draft.item.product is not None
    assert a_draft.item.product.vendor_id == a_draft.item.vendor_id
    assert b_draft.item.product.vendor_id == b_draft.item.vendor_id
