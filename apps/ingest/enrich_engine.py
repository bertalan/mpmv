"""ING.5: pure schema validation and policy checks for enriched texts. No ORM, no I/O."""

import re
from collections.abc import Mapping

from apps.ingest.enrichment import TEXT_FIELDS, EnrichedText, TextPolicy

_HTML = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
# ECGT (directive (EU) 2024/825): generic environmental claims are never generated (CMP.8 note).
_ENV_CLAIMS = (
    "eco-friendly", "eco friendly", "environmentally friendly", "environment friendly",
    "green product", "sustainable", "climate neutral", "carbon neutral", "planet-friendly",
    "earth-friendly", "nature-friendly", "biodegradable", "eco-conscious",
)  # fmt: skip
# A wrong fitment claim makes customers buy parts that do not fit (spec 17): never generated.
_FITMENT_CLAIMS = (
    "fits all", "fits every", "universal fit", "compatible with", "fits the", "suitable for all",
)  # fmt: skip
_PLACEHOLDERS = ("lorem ipsum", "as an ai", "as a language model", "[insert", "tbd")


def validate_schema(raw: Mapping[str, object], lang: str) -> tuple[EnrichedText | None, list[str]]:
    """Accept a provider answer only if every key is known and every value is a string.
    Returns the typed text, or ``None`` with the list of schema errors."""
    errors = [f"unknown field: {key}" for key in raw if key not in TEXT_FIELDS]
    values: dict[str, str] = {}
    for name in TEXT_FIELDS:
        value = raw.get(name, "")
        if not isinstance(value, str):
            errors.append(f"{name}: expected text, got {type(value).__name__}")
        else:
            values[name] = value.strip()
    if errors:
        return None, errors
    return EnrichedText(lang=lang, **values), []


def check_policy(text: EnrichedText, policy: TextPolicy) -> list[str]:
    """The policy violations of a schema-valid text; an empty list means it is in policy."""
    violations: list[str] = []
    if text.lang not in policy.languages:
        violations.append(f"language not allowed: {text.lang}")
    for name in policy.required:
        if not getattr(text, name):
            violations.append(f"{name}: required")
    for name in TEXT_FIELDS:
        value = getattr(text, name)
        limit = policy.max_lengths.get(name)
        if limit is not None and len(value) > limit:
            violations.append(f"{name}: longer than {limit} characters")
        if not policy.allow_html and _HTML.search(value):
            violations.append(f"{name}: contains HTML")
        if not policy.allow_urls and _URL.search(value):
            violations.append(f"{name}: contains a URL")
        lowered = value.lower()
        for term in policy.banned_terms:
            if term.lower() in lowered:
                violations.append(f"{name}: banned term {term!r}")
        for term in _ENV_CLAIMS:
            if term in lowered:
                violations.append(f"{name}: generic environmental claim {term!r}")
        for term in _FITMENT_CLAIMS:
            if term in lowered:
                violations.append(f"{name}: unverified fitment claim {term!r}")
        for term in _PLACEHOLDERS:
            if term in lowered:
                violations.append(f"{name}: placeholder or model boilerplate {term!r}")
    return violations


def can_write_language(lang: str, policy: TextPolicy, existing: Mapping[str, object]) -> bool:
    """The enricher writes only an allowed language (EN until I18N.8) and never over a language
    that already has texts, which is how the MIG.3 Italian texts stay untouched (Q-71)."""
    return lang in policy.languages and not existing.get(lang)
