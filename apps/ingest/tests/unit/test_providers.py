"""ING.14 1/3: Claude and OpenAI adapters behind TextEnricher, transports mocked (no network)."""

import json
import secrets
from collections.abc import Mapping
from typing import Any

import pytest

from apps.ingest import enrich_engine as eng
from apps.ingest.enrichment import ProductFacts, ProviderError, TextPolicy
from apps.ingest.gemini import GeminiEnricher, RetryableProviderError
from apps.ingest.providers import ClaudeEnricher, OpenAIEnricher

FACTS = ProductFacts(sku="SKU-1", title="Ceramic mug", attributes={"colour": "black"})
POLICY = TextPolicy()
KEY = secrets.token_urlsafe(16)
GOOD = {
    "title": "Ceramic mug",
    "short_description": "A black mug.",
    "description": "A black ceramic mug.",
    "seo_title": "Ceramic mug",
    "seo_description": "A black ceramic mug.",
}


def gemini_reply(body: object) -> dict[str, Any]:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}]}


def claude_reply(body: object, stop: str = "end_turn") -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(body)}], "stop_reason": stop}


def openai_reply(body: object, finish: str = "stop", refusal: str | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"content": json.dumps(body), "refusal": refusal}
    return {"choices": [{"message": msg, "finish_reason": finish}]}


class Recorder:
    def __init__(self, *replies: object) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, Mapping[str, Any], Mapping[str, str], float]] = []

    def __call__(
        self, url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float
    ) -> Any:
        self.calls.append((url, payload, headers, timeout))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


def build(name: str, transport: Recorder, **kw: Any) -> Any:
    cls = {"gemini": GeminiEnricher, "claude": ClaudeEnricher, "openai": OpenAIEnricher}[name]
    return cls(KEY, "m-1", transport=transport, **kw)


REPLIES = {"gemini": gemini_reply, "claude": claude_reply, "openai": openai_reply}


@pytest.mark.parametrize("name", ["gemini", "claude", "openai"])
def test_same_product_passes_the_same_schema_through_each_provider(name: str) -> None:
    out = build(name, Recorder(REPLIES[name](GOOD))).describe(FACTS, "en", POLICY)
    assert out.provider == name
    text, errors = eng.validate_schema(out.raw, "en")
    assert errors == []
    assert text is not None
    assert eng.check_policy(text, POLICY) == []


@pytest.mark.parametrize("name", ["claude", "openai"])
def test_key_in_a_header_only_and_payload_is_product_data(name: str) -> None:
    rec = Recorder(REPLIES[name](GOOD))
    build(name, rec).describe(FACTS, "en", POLICY)
    url, payload, headers, _ = rec.calls[0]
    assert KEY not in url
    assert KEY not in json.dumps(payload)
    assert KEY in "".join(headers.values())
    blob = json.dumps(payload)
    assert "SKU-1" in blob
    assert "<product_data>" in blob
    assert url.startswith("https://")


@pytest.mark.parametrize("name", ["claude", "openai"])
def test_repr_and_errors_never_hold_the_key(name: str) -> None:
    enricher = build(name, Recorder(ProviderError("HTTP 401")))
    assert KEY not in repr(enricher)
    assert KEY not in str(vars(enricher).get("model"))
    with pytest.raises(ProviderError) as info:
        enricher.describe(FACTS, "en", POLICY)
    assert KEY not in str(info.value)


@pytest.mark.parametrize("name", ["claude", "openai"])
def test_construction_checks(name: str) -> None:
    cls = {"claude": ClaudeEnricher, "openai": OpenAIEnricher}[name]
    with pytest.raises(ProviderError):
        cls("", "m-1")
    with pytest.raises(ProviderError):
        cls(KEY, "../evil?x=1")


@pytest.mark.parametrize("name", ["claude", "openai"])
def test_retry_on_rate_limit_then_success_and_bounded(name: str) -> None:
    sleeps: list[float] = []
    rec = Recorder(RetryableProviderError("HTTP 429"), REPLIES[name](GOOD))
    out = build(name, rec, sleep=sleeps.append).describe(FACTS, "en", POLICY)
    assert out.raw == GOOD
    assert len(rec.calls) == 2
    assert len(sleeps) == 1
    always = Recorder(RetryableProviderError("HTTP 503"))
    with pytest.raises(ProviderError):
        build(name, always, sleep=sleeps.append).describe(FACTS, "en", POLICY)
    assert len(always.calls) == 3


@pytest.mark.parametrize("name", ["claude", "openai"])
@pytest.mark.parametrize(
    "bad",
    [None, "text", {}, {"content": []}, {"choices": []}, {"content": [{"text": "not json"}]}],
)
def test_malformed_answers_map_to_provider_error(name: str, bad: object) -> None:
    with pytest.raises(ProviderError):
        build(name, Recorder(bad)).describe(FACTS, "en", POLICY)


def test_claude_truncated_or_refused_is_an_error() -> None:
    for stop in ("max_tokens", "refusal"):
        with pytest.raises(ProviderError):
            build("claude", Recorder(claude_reply(GOOD, stop))).describe(FACTS, "en", POLICY)


def test_openai_refusal_or_length_is_an_error() -> None:
    with pytest.raises(ProviderError):
        build("openai", Recorder(openai_reply(GOOD, refusal="no"))).describe(FACTS, "en", POLICY)
    with pytest.raises(ProviderError):
        build("openai", Recorder(openai_reply(GOOD, finish="length"))).describe(FACTS, "en", POLICY)


def test_claude_skips_a_leading_non_text_block() -> None:
    reply = {
        "content": [
            {"type": "thinking", "thinking": "x"},
            {"type": "text", "text": json.dumps(GOOD)},
        ],
        "stop_reason": "end_turn",
    }
    assert build("claude", Recorder(reply)).describe(FACTS, "en", POLICY).raw == GOOD
    with pytest.raises(ProviderError):
        build(
            "claude", Recorder({"content": [{"type": "thinking"}], "stop_reason": "end_turn"})
        ).describe(FACTS, "en", POLICY)


def test_non_object_json_is_an_error() -> None:
    with pytest.raises(ProviderError):
        build("claude", Recorder(claude_reply([1, 2]))).describe(FACTS, "en", POLICY)
