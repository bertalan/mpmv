"""Deterministic product extraction (ING.4, port of the legacy "step 5"). Pure functions: no ORM,
no Django. The services load the vendor's `MappingTable` rows, build `ExtractionRules` and call
`extract_fields`; the mapping rows hold every vocabulary, nothing vendor specific lives here.

Differences from the legacy script, on purpose:
- vocabularies come from mappings (aliases included), not from JSON files read at import time;
- the same colour or material written two ways counts once (the legacy script returned both words);
- the vendor-specific model regex of the legacy script is not ported (model extraction keeps the
  generic "model / mod." marker rule);
- the SKU uniqueness helper is not ported: the catalogue match key and the DB constraint (CAT.13)
  replace it;
- dimensions (mm), material, colour and country of origin feed the CAT.22 product fields.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from .engine import MappingEntry

# --- Vocabulary


@dataclass(frozen=True)
class Candidate:
    """One thing a text may mention: `ref` is handed back, `words` are the spellings searched."""

    ref: str
    words: tuple[str, ...]


@dataclass(frozen=True)
class ExtractionRules:
    types: tuple[Candidate, ...] = ()
    materials: tuple[Candidate, ...] = ()
    colors: tuple[Candidate, ...] = ()
    exclude_words: frozenset[str] = frozenset()
    countries: Mapping[str, str] = field(default_factory=dict)  # lower-case name -> ISO alpha-2
    max_colors: int = 2


def _lower(text: str | None) -> str:
    return (text or "").lower()


def first_match(text: str | None, candidates: Iterable[Candidate]) -> str | None:
    """The first candidate (in the given order) any of whose words occurs in the text."""
    low = _lower(text)
    if not low:
        return None
    for cand in candidates:
        if any(w.lower() in low for w in cand.words if w):
            return cand.ref
    return None


def all_matches(text: str | None, candidates: Iterable[Candidate]) -> list[str]:
    """Every candidate with a word in the text, in the candidates' order."""
    low = _lower(text)
    return [c.ref for c in candidates if any(w.lower() in low for w in c.words if w)]


def word_matches(
    texts: Sequence[str | None], candidates: Sequence[Candidate], *, limit: int
) -> list[str]:
    """Whole-word matches in the first text that has any, by order of appearance, each
    candidate once, at most `limit`."""
    spelled = {w.lower(): c.ref for c in candidates for w in c.words if w}
    if not spelled:
        return []
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(w) for w in sorted(spelled, key=len, reverse=True)) + r")\b"
    )
    for text in texts:
        found: list[str] = []
        for hit in pattern.findall(_lower(text)):
            ref = spelled[hit]
            if ref not in found:
                found.append(ref)
            if len(found) == limit:
                break
        if found:
            return found
    return []


# --- Text fields

_MODEL_MARKER = re.compile(
    r"(?:modello|model|laterali|mod\.)\s+(.+?)(?:\s+(?:per|in|for)\b|$)", re.IGNORECASE
)
_MODEL_WORD = re.compile(r"[a-zA-Z0-9()\-]+")
_STOP_SECTIONS: Final = ("caratteristiche tecniche", "technical", "sistema di aggancio")
_MEASURE_WORD = re.compile(r"\b(misure|materiali|dimensioni|lt|capacity|materials|dimensions)\b")


def extract_model(title: str | None, exclude_words: frozenset[str]) -> str | None:
    """The model name after a "model" marker: at most five words, cut at the first common word
    once a single word was kept. Upper case."""
    match = _MODEL_MARKER.search((title or "").strip())
    if not match:
        return None
    kept: list[str] = []
    for word in _MODEL_WORD.findall(match.group(1)):
        if word.strip("()").lower() in exclude_words:
            if len(kept) == 1:
                break
            continue
        kept.append(word)
        if len(kept) == 5:
            break
    return " ".join(kept).upper() or None


def _tidy(text: str) -> str:
    return " ".join(re.sub(r"[\-*•]", "", text).split())


def extract_materials_text(description: str | None) -> str | None:
    """The text after a "Materials:" label, up to the first full stop."""
    text = _lower(description)
    match = re.search(r"materia(?:li|ls)?:\s*(.*?)(?:\.|$)", text)
    if not match:
        return None
    value = match.group(1).strip()
    for stop in _STOP_SECTIONS:
        idx = value.find(stop)
        if idx != -1:
            value = value[:idx].strip()
    return _tidy(value) or None


def extract_technical_text(description: str | None) -> str | None:
    """The text after a "Technical features:" label, cut at the next measures or materials word."""
    text = _lower(description)
    match = re.search(
        r"(?:caratteristiche tecniche|technical features):\s*(.*?)(?:\.|\n|;|$)", text
    )
    if not match:
        return None
    value = match.group(1).strip()
    cut = _MEASURE_WORD.search(value)
    if cut:
        value = value[: cut.start()].strip()
    return _tidy(value) or None


def extract_capacity(description: str | None) -> str | None:
    """Litres, one value or "a/b" for two bags; the 'each' wording is tried first."""
    text = _lower(description)
    for pattern in (
        r"(?:capienza|capacity)\s*l\.?\s*(\d+)(?:\s*[/-]\s*(\d+))?\s*(?:cad\.|cadauna|each)",
        r"capienza\s*lt\.?\s*(\d+)(?:\s*[/-]\s*(\d+))?",
        r"lt\.?\s*(\d+)(?:\s*[/-]\s*(\d+))?",
    ):
        match = re.search(pattern, text)
        if match:
            return f"{match.group(1)}/{match.group(2)}" if match.group(2) else match.group(1)
    return None


