"""Pure CMS rules (no ORM, no I/O).

Galleries (I18N.3, spec 09 §9.2): the replacement rule of D-18. An image is language neutral
unless it is dedicated to one language. A language with at least one dedicated image shows only
those; any other language shows the neutral images. Product galleries (CAT.4) follow the same
rule.

Legal pages (CMS.5, spec 10 §10.2): a legal page keeps one version history per language (Q-42):
each publish that changes the title, the text or the effective date adds a version. A version
applies from its effective date until a later version takes effect; a version published with
the same date as an earlier one is a correction and replaces it from that date.

FAQ block (CMS.2, spec 10 §10.2): the FAQPage structured data.

Featured cards (CMS.2, spec 10 §10.2, spec 03 "URLs"): the URLs of the term and vendor cards.

Product grid (CMS.10, spec 10 §10.2): how many products a grid shows.

Blog (CMS.3, spec 10 §10.2): an article's schedule, Wagtail's `go_live_at` and `expire_at`, and
the schema.org data of articles and author pages (Person, ProfilePage, BlogPosting).
"""

import datetime as dt
import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from html import unescape
from typing import Any

from django.utils.html import strip_tags

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


# --- Product grid (CMS.10)

PRODUCT_GRID_DEFAULT = 8
PRODUCT_GRID_MAX = 24  # one CAT.8 listing page


def grid_limit(limit: int | None) -> int:
    """The number of products a grid shows: its limit within 1..PRODUCT_GRID_MAX. The block
    validates the bounds; a value stored before a bound changed is clamped here."""
    if limit is None:
        return PRODUCT_GRID_DEFAULT
    return max(1, min(PRODUCT_GRID_MAX, limit))


# --- Blog schedule (CMS.3)


EXPIRY_IN_PAST = "expiry_in_past"
EXPIRY_NOT_AFTER_GO_LIVE = "expiry_not_after_go_live"


def schedule_problem(
    go_live_at: dt.datetime | None, expire_at: dt.datetime | None, now: dt.datetime
) -> str | None:
    """Why an article's schedule cannot be saved (one of the codes above), or None. The rules of
    Wagtail's own publishing form, for articles created outside it (`services.import_article`):
    an expiry must be in the future and after the go-live."""
    if expire_at is None:
        return None
    if expire_at <= now:
        return EXPIRY_IN_PAST
    if go_live_at is not None and expire_at <= go_live_at:
        return EXPIRY_NOT_AFTER_GO_LIVE
    return None


# --- Blog structured data (CMS.3, owner answers on PR #156)

SCHEMA_ORG = "https://schema.org"


def plain_text(html: str) -> str:
    """Rich text as one line of plain text, for a structured-data description."""
    return " ".join(unescape(strip_tags(html)).split())


def person_jsonld(
    *,
    person_id: str,
    name: str,
    url: str,
    image: str = "",
    description: str = "",
    same_as: Sequence[str] = (),
    job_title: str = "",
) -> dict[str, Any]:
    """schema.org Person of a blog author. `person_id` is stable (the author page URL plus
    `#person`) so every BlogPosting can point at it; the empty fields are left out."""
    data: dict[str, Any] = {"@type": "Person", "@id": person_id, "name": name, "url": url}
    optional = {
        "image": image,
        "description": description,
        "sameAs": list(same_as),
        "jobTitle": job_title,
    }
    data.update({key: value for key, value in optional.items() if value})
    return data


def profile_page_jsonld(*, url: str, person: Mapping[str, Any], language: str) -> dict[str, Any]:
    """schema.org ProfilePage of an author page: its main entity is the author's Person."""
    return {
        "@context": SCHEMA_ORG,
        "@type": "ProfilePage",
        "url": url,
        "inLanguage": language,
        "mainEntity": dict(person),
    }


def blog_posting_jsonld(
    *,
    url: str,
    headline: str,
    date_published: dt.date,
    language: str,
    author_id: str = "",
    author_name: str = "",
    description: str = "",
    image: str = "",
) -> dict[str, Any]:
    """schema.org BlogPosting of an article; its `author` points at the author's Person `@id`."""
    data: dict[str, Any] = {
        "@context": SCHEMA_ORG,
        "@type": "BlogPosting",
        "headline": headline,
        "datePublished": date_published.isoformat(),
        "url": url,
        "mainEntityOfPage": url,
        "inLanguage": language,
    }
    if author_id:
        data["author"] = {"@type": "Person", "@id": author_id, "name": author_name}
    if description:
        data["description"] = description
    if image:
        data["image"] = image
    return data
