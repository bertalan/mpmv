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

Launch countdown (CMS.9, spec 10 §10.2): the state of the block at a given instant, and the
longest a cache may keep a page whose countdown is still running.

Product grid (CMS.10, spec 10 §10.2): how many products a grid shows.

Blog (CMS.3, spec 10 §10.2): an article's schedule, Wagtail's `go_live_at` and `expire_at`, and
the schema.org data of articles and author pages (Person, ProfilePage, BlogPosting).

Rich text (CMS.12, spec 10 §10.2): the sanitiser every rich-text write outside the editor goes
through. It is the editor's own allow-list (`DbWhitelister` of the Wagtail features), so only
the elements and attributes the editor can write are stored.
"""

import datetime as dt
import json
import math
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from html import escape, unescape
from html.parser import HTMLParser
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

GO_LIVE_LEGAL_KINDS: tuple[str, ...] = (
    "terms",
    "privacy",
    "withdrawal",
    "shipping",
    "returns",
    "reviews",  # OP-28: Omnibus information on how reviews are verified
)
REQUIRED_LEGAL_REVIEWS: tuple[str, ...] = ("native_review", "counsel_check")
# D-27: the platform terms and the ranking page (CMP.5) are go-live pages in every language too.
DSA_LEGAL_KINDS: tuple[str, ...] = ("platform_terms", "ranking")
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


# --- Product grid (CMS.10)

PRODUCT_GRID_DEFAULT = 8
PRODUCT_GRID_MAX = 24  # one CAT.8 listing page


def grid_limit(limit: int | None) -> int:
    """The number of products a grid shows: its limit within 1..PRODUCT_GRID_MAX. The block
    validates the bounds; a value stored before a bound changed is clamped here."""
    if limit is None:
        return PRODUCT_GRID_DEFAULT
    return max(1, min(PRODUCT_GRID_MAX, limit))


# --- Launch countdown (CMS.9)


@dataclass(frozen=True)
class CountdownState:
    """What a countdown shows at one instant: the time left, or the expired state."""

    expired: bool
    days: int = 0
    hours: int = 0
    minutes: int = 0


def _require_aware(*values: dt.datetime) -> None:
    for value in values:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("countdown datetimes must be timezone-aware")


def countdown_state(target: dt.datetime, now: dt.datetime) -> CountdownState:
    """The countdown to `target` at `now`; expired from the target on (exactly at it too).

    The time left rounds up to the whole minute, as the browser module does: the counter
    never reads "0 minutes" while the launch is still ahead. Both values must be aware;
    any time zone works, the comparison is between instants.
    """
    _require_aware(target, now)
    # In UTC: the difference of two datetimes sharing a tzinfo is wall-clock time, which a
    # daylight saving change would skew.
    left = target.astimezone(dt.UTC) - now.astimezone(dt.UTC)
    if left <= dt.timedelta(0):
        return CountdownState(expired=True)
    total = math.ceil(left / dt.timedelta(minutes=1))
    hours, minutes = divmod(total, 60)
    days, hours = divmod(hours, 24)
    return CountdownState(expired=False, days=days, hours=hours, minutes=minutes)


def capped_max_age(
    targets: Iterable[dt.datetime], now: dt.datetime, max_age: int | None
) -> int | None:
    """The `max-age` (seconds) a page may carry so no cache keeps it past its nearest active
    countdown target: `max_age` itself, or the whole seconds left when fewer. The cap only ever
    shortens a lifetime: with no `max_age` (None) the result is None, so a response that was not
    cacheable never becomes so. Targets already reached do not count; with none left, `max_age`
    is returned unchanged."""
    _require_aware(now)
    if max_age is None:
        return None
    now = now.astimezone(dt.UTC)
    ahead = [target.astimezone(dt.UTC) - now for target in targets if target > now]
    if not ahead:
        return max_age
    return min(max_age, math.floor(min(ahead).total_seconds()))


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


_BLOCK_TAGS = frozenset({"p", "li", "h2", "h3", "h4", "blockquote", "div"})


class _TextOf(HTMLParser):
    """Collects the text of HTML, entities decoded; the end of a block separates words."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")


def plain_text(html: str) -> str:
    """Rich text as one line of plain text, for a structured-data description."""
    parser = _TextOf()
    parser.feed(html)
    parser.close()
    return " ".join("".join(parser.parts).split())


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


# --- Term landing pages (CMS.4)

LANDING_CATEGORY_DEFAULT = 4
LANDING_CATEGORY_MAX = 12


def landing_category_limit(limit: int | None) -> int:
    """How many products each category section of a term landing page shows: 0 turns the
    sections off; a stored value out of bounds is clamped."""
    if limit is None:
        return LANDING_CATEGORY_DEFAULT
    return max(0, min(LANDING_CATEGORY_MAX, limit))


def shows_category_sections(categories: int, limit: int) -> bool:
    """Sections by category only for two categories or more: a single one would repeat the
    listing right below it."""
    return limit > 0 and categories >= 2


def listing_slug_path(path: str, prefix: str) -> str | None:
    """The `category` filter value (CAT.8) of a category listing at `path`: its slugs from the
    top of the tree, after the language `prefix` and the listing segment. None without slugs."""
    _segment, _sep, slugs = path.removeprefix(prefix).strip("/").partition("/")
    return slugs or None


