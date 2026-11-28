"""ING.14 1/3: a failing provider leaves the draft's text unchanged, with each real adapter."""

import secrets
from collections.abc import Mapping
from typing import Any

import pytest

from apps.core.audit import AuditContext
from apps.ingest import services
from apps.ingest.gemini import GeminiEnricher
from apps.ingest.models import EnrichmentVersion
from apps.ingest.provider_http import RetryableProviderError
from apps.ingest.providers import ClaudeEnricher, OpenAIEnricher
from apps.ingest.tests.factories import ImportRunFactory, ProductDraftFactory

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="staff:ingest", source="admin", ip="127.0.0.1")
KEY = secrets.token_urlsafe(16)


def failing(
    url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float
) -> Any:
    raise RetryableProviderError("HTTP 503")


@pytest.mark.parametrize("cls", [GeminiEnricher, ClaudeEnricher, OpenAIEnricher])
def test_a_provider_error_leaves_the_text_unchanged_for_each_adapter(cls: Any) -> None:
    draft = ProductDraftFactory.create(extracted={"title": "Ceramic mug"})
    run = ImportRunFactory.create(source=draft.item.source)
    enricher = cls(KEY, "m-1", transport=failing, sleep=lambda seconds: None)
    result = services.enrich_draft(draft, enricher, run=run, ctx=CTX)
    assert result.outcome == "provider_error"
    draft.refresh_from_db()
    assert draft.enriched == {}
    assert not EnrichmentVersion.objects.exists()
