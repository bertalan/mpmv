"""ING.5 3/3: the Gemini adapter with the HTTP transport mocked (no network, no real key)."""

import json
import secrets
from collections.abc import Mapping
from typing import Any

import pytest
from django.test import override_settings

from apps.ingest import enrich_engine as eng
from apps.ingest.enrichment import FakeEnricher, ProductFacts, ProviderError, TextPolicy
from apps.ingest.gemini import GeminiEnricher, get_enricher, http_post_json

FACTS = ProductFacts(sku="SKU-1", title="Ceramic mug", attributes={"colour": "black"})
POLICY = TextPolicy()
KEY = secrets.token_urlsafe(16)


def answer(body: object) -> dict[str, Any]:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}]}


GOOD = {
    "title": "Ceramic mug",
    "short_description": "A black mug.",
    "description": "A black ceramic mug.",
    "seo_title": "Ceramic mug",
    "seo_description": "A black ceramic mug.",
}


class Recorder:
    def __init__(self, reply: object) -> None:
        self.reply = reply
        self.calls: list[tuple[str, Mapping[str, Any], Mapping[str, str], float]] = []

    def __call__(
        self, url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float
    ) -> Any:
        self.calls.append((url, payload, headers, timeout))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_answer_passes_the_same_schema_and_policy() -> None:
    transport = Recorder(answer(GOOD))
    out = GeminiEnricher(KEY, "m-1", transport=transport).describe(FACTS, "en", POLICY)
    assert (out.provider, out.model, out.prompt_version) == ("gemini", "m-1", "gemini-v1")
    text, errors = eng.validate_schema(out.raw, "en")
    assert errors == []
    assert text is not None
    assert eng.check_policy(text, POLICY) == []


def test_key_is_in_a_header_never_in_url_or_payload() -> None:
    transport = Recorder(answer(GOOD))
    GeminiEnricher(KEY, "m-1", transport=transport).describe(FACTS, "en", POLICY)
    url, payload, headers, _ = transport.calls[0]
    assert KEY not in url
    assert KEY not in json.dumps(payload)
    assert headers == {"x-goog-api-key": KEY}
    assert "m-1:generateContent" in url


def test_payload_holds_product_data_only() -> None:
    transport = Recorder(answer(GOOD))
    GeminiEnricher(KEY, "m-1", transport=transport).describe(FACTS, "en", POLICY)
    prompt = transport.calls[0][1]["contents"][0]["parts"][0]["text"]
    assert "Ceramic mug" in prompt
    assert "SKU-1" in prompt
    assert "colour: black" in prompt


@pytest.mark.parametrize(
    "reply",
    [{}, {"candidates": []}, {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]},
     answer(["a list"])],
)  # fmt: skip
def test_bad_answer_shapes_are_provider_errors(reply: object) -> None:
    with pytest.raises(ProviderError):
        GeminiEnricher(KEY, "m", transport=Recorder(reply)).describe(FACTS, "en", POLICY)


def test_transport_errors_propagate_without_the_key() -> None:
    enricher = GeminiEnricher(KEY, "m", transport=Recorder(ProviderError("HTTP 429")))
    with pytest.raises(ProviderError) as info:
        enricher.describe(FACTS, "en", POLICY)
    assert KEY not in str(info.value)


def test_no_key_is_refused_at_construction() -> None:
    with pytest.raises(ProviderError):
        GeminiEnricher("", "m")


def test_default_switch_is_the_fake() -> None:
    assert isinstance(get_enricher(), FakeEnricher)


def test_switch_picks_gemini_and_rejects_unknown() -> None:
    with override_settings(INGEST_ENRICHER="gemini", INGEST_GEMINI_API_KEY=KEY):
        assert isinstance(get_enricher(), GeminiEnricher)
    with (
        override_settings(INGEST_ENRICHER="gemini", INGEST_GEMINI_API_KEY=""),
        pytest.raises(ProviderError),
    ):
        get_enricher()
    with override_settings(INGEST_ENRICHER="nope"), pytest.raises(ProviderError):
        get_enricher()


def test_http_errors_become_provider_errors_without_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    def boom(request: object, *, timeout: float) -> object:
        raise urllib.error.HTTPError("https://secret.example/?key=abc", 503, "x", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr("apps.ingest.gemini.open_url", boom)
    with pytest.raises(ProviderError) as info:
        http_post_json("https://x.example/", {}, {}, 1.0)
    assert str(info.value) == "HTTP 503"
