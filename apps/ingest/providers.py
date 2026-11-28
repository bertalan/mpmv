"""ING.14: the Claude and OpenAI adapters behind ``TextEnricher``, mirroring ``GeminiEnricher``.

Only product data goes out (``ProductFacts``). The key travels in a header, never in the URL, and
never appears in an error, a log line, a repr or the archive. Per-vendor keys and provider
resolution come in later slices.
"""

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from .enrichment import EnrichmentOutput, ProductFacts, ProviderError, TextPolicy
from .gemini import (
    MODEL_NAME,
    RETRY_DELAYS,
    RetryableProviderError,
    Transport,
    build_prompt,
    http_post_json,
)

CLAUDE_ENDPOINT = "https://api.anthropic.com/v1/messages"
CLAUDE_VERSION = "2023-06-01"
CLAUDE_MAX_TOKENS = 2000
OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"


class _HttpEnricher:
    provider = ""
    prompt_version = ""
    endpoint = ""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        transport: Transport = http_post_json,
        timeout: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ProviderError("no API key configured")
        if not MODEL_NAME.match(model):
            raise ProviderError("invalid model name")
        self._api_key = api_key
        self.model = model
        self._transport = transport
        self._timeout = timeout
        self._sleep = sleep

    def __repr__(self) -> str:  # never the key
        return f"<{type(self).__name__} model={self.model}>"

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _payload(self, prompt: str) -> dict[str, Any]:
        raise NotImplementedError

    def _extract(self, answer: Any) -> str:
        raise NotImplementedError

    def _call(self, payload: Mapping[str, Any]) -> Any:
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                return self._transport(self.endpoint, payload, self._headers(), self._timeout)
            except RetryableProviderError:
                if attempt == len(RETRY_DELAYS):
                    raise
                self._sleep(RETRY_DELAYS[attempt])

    def describe(self, facts: ProductFacts, lang: str, policy: TextPolicy) -> EnrichmentOutput:
        answer = self._call(self._payload(build_prompt(facts, lang, policy)))
        try:
            raw = json.loads(self._extract(answer))
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise ProviderError("unexpected answer shape") from None
        if not isinstance(raw, dict):
            raise ProviderError("unexpected answer shape")
        return EnrichmentOutput(
            raw=raw, provider=self.provider, model=self.model, prompt_version=self.prompt_version
        )


class ClaudeEnricher(_HttpEnricher):
    provider = "claude"
    prompt_version = "claude-v1"
    endpoint = CLAUDE_ENDPOINT

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key, "anthropic-version": CLAUDE_VERSION}

    def _payload(self, prompt: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": CLAUDE_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }

    def _extract(self, answer: Any) -> str:
        stop = answer["stop_reason"]
        if stop != "end_turn":
            raise ProviderError(f"stop reason {str(stop)[:30]}")
        text = answer["content"][0]["text"]
        if not isinstance(text, str):
            raise TypeError
        return text


class OpenAIEnricher(_HttpEnricher):
    provider = "openai"
    prompt_version = "openai-v1"
    endpoint = OPENAI_ENDPOINT

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _payload(self, prompt: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }

    def _extract(self, answer: Any) -> str:
        choice = answer["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ProviderError(f"finish reason {str(choice.get('finish_reason'))[:30]}")
        message = choice["message"]
        if message.get("refusal"):
            raise ProviderError("refused by the provider")
        text = message["content"]
        if not isinstance(text, str):
            raise TypeError
        return text
