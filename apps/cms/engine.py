"""Pure CMS rules (no ORM, no I/O).

Galleries (I18N.3, spec 09 §9.2): the replacement rule of D-18. An image is language neutral
unless it is dedicated to one language. A language with at least one dedicated image shows only
those; any other language shows the neutral images. Product galleries (CAT.4) follow the same
rule.

Legal pages (CMS.5, spec 10 §10.2): a legal page keeps one version history per language (Q-42):
each publish that changes the title, the text or the effective date adds a version. A version
applies from its effective date until a later version takes effect; a version published with
the same date as an earlier one is a correction and replaces it from that date.

Go-live readiness (I18N.9, Q-42, spec 09 §9.2): the customer legal and checkout pages (terms of
sale, privacy, withdrawal, shipping, returns) are published in every go-live language, with no
EN fallback, and each language's published text has its native-speaker review and legal counsel
check recorded. An AI draft is recorded when one was used, but is not required.

FAQ block (CMS.2, spec 10 §10.2): the FAQPage structured data.

Featured cards (CMS.2, spec 10 §10.2, spec 03 "URLs"): the URLs of the term and vendor cards.
"""

import datetime as dt
import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

NEUTRAL = ""  # the `language` of an image shown in every language without dedicated images


def gallery_for_language[T: Mapping[str, Any]](items: Iterable[T], language: str) -> list[T]:
    """The items to show in `language`, in their order. Each item has a `language` key."""
    wanted = language.lower()
    items = list(items)
    dedicated = [item for item in items if item["language"] and item["language"].lower() == wanted]
    if dedicated:
        return dedicated
    return [item for item in items if not item["language"]]


# Characters that could end a <script> element or start markup, escaped inside JSON strings.
_JSONLD_ESCAPES = {ord("<"): "\\u003c", ord(">"): "\\u003e", ord("&"): "\\u0026"}


def faq_page_jsonld(questions: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """schema.org FAQPage for (question, answer HTML) pairs, in their order."""
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {"@type": "Answer", "text": answer},
            }
            for question, answer in questions
        ],
    }


def jsonld_text(data: Mapping[str, Any]) -> str:
    """`data` as JSON safe to put inside `<script type="application/ld+json">`."""
    return json.dumps(data, ensure_ascii=False).translate(_JSONLD_ESCAPES)


@dataclass(frozen=True)
class VersionStamp:
    number: int
    effective_date: dt.date


def version_in_force(stamps: Iterable[VersionStamp], today: dt.date) -> VersionStamp | None:
    """The version that applies on `today`: the highest-numbered one already effective."""
    effective = [stamp for stamp in stamps if stamp.effective_date <= today]
    return max(effective, key=lambda stamp: stamp.number, default=None)


def is_backdated(latest: dt.date | None, new: dt.date) -> bool:
    """True when a new version would take effect before the last recorded one."""
    return latest is not None and new < latest


def content_changed(latest: Mapping[str, Any] | None, current: Mapping[str, Any]) -> bool:
    """True when the page's published content differs from its last recorded version."""
    return latest is None or dict(latest) != dict(current)


# --- Effective date of a synced legal translation (I18N.9, Q-42)


def synced_translation_date(*, own: dt.date, source: dt.date | None, text_changed: bool) -> dt.date:
    """The effective date a synced translation publishes with.

    A translation keeps its own date, except when a sync brings it a text newer than that date:
    the source text then takes effect from the source's date, never before (otherwise CMS.5
    would treat the new text as a correction, in force since the translation's old date).
    """
    if text_changed and source is not None and source > own:
        return source
    return own


def translation_date_problem(
    new: dt.date, *, source: dt.date | None, latest: dt.date | None
) -> str | None:
    """Why a translator cannot give a translation `new` as its own date, or None.

    "source": before the date of the source text it translates; "latest": before its own last
    recorded version (a new version never takes effect before the previous one, CMS.5).
    """
    if source is not None and new < source:
        return "source"
    if is_backdated(latest, new):
        return "latest"
    return None


# --- Go-live readiness of the customer legal and checkout pages (I18N.9)

GO_LIVE_LEGAL_KINDS: tuple[str, ...] = ("terms", "privacy", "withdrawal", "shipping", "returns")
REQUIRED_LEGAL_REVIEWS: tuple[str, ...] = ("native_review", "counsel_check")
MISSING_TRANSLATION = "translation"


@dataclass(frozen=True)
class PublishedLegalText:
    """A live legal page in one language: its last recorded version and the reviews of it."""

    kind: str
    language: str
    version: int | None
    reviews: frozenset[str]


@dataclass(frozen=True)
class ReadinessProblem:
    kind: str
    language: str
    missing: str  # MISSING_TRANSLATION or a review stage

    @property
    def message(self) -> str:
        if self.missing == MISSING_TRANSLATION:
            return f"{self.kind} [{self.language}]: no published translation"
        return f"{self.kind} [{self.language}]: no {self.missing} of the published version"


def legal_readiness_problems(
    texts: Iterable[PublishedLegalText],
    kinds: Sequence[str],
    languages: Sequence[str],
    required_reviews: Sequence[str] = REQUIRED_LEGAL_REVIEWS,
) -> list[ReadinessProblem]:
    """What stops go-live, by page then language. Each language stands on its own: a text in
    another language never covers it (no EN fallback, Q-42)."""
    by_page: dict[tuple[str, str], list[PublishedLegalText]] = {}
    for text in texts:
        by_page.setdefault((text.kind, text.language), []).append(text)
    problems: list[ReadinessProblem] = []
    for kind in kinds:
        for language in languages:
            published = by_page.get((kind, language), [])
            if not published:
                problems.append(ReadinessProblem(kind, language, MISSING_TRANSLATION))
                continue
            # Two live pages of one kind in a language: the better reviewed one counts.
            missing = min(
                (
                    [stage for stage in required_reviews if stage not in text.reviews]
                    for text in published
                ),
                key=len,
            )
            problems.extend(ReadinessProblem(kind, language, stage) for stage in missing)
    return problems


# --- Card URLs (spec 03 "URLs"; taxonomy URLs follow D-07, served by CAT.8)


def localized_path(language: str, primary: str, segments: Iterable[str]) -> str:
    """`/a/b/` in the primary language, `/<language>/a/b/` in any other."""
    prefix = "" if language == primary else f"/{language}"
    return f"{prefix}/{'/'.join(segments)}/"


def storefront_path(vendor_slug: str, language: str, primary: str) -> str:
    """A vendor storefront: `/shop/{vendor}/`, the slug the same in every language."""
    return localized_path(language, primary, ["shop", vendor_slug])


def term_path(taxonomy_slug: str, term_slugs: Sequence[str], language: str, primary: str) -> str:
    """A term listing: `/{taxonomy}/{term}/{child}/`, slugs from the root term down."""
    return localized_path(language, primary, [taxonomy_slug, *term_slugs])


def link_language(requested: str, primary: str, available: Sequence[Collection[str]]) -> str | None:
    """The language of a link made of translated segments: `requested` when every segment is
    published in it, else `primary` when every segment is, else None (no link).

    `available` holds, per segment, the languages it is published in. One language for the
    whole URL: a mix of slugs would not resolve.
    """
    for candidate in (requested, primary):
        if all(candidate in languages for languages in available):
            return candidate
    return None
