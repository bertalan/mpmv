"""ING.5 2/3: running an enricher on a draft (archive, review, EN-only, IT protection)."""

from typing import Any

import pytest

from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.ingest import services
from apps.ingest.enrichment import FakeEnricher, TextPolicy
from apps.ingest.models import EnrichmentVersion, ImportRun, ProductDraft, ReviewItem
from apps.ingest.tests.factories import (
    EnrichmentVersionFactory,
    ImportRunFactory,
    ProductDraftFactory,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:ingest", source="admin", ip="127.0.0.1")


def make_draft(**kwargs: Any) -> tuple[ProductDraft, ImportRun]:
    draft = ProductDraftFactory.create(extracted={"title": "Ceramic mug"}, **kwargs)
    run = ImportRunFactory.create(source=draft.item.source)
    return draft, run


def test_in_policy_output_is_applied_archived_and_audited() -> None:
    draft, run = make_draft()
    result = services.enrich_draft(draft, FakeEnricher(), run=run, ctx=CTX)
    assert result.outcome == "accepted"
    draft.refresh_from_db()
    assert draft.enriched["en"]["title"] == "Ceramic mug"
    assert draft.enriched["en"]["origin"] == "machine"
    version = EnrichmentVersion.objects.get()
    assert (version.provider, version.model, version.prompt_version) == ("fake", "fake-1", "v1")
    assert version.outcome == "accepted"
    assert AuditLog.objects.filter(action=services.DRAFT_ENRICHED).count() == 1
    assert AuditLog.objects.filter(action=services.ENRICHMENT_ARCHIVED).count() == 1
    assert not ReviewItem.objects.exists()


def test_out_of_policy_output_goes_to_review_and_is_not_applied() -> None:
    draft, run = make_draft()
    fake = FakeEnricher({"description": "An eco-friendly mug"})
    result = services.enrich_draft(draft, fake, run=run, ctx=CTX)
    assert result.outcome == "out_of_policy"
    draft.refresh_from_db()
    assert draft.enriched == {}
    assert draft.status == ProductDraft.Status.NEEDS_REVIEW
    review = ReviewItem.objects.get()
    assert review.kind == ReviewItem.Kind.TEXT_POLICY
    assert review.blocking is False
    assert review.item == draft.item
    assert any("environmental" in v for v in review.payload["violations"])
    assert EnrichmentVersion.objects.get().outcome == "out_of_policy"


def test_schema_invalid_output_goes_to_review() -> None:
    draft, run = make_draft()
    result = services.enrich_draft(draft, FakeEnricher({"extra": "x"}), run=run, ctx=CTX)
    assert result.outcome == "schema_invalid"
    draft.refresh_from_db()
    assert draft.enriched == {}
    assert ReviewItem.objects.get().kind == ReviewItem.Kind.TEXT_POLICY


def test_dry_run_changes_nothing_and_returns_the_changeset() -> None:
    draft, run = make_draft()
    result = services.enrich_draft(draft, FakeEnricher(), run=run, ctx=CTX, dry_run=True)
    assert not result.changes.is_empty
    assert not EnrichmentVersion.objects.exists()
    assert not AuditLog.objects.filter(action=services.DRAFT_ENRICHED).exists()
    draft.refresh_from_db()
    assert draft.enriched == {}


def test_provider_error_leaves_the_text_unchanged() -> None:
    draft, run = make_draft()
    result = services.enrich_draft(draft, FakeEnricher(error="quota"), run=run, ctx=CTX)
    assert result.outcome == "provider_error"
    draft.refresh_from_db()
    assert draft.enriched == {}
    assert not EnrichmentVersion.objects.exists()


def test_only_english_is_written() -> None:
    draft, run = make_draft()
    fake = FakeEnricher()
    with pytest.raises(services.EnrichmentRefused):
        services.enrich_draft(draft, fake, run=run, ctx=CTX, lang="it")
    assert fake.calls == []  # the provider is not even called


def test_existing_texts_in_a_language_are_never_overwritten() -> None:
    migrated = {"it": {"title": "Tazza"}}
    draft, run = make_draft(enriched=migrated)
    fake = FakeEnricher()
    policy = TextPolicy(languages=("en", "it"))  # even if a policy allowed IT
    with pytest.raises(services.EnrichmentRefused):
        services.enrich_draft(draft, fake, run=run, ctx=CTX, lang="it", policy=policy)
    draft.refresh_from_db()
    assert draft.enriched == migrated
    assert fake.calls == []


def test_an_existing_catalog_translation_blocks_the_language() -> None:
    product = ProductFactory.create()
    language = ProductTranslationFactory.create(product=product).language
    draft, run = make_draft(item__product=product, item__source__vendor=product.vendor)
    fake = FakeEnricher()
    policy = TextPolicy(languages=(language,))
    with pytest.raises(services.EnrichmentRefused):
        services.enrich_draft(draft, fake, run=run, ctx=CTX, lang=language, policy=policy)
    assert fake.calls == []


def test_run_of_another_vendor_is_refused() -> None:
    draft, _ = make_draft()
    other = ImportRunFactory.create()
    with pytest.raises(services.EnrichmentRefused):
        services.enrich_draft(draft, FakeEnricher(), run=other, ctx=CTX)


def test_archive_lists_the_newest_version_first() -> None:
    draft, _ = make_draft()
    older = EnrichmentVersionFactory.create(draft=draft, outcome="out_of_policy")
    newer = EnrichmentVersionFactory.create(draft=draft)
    assert list(draft.enrichment_versions.all()) == [newer, older]