def extract_load_weight(description: str | None) -> str | None:
    match = re.search(
        r"(?:peso\s*trasportabile|max(?:imum)?\s*load)\s*kg\.?\s*([\d.,]+)", _lower(description)
    )
    return match.group(1).replace(",", ".") if match else None


# --- CAT.22 fields

_UNIT_MM: Final = {"mm": Decimal(1), "cm": Decimal(10), "m": Decimal(1000)}
_NUM = r"(\d+(?:[.,]\d+)?)"
_SEP = r"\s*(?:x|\u00d7|\*)\s*"
# A match never starts inside a run of digits: the run's first digit has already tried every way
# to match, and trying each later digit again made a long run quadratic.
_DIMS = re.compile(rf"(?<!\d){_NUM}{_SEP}{_NUM}(?:{_SEP}{_NUM})?\s*(mm|cm|m)\b", re.IGNORECASE)


def _to_mm(value: str, unit: str) -> int | None:
    mm = (Decimal(value.replace(",", ".")) * _UNIT_MM[unit.lower()]).quantize(
        Decimal(1), rounding=ROUND_HALF_UP
    )
    return int(mm) if mm > 0 else None  # CAT.22: a dimension is positive


def extract_dimensions_mm(text: str | None) -> dict[str, int]:
    """`A x B x C unit` (unit mm, cm or m, comma or dot decimals) as length, width, height in mm.
    Two numbers give length and width. A zero side, or no unit, gives nothing."""
    match = _DIMS.search(text or "")
    if not match:
        return {}
    *numbers, unit = match.groups()
    sides = [_to_mm(n, unit) for n in numbers if n is not None]
    if any(s is None for s in sides):
        return {}
    return {
        key: side
        for key, side in zip(("length_mm", "width_mm", "height_mm"), sides, strict=False)
        if side is not None
    }


_ORIGIN = re.compile(
    r"(?:country of origin|made in|origin|paese di origine|fabbricato in|prodotto in)"
    r"\s*[:\-]?\s*([A-Za-z][A-Za-z .'-]*)",
    re.IGNORECASE,
)


def extract_country_of_origin(text: str | None, countries: Mapping[str, str]) -> str | None:
    """An ISO 3166-1 alpha-2 code: an upper-case code right after an origin label, or a country
    name from `countries`. An unknown name gives nothing (never a guess)."""
    for match in _ORIGIN.finditer(text or ""):
        raw = match.group(1).strip(" .")
        first = raw.split()[0] if raw else ""
        if re.fullmatch(r"[A-Z]{2}", first):
            return first
        low = raw.lower()
        for name in sorted(countries, key=len, reverse=True):
            if low.startswith(name):
                return countries[name]
    return None


# --- Rules from mappings

STOP_WORDS: Final = frozenset({"per", "in", "for", "con", "with", "and", "e"})


def build_rules(entries: Sequence[MappingEntry]) -> ExtractionRules:
    """The extraction rules a vendor's mapping rows stand for. Order of `entries` is the order
    candidates are tried in; `ref` of each candidate is the entry's `ref`, as text."""

    def candidates(kind: str) -> tuple[Candidate, ...]:
        return tuple(Candidate(str(e.ref), (e.key, *e.aliases)) for e in entries if e.kind == kind)

    colors = candidates("color")
    countries = {
        w.lower(): e.target_value.upper()
        for e in entries
        if e.kind == "country" and e.target_value
        for w in (e.key, *e.aliases)
    }
    return ExtractionRules(
        types=candidates("product_type"),
        materials=candidates("material"),
        colors=colors,
        exclude_words=STOP_WORDS | {w.lower() for c in colors for w in c.words},
        countries=countries,
    )


# --- Whole extraction


def extract_fields(
    title: str | None, description: str | None, rules: ExtractionRules
) -> dict[str, Any]:
    """Every field the text yields; a field with no value is absent. Values that stand for
    mappings are the candidates' `ref`s: the caller turns them into catalogue targets."""
    both = f"{title or ''}\n{description or ''}"
    out: dict[str, Any] = {}
    product_type = first_match(title, rules.types)
    if product_type is not None:
        out["product_type"] = product_type
    model = extract_model(title, rules.exclude_words)
    if model:
        out["model"] = model
    materials_text = extract_materials_text(description)
    if materials_text:
        out["materials_text"] = materials_text
        materials = all_matches(materials_text, rules.materials)
        if materials:
            out["materials"] = materials
    technical = extract_technical_text(description)
    if technical:
        out["technical"] = technical
    for key, value in (
        ("capacity_l", extract_capacity(description)),
        ("load_weight_kg", extract_load_weight(description)),
    ):
        if value:
            out[key] = value
    colors = word_matches([title, description], rules.colors, limit=rules.max_colors)
    if colors:
        out["colors"] = colors
    out.update(extract_dimensions_mm(both))
    country = extract_country_of_origin(both, rules.countries)
    if country:
        out["country_of_origin"] = country
    return out
