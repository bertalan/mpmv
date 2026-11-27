"""ING.5: the text enrichment contract (``TextEnricher``) and the types around it.

External AI providers (ING.14) are mocked only through this interface. No I/O, no ORM and no
provider SDK here; ``FakeEnricher`` is the deterministic stand-in for tests and local runs.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# The only language the enricher writes until I18N.8 is decided (D-18, D-18 bis, Q-71).
ENRICH_LANGUAGES = ("en",)

# The text fields of one language, in schema order.
TEXT_FIELDS = ("title", "short_description", "description", "seo_title", "seo_description")


class ProviderError(Exception):
    """A provider failed (network, quota, bad answer). The caller leaves the text unchanged."""


@dataclass(frozen=True, slots=True)
class TextPolicy:
    """What an enriched text must respect. Defaults follow the spec (ING.5, CMP.8, ECGT)."""

    languages: tuple[str, ...] = ENRICH_LANGUAGES
    max_lengths: Mapping[str, int] = field(
        default_factory=lambda: {
            "title": 120,
            "short_description": 300,
            "description": 4000,
            "seo_title": 70,
            "seo_description": 160,
        }
    )
    required: tuple[str, ...] = ("title", "description")
    banned_terms: tuple[str, ...] = ()
    allow_html: bool = False
    allow_urls: bool = False


@dataclass(frozen=True, slots=True)
class ProductFacts:
    """Product data sent to a provider. Product data only, never customer data (ING.14)."""

    sku: str
    title: str
    attributes: Mapping[str, str] = field(default_factory=dict)
    source_text: str = ""


@dataclass(frozen=True, slots=True)
class EnrichedText:
    """One language's texts, as a provider returned them and the schema accepted them."""

    lang: str
    title: str = ""
    short_description: str = ""
    description: str = ""
    seo_title: str = ""
    seo_description: str = ""

    def as_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in TEXT_FIELDS}


@dataclass(frozen=True, slots=True)
class EnrichmentOutput:
    """What a provider call returns: the raw answer plus who produced it (for the archive)."""

    raw: Mapping[str, object]
    provider: str
    model: str
    prompt_version: str


@runtime_checkable
class TextEnricher(Protocol):
    """An interchangeable AI provider (D-13). It returns raw output; schema and policy are ours."""

    provider: str
    model: str
    prompt_version: str

    def describe(self, facts: ProductFacts, lang: str, policy: TextPolicy) -> EnrichmentOutput: ...


class FakeEnricher:
    """Deterministic enricher for tests and local runs. ``overrides`` replaces fields of the
    answer (a missing value drops the key); ``error`` makes every call raise ``ProviderError``."""

    provider = "fake"
    model = "fake-1"
    prompt_version = "v1"

    def __init__(
        self,
        overrides: Mapping[str, object] | None = None,
        *,
        error: str | None = None,
    ) -> None:
        self.overrides = dict(overrides or {})
        self.error = error
        self.calls: list[tuple[ProductFacts, str]] = []

    def describe(self, facts: ProductFacts, lang: str, policy: TextPolicy) -> EnrichmentOutput:
        self.calls.append((facts, lang))
        if self.error is not None:
            raise ProviderError(self.error)
        raw: dict[str, object] = {
            "title": facts.title,
            "short_description": f"{facts.title}, ready to ship.",
            "description": f"{facts.title} (SKU {facts.sku}). {facts.source_text}".strip(),
            "seo_title": facts.title[: policy.max_lengths.get("seo_title", 70)],
            "seo_description": f"Buy {facts.title}."[
                : policy.max_lengths.get("seo_description", 160)
            ],
        }
        raw.update(self.overrides)
        return EnrichmentOutput(
            raw={k: v for k, v in raw.items() if v is not None},
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
        )