#: Attributes a stored `<a linktype=...>` and `<embed>` may keep (the database format).
_DB_LINK_ATTRS = ("id", "linktype")
_DB_EMBED_ATTRS = ("embedtype", "id", "alt", "format")
#: Link and embed types whose handler looks the target up by a numeric `id`: a missing or
#: non-numeric one makes Wagtail's `expand_db_html` raise at render, so such a link is unwrapped.
_ID_LINKTYPES = frozenset({"page", "document"})
_ID_EMBEDTYPES = frozenset({"image"})
_NUMERIC_ID_RE = re.compile(r"[0-9]+")


def image_format_names() -> frozenset[str]:
    """The names of Wagtail's registered image formats: an image embed outside them makes
    `expand_db_html` raise at render."""
    from wagtail.images.formats import get_image_formats

    return frozenset(fmt.name for fmt in get_image_formats())


def _has_valid_id(kind: str, value: Any, id_kinds: frozenset[str]) -> bool:
    """False for a `kind` that needs a numeric id and has none; True for any other kind."""
    return kind not in id_kinds or (
        isinstance(value, str) and _NUMERIC_ID_RE.fullmatch(value) is not None
    )


_TAG_RE = re.compile(r"<[^>]*>?")
EXTERNAL_LINK_REL = "noopener noreferrer"
_EXTERNAL_PREFIXES = ("http://", "https://", "//")


@lru_cache(maxsize=32)
def _whitelister(features: tuple[str, ...]) -> Any:
    """The editor's allow-list for `features`, reading the database format too.

    Wagtail's `DbWhitelister` turns the editor's HTML into the stored form; stored text
    (`<a linktype="page" id="3">`, `<embed embedtype=...>`) is not editor HTML, so those two
    shapes are kept here, reduced to their known attributes, when the feature is granted.
    """
    # Imported here: the admin converters need the app registry, which loads after the models
    # that import this module.
    from bs4.element import NavigableString
    from wagtail.admin.rich_text.converters.editor_html import (
        DbWhitelister,
        EditorHTMLConverter,
    )
    from wagtail.whitelist import check_url

    class StoredTextWhitelister(DbWhitelister):  # type: ignore[misc]
        def clean_string_node(self, doc: Any, node: Any) -> None:
            # Only plain text stays. CDATA, DOCTYPE, declarations and processing instructions
            # are `NavigableString`s too, and a browser reads them as bogus comments that end
            # at the first `>`: what follows is live markup.
            if type(node) is not NavigableString:
                node.extract()

        def clean_tag_node(self, doc: Any, tag: Any) -> None:
            try:
                self._clean_tag_node(doc, tag)
            except (KeyError, AttributeError, TypeError, ValueError):
                # A link or embed missing what its handler needs (`data-id`): not one of ours.
                if tag.name == "embed" or "data-embedtype" in tag.attrs:
                    tag.decompose()
                elif tag.parent is not None:
                    tag.unwrap()

        def _clean_tag_node(self, doc: Any, tag: Any) -> None:
            if tag.name == "a" and "linktype" in tag.attrs and "data-linktype" not in tag.attrs:
                for child in list(tag.contents):
                    self.clean_node(doc, child)
                if tag["linktype"] in self.link_handlers and _has_valid_id(
                    tag["linktype"], tag.get("id"), _ID_LINKTYPES
                ):
                    tag.attrs = {k: v for k, v in tag.attrs.items() if k in _DB_LINK_ATTRS}
                else:
                    tag.unwrap()
                return
            if tag.name == "embed" and "data-embedtype" not in tag.attrs:
                if (
                    tag.get("embedtype") in self.embed_handlers
                    and _has_valid_id(tag["embedtype"], tag.get("id"), _ID_EMBEDTYPES)
                    and (tag["embedtype"] != "image" or tag.get("format") in image_format_names())
                ):
                    tag.attrs = {k: v for k, v in tag.attrs.items() if k in _DB_EMBED_ATTRS}
                else:
                    tag.decompose()
                return
            super().clean_tag_node(doc, tag)
            if (
                tag.name == "a"
                and tag.get("linktype")
                and not _has_valid_id(tag["linktype"], tag.get("id"), _ID_LINKTYPES)
            ):
                # The editor's own format (`data-id`) reaches the same stored shape.
                tag.unwrap()
                return
            href = tag.get("href") if tag.name == "a" else None
            if href and check_url(href) and href.lstrip().lower().startswith(_EXTERNAL_PREFIXES):
                tag["rel"] = EXTERNAL_LINK_REL

    return StoredTextWhitelister(EditorHTMLConverter(list(features)).converter_rules)


def sanitise_rich_text(html: str, features: Sequence[str] | None) -> str:
    """`html` reduced to what the editor with `features` can write (CMS.12).

    The editor's allow-list is the one on save: elements and attributes of the granted features
    stay, anything else (script, style, iframe, event handlers, `javascript:` and `data:` links)
    goes, with the text of a dropped element kept. Stored text is not escaped twice, and the
    result is stable: sanitising it again changes nothing. `features=None` means Wagtail's
    default features, as for a field. The function never raises: a text nested too deeply for
    the parser is reduced to its escaped plain text.
    """
    if not html:
        return html
    if features is None:
        from wagtail.rich_text import features as feature_registry

        features = feature_registry.get_default_features()
    try:
        return str(_whitelister(tuple(features)).clean(html))
    except RecursionError:
        return escape(_TAG_RE.sub("", unescape(html)))
