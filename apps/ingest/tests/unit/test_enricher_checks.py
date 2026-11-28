"""ING.5: the system check for the enricher settings."""

import pytest
from django.test import override_settings

from apps.ingest.checks import check_enricher_settings


def ids() -> list[str]:
    return [m.id or "" for m in check_enricher_settings(None)]


def test_the_default_needs_no_key() -> None:
    assert ids() == []


@pytest.mark.parametrize("key", ["", None])
def test_a_selected_provider_without_a_key_is_an_error(key: str | None) -> None:
    with override_settings(INGEST_ENRICHER="gemini", INGEST_GEMINI_API_KEY=key or ""):
        assert ids() == ["ingest.E001"]


def test_a_selected_provider_with_a_key_passes() -> None:
    with override_settings(INGEST_ENRICHER="gemini", INGEST_GEMINI_API_KEY="k" * 20):
        assert ids() == []


def test_an_unknown_provider_is_an_error() -> None:
    with override_settings(INGEST_ENRICHER="other"):
        assert ids() == ["ingest.E002"]
