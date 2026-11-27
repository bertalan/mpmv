"""ING.5: the Gemini adapter behind ``TextEnricher``. Other providers are ING.14.

Only product data goes out (``ProductFacts``), never customer data. The key travels in a header,
never in the URL, and never appears in an error, a log line or the archive.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from django.conf import settings

from apps.core.outbound import open_url

from .enrichment import (
    TEXT_FIELDS,
    EnrichmentOutput,
    FakeEnricher,
    ProductFacts,
    ProviderError,
    TextEnricher,
    TextPolicy,
)

PROMPT_VERSION = "gemini-v1"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

Transport = Callable[[str, Mapping[str, Any], Mapping[str, str], float], Any]


def http_post_json(
    url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float
) -> Any:
    """POST ``payload`` as JSON and return the decoded answer. Errors name the failure only.

    TODO(MIG.12): move to ``apps/core/safe_http.py`` once it is on main.
    """
    request = urllib.request.Request(  # noqa: S310 (fixed https endpoint)
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with open_url(request, timeout=timeout) as response:  # never follows a redirect
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"HTTP {exc.code}") from None
    except OSError as exc:
        raise ProviderError(type(exc).__name__) from None
    try:
        return json.loads(body)
    except ValueError:
        raise ProviderError("invalid JSON") from None


def build_prompt(facts: ProductFacts, lang: str, policy: TextPolicy) -> str:
    limits = ", ".join(f"{k} max {v} characters" for k, v in policy.max_lengths.items())
    attributes = "\n".join(f"- {k}: {v}" for k, v in facts.attributes.items())
    return (
        f"Write product texts in language '{lang}' (British English if English) for a shop.\n"
        f"Answer with one JSON object with exactly these keys: {', '.join(TEXT_FIELDS)}.\n"
        f"Limits: {limits}. Plain text only: no HTML, no links.\n"
        "Never make environmental claims and never say which vehicles or models it fits.\n"
        f"Product: {facts.title}\nSKU: {facts.sku}\n{attributes}\n"
        f"Supplier text: {facts.source_text}"
    )


class GeminiEnricher:
    provider = "gemini"
    prompt_version = PROMPT_VERSION

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        transport: Transport = http_post_json,
        timeout: float = 20.0,
    ) -> None:
        if not api_key:
            raise ProviderError("no API key configured")
        self._api_key = api_key
        self.model = model
        self._transport = transport
        self._timeout = timeout

    def describe(self, facts: ProductFacts, lang: str, policy: TextPolicy) -> EnrichmentOutput:
        payload = {
            "contents": [{"parts": [{"text": build_prompt(facts, lang, policy)}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        answer = self._transport(
            ENDPOINT.format(model=self.model),
            payload,
            {"x-goog-api-key": self._api_key},
            self._timeout,
        )
        try:
            text = answer["candidates"][0]["content"]["parts"][0]["text"]
            raw = json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderError("unexpected answer shape") from None
        if not isinstance(raw, dict):
            raise ProviderError("unexpected answer shape")
        return EnrichmentOutput(
            raw=raw, provider=self.provider, model=self.model, prompt_version=self.prompt_version
        )


def get_enricher() -> TextEnricher:
    """The enricher chosen by ``settings.INGEST_ENRICHER`` (default ``fake``)."""
    choice = settings.INGEST_ENRICHER
    if choice == "fake":
        return FakeEnricher()
    if choice == "gemini":
        return GeminiEnricher(
            settings.INGEST_GEMINI_API_KEY,
            settings.INGEST_GEMINI_MODEL,
            transport=http_post_json,
            timeout=settings.INGEST_ENRICH_TIMEOUT,
        )
    raise ProviderError(f"unknown enricher {choice!r}")
