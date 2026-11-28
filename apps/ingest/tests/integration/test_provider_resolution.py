"""ING.14 3/3: which enricher a vendor's drafts use, key rotation and per-vendor usage."""

import json
import secrets
from collections.abc import Mapping
from typing import Any

import pytest
from django.conf import settings
from django.db import connection
from django.test import override_settings

from apps.core.audit import AuditContext
from apps.core.crypto import generate_key
from apps.core.encryption import key_id_of_raw
from apps.core.models import AuditLog
from apps.core.services import rotate_field_keys
from apps.ingest import provider_services as svc
from apps.ingest import services
from apps.ingest.enrichment import FakeEnricher, ProviderError
from apps.ingest.gemini import GeminiEnricher
from apps.ingest.models import EnrichmentVersion, ProductDraft, VendorAIProvider
from apps.ingest.providers import ClaudeEnricher, OpenAIEnricher
from apps.ingest.tests.factories import (
    ImportRunFactory,
    ProductDraftFactory,
    VendorAIProviderFactory,
)
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="staff:ingest", source="admin", ip="127.0.0.1")
GOOD = {
    "title": "Ceramic mug",
    "short_description": "A black mug.",
    "description": "A black ceramic mug.",
    "seo_title": "Ceramic mug",
    "seo_description": "A black ceramic mug.",
}


def noop_transport(
    url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float
) -> Any:
    raise AssertionError("no network in resolution tests")


def test_default_is_the_fake_enricher_without_a_vendor_choice() -> None:
    assert isinstance(svc.resolve_enricher(VendorFactory.create()), FakeEnricher)


@pytest.mark.parametrize(
    ("provider", "cls"),
    [("claude", ClaudeEnricher), ("openai", OpenAIEnricher), ("gemini", GeminiEnricher)],
)
def test_a_vendor_with_a_key_gets_its_own_provider(provider: str, cls: type) -> None:
    vendor = VendorFactory.create()
    VendorAIProviderFactory.create(vendor=vendor, provider=provider, model="m-1")
    enricher = svc.resolve_enricher(vendor, transport=noop_transport)
    assert isinstance(enricher, cls)
    assert enricher.model == "m-1"


def test_a_choice_without_a_key_falls_back_to_the_platform_default() -> None:
    vendor = VendorFactory.create()
    VendorAIProviderFactory.create(vendor=vendor, provider="claude", api_key="")
    assert isinstance(svc.resolve_enricher(vendor), FakeEnricher)


def test_the_choice_applies_only_to_that_vendors_products() -> None:
    a, b = VendorFactory.create(), VendorFactory.create()
    VendorAIProviderFactory.create(vendor=a, provider="claude")
    assert isinstance(svc.resolve_enricher(a, transport=noop_transport), ClaudeEnricher)
    assert isinstance(svc.resolve_enricher(b), FakeEnricher)


def test_platform_default_can_be_a_real_provider_with_its_own_key() -> None:
    vendor = VendorFactory.create()
    key = secrets.token_urlsafe(16)
    with override_settings(INGEST_ENRICHER="openai", INGEST_OPENAI_API_KEY=key):
        assert isinstance(svc.resolve_enricher(vendor, transport=noop_transport), OpenAIEnricher)
    with override_settings(INGEST_ENRICHER="claude", INGEST_CLAUDE_API_KEY=key):
        assert isinstance(svc.resolve_enricher(vendor, transport=noop_transport), ClaudeEnricher)


def test_an_unreadable_vendor_key_never_falls_back_to_another_provider() -> None:
    vendor = VendorFactory.create()
    VendorAIProviderFactory.create(vendor=vendor, provider="claude")
    with (
        override_settings(FIELD_ENCRYPTION_KEYS=[generate_key("other")], INGEST_ENRICHER="fake"),
        pytest.raises(ProviderError),
    ):
        svc.resolve_enricher(vendor)


def test_vendor_b_cannot_use_vendor_a_key() -> None:
    a, b = VendorFactory.create(), VendorFactory.create()
    row = VendorAIProviderFactory.create(vendor=a, provider="claude")
    enricher = svc.resolve_enricher(b)
    assert isinstance(enricher, FakeEnricher)
    assert svc.key_for(b) is None
    assert row.api_key


