"""MIG.6 part 1: the pure legacy-URL redirect map. No ORM, no legacy access, no Django imports.

Plain data in (what the MIG.1 extractor reads from the legacy system), one merged map out:
every legacy URL form maps to its final MPMV path in a single hop (spec 13, MIG.6 and MIG.11).

Sources merged here: the current legacy product slug, the custom old-slug meta, the core
old-slug meta, trash-suffixed slugs of draft and trashed products, both blog forms (with and
without the category segment), merged model tags (EN and IT), old model categories, the old
brand base and retired SKUs. Chains collapse to one hop; loops, self-redirects, off-site targets
and ambiguous sources are refused and reported, never guessed.

Not in part 1: query-string forms (`?p=`, `?product=`, `?add-to-cart=`), variant URLs and SKU
permalinks (MIG.6 part 2), and the loader that writes `SlugHistory` and `Redirect` rows.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import unquote_to_bytes, urlsplit

# --- Origins of a map entry. The `Redirect.Origin` values are reused where one exists
# (apps.catalog.models); `REDIRECT_ORIGIN_OF` says how each origin is stored.
ORIGIN_LEGACY_CURRENT = "legacy_current"
ORIGIN_CUSTOM_OLD_SLUG = "custom_old_slug"
ORIGIN_CORE_OLD_SLUG = "core_old_slug"
ORIGIN_TRASHED = "trashed"
ORIGIN_BLOG = "blog"
ORIGIN_BRAND = "brand"
ORIGIN_RETIRED_SKU = "retired_sku"
ORIGIN_MERGED_TAG = "merged_tag"
ORIGIN_CATEGORY = "discontinued_category"

SLUG_HISTORY = "slug_history"
# Old product slugs are SlugHistory rows (they follow the product's current URL); everything
# else is a `Redirect` row with this origin.
REDIRECT_ORIGIN_OF: Mapping[str, str] = {
    ORIGIN_LEGACY_CURRENT: "legacy_rule",
    ORIGIN_CUSTOM_OLD_SLUG: SLUG_HISTORY,
    ORIGIN_CORE_OLD_SLUG: SLUG_HISTORY,
    ORIGIN_TRASHED: "legacy_rule",
    ORIGIN_BLOG: "legacy_rule",
    ORIGIN_BRAND: "legacy_rule",
    ORIGIN_RETIRED_SKU: ORIGIN_RETIRED_SKU,
    ORIGIN_MERGED_TAG: ORIGIN_MERGED_TAG,
    ORIGIN_CATEGORY: ORIGIN_CATEGORY,
}

# When two sources claim the same legacy URL, the lower number wins. A product that is not
# published always loses against a published one.
_PRIORITY = {
    ORIGIN_LEGACY_CURRENT: 0,
    ORIGIN_CUSTOM_OLD_SLUG: 1,
    ORIGIN_CORE_OLD_SLUG: 2,
    ORIGIN_RETIRED_SKU: 3,
    ORIGIN_TRASHED: 4,
    ORIGIN_BLOG: 0,
    ORIGIN_MERGED_TAG: 0,
    ORIGIN_CATEGORY: 0,
    ORIGIN_BRAND: 0,
}
_UNPUBLISHED_PENALTY = 10
TRASH_SUFFIX = "__trashed"

ISSUE_LOOP = "loop"
ISSUE_CONFLICT = "conflict"
ISSUE_INVALID = "invalid"

MOVED = 301
GONE = 410
PUBLISHED = "publish"


@dataclass(frozen=True)
class LegacyUrls:
    """The legacy URL bases per language (a path without slashes at both ends, so the language
    prefix is part of it). The defaults are the ones of the legacy data map (spec 13 §13.2)."""

    product: Mapping[str, str] = field(
        default_factory=lambda: {"en": "product", "it": "it/prodotto"}
    )
    tag: Mapping[str, str] = field(
        default_factory=lambda: {"en": "tag-product", "it": "it/tag-product-it"}
    )
    category: Mapping[str, str] = field(
        default_factory=lambda: {"en": "product-category", "it": "it/categoria-prodotto"}
    )
    brand: Mapping[str, str] = field(default_factory=lambda: {"en": "brand", "it": "it/brand"})
    blog: Mapping[str, str] = field(default_factory=lambda: {"en": "", "it": "it"})


DEFAULT_URLS = LegacyUrls()


@dataclass(frozen=True)
class ProductIn:
    """One legacy product (one `trid` group), with its final path per language.

    `targets` is empty (or lacks a language) when the product has no page there: its URLs are
    then gone (410). `status` is the legacy post status (`publish`, `draft`, `trash`, ...).
    """

    key: str
    status: str
    targets: Mapping[str, str]
    current_slugs: Mapping[str, str] = field(default_factory=dict)
    custom_old_slugs: Mapping[str, Sequence[str]] = field(default_factory=dict)
    core_old_slugs: Mapping[str, Sequence[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class BlogPostIn:
    key: str
    language: str
    slug: str
    category_slugs: Sequence[str]
    target: str


@dataclass(frozen=True)
class TagIn:
    """A legacy model tag (merged or not) and the term page it now lives on. `slug` is the
    legacy slug as stored (an IT tag slug already ends with `-it`)."""

    language: str
    slug: str
    target: str | None


@dataclass(frozen=True)
class CategoryIn:
    """An old model category; `target` None means discontinued (410)."""

    language: str
    slug: str
    target: str | None


@dataclass(frozen=True)
class BrandIn:
    language: str
    slug: str
    target: str | None


@dataclass(frozen=True)
class RetiredSkuIn:
    sku: str
    language: str
    target: str | None


@dataclass(frozen=True)
class MapEntry:
    source: str
    target: str | None  # None: gone
    code: int
    origin: str
    language: str
    key: str = ""  # the product or post key, when the entry belongs to one
    slug: str = ""  # the legacy slug, for SlugHistory rows


@dataclass(frozen=True)
class MapIssue:
    kind: str
    sources: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class RedirectMap:
    entries: tuple[MapEntry, ...]
    issues: tuple[MapIssue, ...] = ()
    collapsed: tuple[str, ...] = ()  # sources whose chain was cut to one hop
    _by_source: dict[str, MapEntry] = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_source", {e.source: e for e in self.entries})

    def resolve(self, path: str) -> MapEntry | None:
        return self._by_source.get(normalise_path(path))

    def slug_history_entries(self) -> tuple[MapEntry, ...]:
        return tuple(e for e in self.entries if REDIRECT_ORIGIN_OF[e.origin] == SLUG_HISTORY)

    def redirect_entries(self) -> tuple[MapEntry, ...]:
        return tuple(e for e in self.entries if REDIRECT_ORIGIN_OF[e.origin] != SLUG_HISTORY)

    def slug_history_rows(self) -> tuple[tuple[str, str, str], ...]:
        """(product key, language, old slug) for the SlugHistory loader."""
        return tuple((e.key, e.language, e.slug) for e in self.slug_history_entries())


# --- Paths


def normalise_path(path: str) -> str:
    """A path as Django hands it over: percent-decoded once, with one trailing slash."""
    try:
        text = unquote_to_bytes(path).decode("utf-8")
    except UnicodeDecodeError:
        text = path
    if not text.startswith("/"):
        text = f"/{text}"
    return text if text.endswith("/") else f"{text}/"


def _path_error(path: str) -> str | None:
    if any(c.isspace() or ord(c) < 32 for c in path):
        return "contains spaces or control characters"
    if "\\" in path or path.startswith("//"):
        return "is not a path on this site"
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or not path.startswith("/"):
        return "is not a path on this site"
    if "?" in path or "#" in path:
        return "has a query or fragment"
    if any(s in (".", "..") for s in parts.path.split("/")):
        return "has dot segments"
    return None


def _join(base: str, *segments: str) -> str:
    parts = [p.strip("/") for p in (base, *segments) if p.strip("/")]
    return "/" + "/".join(parts) + "/"


def _sku_slug(sku: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", sku.casefold()).strip("-")


# --- Building


def _entries(
    *,
    products: Iterable[ProductIn],
    blog_posts: Iterable[BlogPostIn],
    tags: Iterable[TagIn],
    categories: Iterable[CategoryIn],
    brands: Iterable[BrandIn],
    retired_skus: Iterable[RetiredSkuIn],
    urls: LegacyUrls,
) -> tuple[list[tuple[int, MapEntry]], list[MapIssue]]:
    """Every raw (priority, entry), before conflicts and chains are settled."""
    raw: list[tuple[int, MapEntry]] = []
    issues: list[MapIssue] = []

    def add(
        priority: int,
        source: str,
        target: str | None,
        origin: str,
        language: str,
        key: str = "",
        slug: str = "",
    ) -> None:
        source = normalise_path(source)
        error = _path_error(source)
        if target is not None:
            target = normalise_path(target) if target.startswith("/") else target
            target_error = _path_error(target)
            if error is None and target_error:
                error = f"target {target!r} {target_error}"
        if error:
            issues.append(MapIssue(ISSUE_INVALID, (source,), f"{source}: {error}."))
            return
        raw.append(
            (
                priority,
                MapEntry(source, target, MOVED if target else GONE, origin, language, key, slug),
            )
        )

    def base(kind: Mapping[str, str], language: str) -> str:
        return kind.get(language, language if language != "en" else "")

    for product in products:
        penalty = 0 if product.status == PUBLISHED else _UNPUBLISHED_PENALTY
        languages = (
            set(product.current_slugs) | set(product.custom_old_slugs) | set(product.core_old_slugs)
        )
        for lang in sorted(languages):
            target = product.targets.get(lang)
            prefix = base(urls.product, lang)

            def add_slug(
                slug: str,
                origin: str,
                *,
                _lang: str = lang,
                _target: str | None = target,
                _prefix: str = prefix,
                _product: ProductIn = product,
                _penalty: int = penalty,
            ) -> None:
                add(
                    _PRIORITY[origin] + _penalty,
                    _join(_prefix, slug),
                    _target,
                    origin,
                    _lang,
                    _product.key,
                    slug,
                )

            current = product.current_slugs.get(lang)
            if current:
                if current.endswith(TRASH_SUFFIX):
                    add_slug(current, ORIGIN_TRASHED)
                    # The name the post had before it was trashed, if nobody else owns it.
                    add(
                        _PRIORITY[ORIGIN_TRASHED] + penalty + _UNPUBLISHED_PENALTY,
                        _join(prefix, current.removesuffix(TRASH_SUFFIX)),
                        target,
                        ORIGIN_TRASHED,
                        lang,
                        product.key,
                        current.removesuffix(TRASH_SUFFIX),
                    )
                else:
                    add_slug(current, ORIGIN_LEGACY_CURRENT)
            for slug in product.custom_old_slugs.get(lang, ()):
                add_slug(slug, ORIGIN_CUSTOM_OLD_SLUG)
            for slug in product.core_old_slugs.get(lang, ()):
                add_slug(slug, ORIGIN_CORE_OLD_SLUG)

    for post in blog_posts:
        blog = base(urls.blog, post.language)
        add(0, _join(blog, post.slug), post.target, ORIGIN_BLOG, post.language, post.key, post.slug)
        for category in post.category_slugs:
            add(
                0,
                _join(blog, category, post.slug),
                post.target,
                ORIGIN_BLOG,
                post.language,
                post.key,
                post.slug,
            )
    for tag in tags:
        add(
            0,
            _join(base(urls.tag, tag.language), tag.slug),
            tag.target,
            ORIGIN_MERGED_TAG,
            tag.language,
            slug=tag.slug,
        )
    for category_in in categories:
        add(
            0,
            _join(base(urls.category, category_in.language), category_in.slug),
            category_in.target,
            ORIGIN_CATEGORY,
            category_in.language,
            slug=category_in.slug,
        )
    for brand in brands:
        add(
            0,
            _join(base(urls.brand, brand.language), brand.slug),
            brand.target,
            ORIGIN_BRAND,
            brand.language,
            slug=brand.slug,
        )
    for sku in retired_skus:
        slug = _sku_slug(sku.sku)
        if slug:
            add(
                _PRIORITY[ORIGIN_RETIRED_SKU],
                _join(base(urls.product, sku.language), slug),
                sku.target,
                ORIGIN_RETIRED_SKU,
                sku.language,
                slug=slug,
            )
    return raw, issues


def _settle_conflicts(
    raw: list[tuple[int, MapEntry]],
) -> tuple[dict[str, MapEntry], list[MapIssue]]:
    """One entry per source: the stronger origin wins, an equal claim on two targets is
    refused, an identical repeat is not a conflict."""
    by_source: dict[str, list[tuple[int, MapEntry]]] = {}
    for priority, entry in raw:
        by_source.setdefault(entry.source, []).append((priority, entry))
    chosen: dict[str, MapEntry] = {}
    issues: list[MapIssue] = []
    for source in sorted(by_source):
        claims = sorted(by_source[source], key=lambda c: (c[0], c[1].key, c[1].target or ""))
        best = claims[0][0]
        top = [e for p, e in claims if p == best]
        targets = {e.target for e in top}
        others = {e.target for p, e in claims if p != best} - targets
        if len(targets) > 1:
            issues.append(
                MapIssue(
                    ISSUE_CONFLICT,
                    (source,),
                    f"{source} is claimed by {len(targets)} different targets at the same "
                    "strength; it is left out until one is chosen.",
                )
            )
            continue
        if others:
            issues.append(
                MapIssue(
                    ISSUE_CONFLICT,
                    (source,),
                    f"{source} is claimed by more than one target; the stronger origin "
                    f"({top[0].origin}) wins.",
                )
            )
        chosen[source] = top[0]
    return chosen, issues


def _collapse(
    chosen: dict[str, MapEntry],
) -> tuple[dict[str, MapEntry], list[MapIssue], list[str]]:
    """Cut every chain to one hop; refuse the sources that loop or lead into a loop."""
    final: dict[str, MapEntry] = {}
    issues: list[MapIssue] = []
    collapsed: list[str] = []
    refused: dict[str, tuple[str, ...]] = {}
    for source in sorted(chosen):
        entry = chosen[source]
        path = [source]
        current = entry
        looped = False
        while current.target is not None and current.target in chosen:
            if current.target in path:
                looped = True
                break
            path.append(current.target)
            current = chosen[current.target]
        if looped:
            refused[source] = tuple(path)
            continue
        if len(path) > 1:
            collapsed.append(source)
        final[source] = MapEntry(
            source,
            current.target,
            current.code,
            entry.origin,
            entry.language,
            entry.key,
            entry.slug,
        )
    # One issue per loop (its cycle plus anything leading into it).
    reported: set[frozenset[str]] = set()
    for source in sorted(refused):
        trail = refused[source]
        cycle = trail[trail.index(chosen[trail[-1]].target or "") :]
        members = frozenset(cycle)
        group = tuple(sorted(s for s, p in refused.items() if members & set(p)))
        if frozenset(group) in reported:
            continue
        reported.add(frozenset(group))
        issues.append(
            MapIssue(
                ISSUE_LOOP,
                group,
                "Redirect loop (refused): "
                + " -> ".join((*cycle, cycle[0]))
                + (
                    ""
                    if len(group) == len(cycle)
                    else f"; also leading into it: {', '.join(sorted(set(group) - members))}"
                )
                + ".",
            )
        )
    return final, issues, collapsed


def build_redirect_map(
    *,
    products: Iterable[ProductIn] = (),
    blog_posts: Iterable[BlogPostIn] = (),
    tags: Iterable[TagIn] = (),
    categories: Iterable[CategoryIn] = (),
    brands: Iterable[BrandIn] = (),
    retired_skus: Iterable[RetiredSkuIn] = (),
    urls: LegacyUrls = DEFAULT_URLS,
) -> RedirectMap:
    """Merge every legacy origin into one map where each source reaches its final page (or a
    410) in one hop. Deterministic: the order of the inputs does not matter."""
    raw, issues = _entries(
        products=products,
        blog_posts=blog_posts,
        tags=tags,
        categories=categories,
        brands=brands,
        retired_skus=retired_skus,
        urls=urls,
    )
    chosen, conflicts = _settle_conflicts(raw)
    # A redirect to itself is a loop of one.
    selfs = {s: e for s, e in chosen.items() if e.target == s}
    issues.extend(
        MapIssue(ISSUE_LOOP, (s,), f"{s} redirects to itself (a loop); it is left out.")
        for s in sorted(selfs)
    )
    chosen = {s: e for s, e in chosen.items() if s not in selfs}
    final, loops, collapsed = _collapse(chosen)
    entries = tuple(final[s] for s in sorted(final))
    return RedirectMap(entries, tuple([*issues, *conflicts, *loops]), tuple(collapsed))


def sitemap_failures(result: RedirectMap, urls: Iterable[str]) -> list[str]:
    """Legacy URLs (a sitemap, an access-log list) the map does not resolve in one hop."""
    failures: list[str] = []
    for url in urls:
        entry = result.resolve(url)
        if entry is None:
            failures.append(f"{url} is not in the redirect map.")
        elif entry.target is not None and result.resolve(entry.target) is not None:
            failures.append(f"{url} leads to {entry.target}, which redirects again.")
    return failures
