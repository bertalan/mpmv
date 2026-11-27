"""ING.5: pure schema validation and policy checks for enriched texts. No ORM, no I/O."""

import html
import re
import unicodedata
from collections.abc import Mapping

from apps.ingest.enrichment import HARD_MAX_LENGTHS, TEXT_FIELDS, EnrichedText, TextPolicy

# Markup is never allowed (no sanitiser backs it): a tag, a comment, a processing instruction, a
# script URL or a markdown link, also when entity-encoded (``&lt;b&gt;``) or hidden with
# whitespace. The text is unescaped twice before the check.
_MARKUP = re.compile(r"<\s*[/!?a-zA-Z]|javascript\s*:|vbscript\s*:|data\s*:\s*text/html")
_MD_LINK = re.compile(r"!?\[[^\]]*\]\s*\([^)]*\)|^\s*\[[^\]]+\]:\s*\S+", re.MULTILINE)
_URL = re.compile(
    r"(?:https?://|www\.)\S+"  # with a scheme or www
    r"|\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+"  # an e-mail address
    r"|\b(?:[a-z0-9-]+\.)+(?:com|net|org|eu|it|de|fr|es|io|co|uk|info|shop|store|biz|ru|cn)\b",
    re.IGNORECASE,
)
_SINGLE_LINE = ("title", "seo_title", "seo_description")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
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
_PLACEHOLDERS = ("lorem ipsum", "as an ai", "as a language model", "[insert", "tbd", "todo")


def _fold(value: str) -> str:
    """NFKC, case-folded, without invisible format characters (zero-width and the like), so a
    term cannot be hidden with fullwidth letters or a zero-width space."""
    text = unicodedata.normalize("NFKC", value).casefold()
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def _word(term: str) -> re.Pattern[str]:
    """The term as whole words: spaces match any whitespace, and a letter or digit may not
    touch either end ("tbd" is not in "matchbd", "sustainable" is not in "unsustainable")."""
    body = r"\s+".join(re.escape(part) for part in term.split())
    return re.compile(rf"(?<![^\W_]){body}(?![^\W_])")


_ENV_RES = tuple((t, _word(t)) for t in _ENV_CLAIMS)
_FITMENT_RES = tuple((t, _word(t)) for t in _FITMENT_CLAIMS)
_PLACEHOLDER_RES = tuple((t, _word(t)) for t in _PLACEHOLDERS)


def _unescaped(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    for _ in range(2):
        text = html.unescape(text)
    return text


def validate_schema(raw: Mapping[str, object], lang: str) -> tuple[EnrichedText | None, list[str]]:
    """Accept a provider answer only if every key is known and present, every value is a string
    and none is above its hard length ceiling.
    Returns the typed text, or ``None`` with the list of schema errors."""
    errors = [f"unknown field: {key}" for key in raw if key not in TEXT_FIELDS]
    values: dict[str, str] = {}
    for name in TEXT_FIELDS:
        if name not in raw:
            errors.append(f"{name}: missing")
            continue
        value = raw[name]
        if not isinstance(value, str):
            errors.append(f"{name}: expected text, got {type(value).__name__}")
        elif len(value.strip()) > HARD_MAX_LENGTHS[name]:
            errors.append(f"{name}: longer than the hard limit of {HARD_MAX_LENGTHS[name]}")
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
        limit = min(policy.max_lengths.get(name, HARD_MAX_LENGTHS[name]), HARD_MAX_LENGTHS[name])
        if len(value) > limit:
            violations.append(f"{name}: longer than {limit} characters")
        decoded = _unescaped(value)
        if _MARKUP.search(_fold(decoded)) or _MD_LINK.search(decoded):
            violations.append(f"{name}: contains HTML or a markup link")
        if not policy.allow_urls and _URL.search(decoded):
            violations.append(f"{name}: contains a URL")
        if name in _SINGLE_LINE and _CONTROL.search(value):
            violations.append(f"{name}: contains a line break or control character")
        lowered = _fold(decoded)
        for term in policy.banned_terms:
            if _fold(term) in lowered:  # a banned term is a plain substring, by design
                violations.append(f"{name}: banned term {term!r}")
        for term, pattern in _ENV_RES:
            if pattern.search(lowered):
                violations.append(f"{name}: generic environmental claim {term!r}")
        for term, pattern in _FITMENT_RES:
            if pattern.search(lowered):
                violations.append(f"{name}: unverified fitment claim {term!r}")
        for term, pattern in _PLACEHOLDER_RES:
            if pattern.search(lowered):
                violations.append(f"{name}: placeholder or model boilerplate {term!r}")
    return violations


def can_write_language(lang: str, policy: TextPolicy, existing: Mapping[str, object]) -> bool:
    """The enricher writes only an allowed language (EN until I18N.8) and never over a language
    that already has texts, which is how the MIG.3 Italian texts stay untouched (Q-71)."""
    return lang in policy.languages and not existing.get(lang)