def test_the_key_stays_out_of_the_archive_audit_and_review_after_an_enrichment() -> None:
    vendor = VendorFactory.create()
    key = secrets.token_urlsafe(24)
    VendorAIProviderFactory.create(vendor=vendor, provider="claude", api_key=key)
    seen: list[Mapping[str, str]] = []

    def transport(
        url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float
    ) -> Any:
        seen.append(headers)
        return {"content": [{"text": json.dumps(GOOD)}], "stop_reason": "end_turn"}

    draft = ProductDraftFactory.create(
        extracted={"title": "Ceramic mug"}, item__vendor=vendor, item__source__vendor=vendor
    )
    run = ImportRunFactory.create(source=draft.item.source)
    enricher = svc.resolve_enricher(vendor, transport=transport)
    result = services.enrich_draft(draft, enricher, run=run, ctx=CTX)
    assert result.outcome == "accepted"
    assert key in "".join(seen[0].values())  # sent to its own provider, in a header only
    dump = json.dumps(
        [
            *EnrichmentVersion.objects.values_list("raw", "violations", "provider", "model"),
            *AuditLog.objects.values_list("before", "after"),
            *ProductDraft.objects.values_list("enriched", "extracted"),
        ],
        default=str,
    )
    assert key not in dump


def test_a_provider_error_leaves_the_text_unchanged_and_does_not_try_another() -> None:
    vendor = VendorFactory.create()
    VendorAIProviderFactory.create(vendor=vendor, provider="claude")
    calls: list[str] = []

    def failing(
        url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float
    ) -> Any:
        calls.append(url)
        raise ProviderError("HTTP 401")

    draft = ProductDraftFactory.create(
        extracted={"title": "Ceramic mug"}, item__vendor=vendor, item__source__vendor=vendor
    )
    run = ImportRunFactory.create(source=draft.item.source)
    result = services.enrich_draft(
        draft, svc.resolve_enricher(vendor, transport=failing), run=run, ctx=CTX
    )
    assert result.outcome == "provider_error"
    draft.refresh_from_db()
    assert draft.enriched == {}
    assert len(calls) == 1
    assert all("anthropic" in url for url in calls)


def test_usage_is_counted_per_vendor() -> None:
    a, b = VendorFactory.create(), VendorFactory.create()
    for vendor, times in ((a, 2), (b, 1)):
        for _ in range(times):
            draft = ProductDraftFactory.create(
                extracted={"title": "Ceramic mug"},
                item__vendor=vendor,
                item__source__vendor=vendor,
            )
            run = ImportRunFactory.create(source=draft.item.source)
            services.enrich_draft(draft, FakeEnricher(), run=run, ctx=CTX)
    assert (svc.usage_count(a), svc.usage_count(b)) == (2, 1)


# --- rotation: old ciphertext stays readable, the re-encrypt tool moves it


def raw_key_id(row: VendorAIProvider) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT api_key FROM ingest_vendoraiprovider WHERE id = %s", [row.pk])
        found = cursor.fetchone()
    assert found is not None
    return key_id_of_raw(str(found[0]))


def test_rotation_keeps_old_ciphertext_readable_and_the_job_moves_it() -> None:
    vendor = VendorFactory.create()
    key = secrets.token_urlsafe(24)
    row = VendorAIProviderFactory.create(vendor=vendor, api_key=key)
    old, new = str(settings.FIELD_ENCRYPTION_KEYS[0]), generate_key("k2")
    assert raw_key_id(row) == "test"
    with override_settings(FIELD_ENCRYPTION_KEYS=[new, old]):
        assert svc.key_for(vendor) == key  # the old ciphertext still decrypts
        preview = rotate_field_keys(ctx=CTX, dry_run=True)
        assert [c.model for c in preview.changes] == ["ingest.vendoraiprovider"]
        assert raw_key_id(row) == "test"
        rotate_field_keys(ctx=CTX)
        assert raw_key_id(row) == "k2"
        assert svc.key_for(vendor) == key
        entry = AuditLog.objects.get(target_model="ingest.vendoraiprovider")
        assert key not in str(entry.before) + str(entry.after)
        assert rotate_field_keys(ctx=CTX).is_empty
    with override_settings(FIELD_ENCRYPTION_KEYS=[new]):  # the old key can be retired
        assert svc.key_for(vendor) == key
