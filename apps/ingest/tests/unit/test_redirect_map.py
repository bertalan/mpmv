"""MIG.6 part 1: the pure legacy-URL redirect map (one hop, chains collapsed, loops refused)."""

from apps.ingest import redirect_map as rm
from apps.ingest.redirect_map import (
    BlogPostIn,
    BrandIn,
    CategoryIn,
    MapEntry,
    ProductIn,
    RetiredSkuIn,
    TagIn,
)

PRODUCT_A = ProductIn(
    key="a",
    status="publish",
    targets={"en": "/products/widget/", "it": "/it/prodotti/widget/"},
    current_slugs={"en": "widget-en-sku1", "it": "widget-it-sku1"},
    custom_old_slugs={"en": ("widget-old", "widget-en"), "it": ("widget-vecchio",)},
    core_old_slugs={"en": ("widget-core",), "it": ()},
)
# A trashed product keeps a trash-suffixed slug; it has no page, so its URLs are gone.
PRODUCT_TRASH = ProductIn(
    key="t",
    status="trash",
    targets={},
    current_slugs={"en": "gizmo__trashed"},
)
# A draft product that was migrated as a draft still has a target.
PRODUCT_DRAFT = ProductIn(
    key="d",
    status="draft",
    targets={"en": "/products/draft-thing/"},
    current_slugs={"en": "draft-thing-en"},
)
POSTS = (
    BlogPostIn(
        key="p1",
        language="en",
        slug="hello",
        category_slugs=("news", "tips"),
        target="/blog/hello/",
    ),
    BlogPostIn(
        key="p2", language="it", slug="ciao", category_slugs=("novita",), target="/it/blog/ciao/"
    ),
)
TAGS = (
    TagIn(language="en", slug="model-x", target="/models/model-x/"),
    TagIn(language="en", slug="model-x-old", target="/models/model-x/"),
    TagIn(language="it", slug="modello-x-it", target="/it/modelli/modello-x/"),
)
CATEGORIES = (
    CategoryIn(language="en", slug="old-cat", target="/categories/new-cat/"),
    CategoryIn(language="it", slug="vecchia-cat-it", target="/it/categorie/nuova-cat/"),
    CategoryIn(language="en", slug="dropped-cat", target=None),
)
BRANDS = (BrandIn(language="en", slug="acme", target="/brands/acme/"),)
SKUS = (RetiredSkuIn(sku="OLD-123", language="en", target="/products/widget/"),)

# A fixture sitemap: every legacy URL form the map must resolve in one hop.
SITEMAP = {
    "/product/widget-en-sku1/": "/products/widget/",
    "/product/widget-old/": "/products/widget/",
    "/product/widget-en/": "/products/widget/",
    "/product/widget-core/": "/products/widget/",
    "/it/prodotto/widget-it-sku1/": "/it/prodotti/widget/",
    "/it/prodotto/widget-vecchio/": "/it/prodotti/widget/",
    "/product/draft-thing-en/": "/products/draft-thing/",
    "/product/gizmo__trashed/": None,
    "/news/hello/": "/blog/hello/",
    "/tips/hello/": "/blog/hello/",
    "/hello/": "/blog/hello/",
    "/it/novita/ciao/": "/it/blog/ciao/",
    "/it/ciao/": "/it/blog/ciao/",
    "/tag-product/model-x/": "/models/model-x/",
    "/tag-product/model-x-old/": "/models/model-x/",
    "/it/tag-product-it/modello-x-it/": "/it/modelli/modello-x/",
    "/product-category/old-cat/": "/categories/new-cat/",
    "/it/categoria-prodotto/vecchia-cat-it/": "/it/categorie/nuova-cat/",
    "/product-category/dropped-cat/": None,
    "/brand/acme/": "/brands/acme/",
    "/product/old-123/": "/products/widget/",
}


def build(**extra: object) -> rm.RedirectMap:
    args: dict[str, object] = {
        "products": (PRODUCT_A, PRODUCT_TRASH, PRODUCT_DRAFT),
        "blog_posts": POSTS,
        "tags": TAGS,
        "categories": CATEGORIES,
        "brands": BRANDS,
        "retired_skus": SKUS,
    }
    args.update(extra)
    return rm.build_redirect_map(**args)  # type: ignore[arg-type]


def test_every_fixture_sitemap_url_resolves_in_one_hop() -> None:
    result = build()
    assert result.issues == ()
    for source, expected in SITEMAP.items():
        entry = result.resolve(source)
        assert entry is not None, source
        assert entry.target == expected, source
        assert entry.code == (301 if expected else 410), source
    assert rm.sitemap_failures(result, SITEMAP) == []


def test_no_target_is_itself_a_source() -> None:
    result = build()
    sources = {e.source for e in result.entries}
    assert all(e.target not in sources for e in result.entries if e.target)


def test_sitemap_check_reports_unmapped_urls() -> None:
    result = build()
    assert rm.sitemap_failures(result, ["/never-heard-of/"]) == [
        "/never-heard-of/ is not in the redirect map."
    ]


def test_origins_cover_every_legacy_origin() -> None:
    result = build()
    origins = {e.origin for e in result.entries}
    assert origins >= {
        rm.ORIGIN_LEGACY_CURRENT,
        rm.ORIGIN_CUSTOM_OLD_SLUG,
        rm.ORIGIN_CORE_OLD_SLUG,
        rm.ORIGIN_TRASHED,
        rm.ORIGIN_BLOG,
        rm.ORIGIN_MERGED_TAG,
        rm.ORIGIN_CATEGORY,
        rm.ORIGIN_BRAND,
        rm.ORIGIN_RETIRED_SKU,
    }


def test_origins_match_the_redirect_model_choices() -> None:
    from apps.catalog.models import Redirect

    values = {c.value for c in Redirect.Origin}
    assert {
        rm.ORIGIN_RETIRED_SKU,
        rm.ORIGIN_MERGED_TAG,
        rm.ORIGIN_CATEGORY,
    } <= values
    assert rm.REDIRECT_ORIGIN_OF[rm.ORIGIN_BLOG] in values
    assert set(rm.REDIRECT_ORIGIN_OF.values()) <= values | {rm.SLUG_HISTORY}


def test_stripped_trashed_slug_is_mapped_unless_a_live_product_owns_it() -> None:
    trashed = ProductIn(
        key="t2",
        status="trash",
        targets={"en": "/products/t2/"},
        current_slugs={"en": "foo__trashed"},
    )
    result = build(products=(trashed,))
    assert result.resolve("/product/foo/") is not None
    live = ProductIn(
        key="l", status="publish", targets={"en": "/products/l/"}, current_slugs={"en": "foo"}
    )
    result = build(products=(trashed, live))
    entry = result.resolve("/product/foo/")
    assert entry is not None
    assert entry.target == "/products/l/"


def test_chain_collapses_to_one_hop() -> None:
    # The retired SKU points at the old tag URL, which itself moves to the final term.
    tags = (
        TagIn(language="en", slug="a", target="/tag-product/b/"),
        TagIn("en", "b", "/models/c/"),
    )
    result = build(products=(), blog_posts=(), categories=(), brands=(), retired_skus=(), tags=tags)
    first = result.resolve("/tag-product/a/")
    assert first is not None
    assert first.target == "/models/c/"
    assert result.collapsed == ("/tag-product/a/",)
    assert result.issues == ()


def test_long_chain_collapses() -> None:
    tags = (
        TagIn("en", "a", "/tag-product/b/"),
        TagIn("en", "b", "/tag-product/c/"),
        TagIn("en", "c", "/tag-product/d/"),
        TagIn("en", "d", "/models/final/"),
    )
    result = build(products=(), blog_posts=(), categories=(), brands=(), retired_skus=(), tags=tags)
    for slug in "abcd":
        entry = result.resolve(f"/tag-product/{slug}/")
        assert entry is not None
        assert entry.target == "/models/final/"


def test_loop_is_refused_with_a_report() -> None:
    tags = (TagIn("en", "a", "/tag-product/b/"), TagIn("en", "b", "/tag-product/a/"))
    result = build(products=(), blog_posts=(), categories=(), brands=(), retired_skus=(), tags=tags)
    assert result.resolve("/tag-product/a/") is None
    assert result.resolve("/tag-product/b/") is None
    loops = [i for i in result.issues if i.kind == rm.ISSUE_LOOP]
    assert len(loops) == 1
    assert set(loops[0].sources) == {"/tag-product/a/", "/tag-product/b/"}
    assert "loop" in loops[0].message


def test_a_chain_leading_into_a_loop_is_refused_too() -> None:
    tags = (
        TagIn("en", "in", "/tag-product/a/"),
        TagIn("en", "a", "/tag-product/b/"),
        TagIn("en", "b", "/tag-product/a/"),
    )
    result = build(products=(), blog_posts=(), categories=(), brands=(), retired_skus=(), tags=tags)
    assert result.entries == ()
    assert {i.kind for i in result.issues} == {rm.ISSUE_LOOP}


def test_a_self_redirect_is_dropped_and_reported() -> None:
    tags = (TagIn("en", "a", "/tag-product/a/"),)
    result = build(products=(), blog_posts=(), categories=(), brands=(), retired_skus=(), tags=tags)
    assert result.entries == ()
    assert [i.kind for i in result.issues] == [rm.ISSUE_LOOP]


def test_chain_through_a_gone_entry_ends_gone() -> None:
    cats = (CategoryIn("en", "a", "/product-category/b/"), CategoryIn("en", "b", None))
    result = build(products=(), blog_posts=(), tags=(), brands=(), retired_skus=(), categories=cats)
    entry = result.resolve("/product-category/a/")
    assert entry is not None
    assert entry.target is None
    assert entry.code == 410


def test_conflicting_sources_keep_the_stronger_origin_and_report() -> None:
    other = ProductIn(
        key="o",
        status="publish",
        targets={"en": "/products/o/"},
        custom_old_slugs={"en": ("widget",)},
    )
    mine = ProductIn(
        key="m", status="publish", targets={"en": "/products/m/"}, current_slugs={"en": "widget"}
    )
    result = build(
        products=(other, mine), blog_posts=(), tags=(), categories=(), brands=(), retired_skus=()
    )
    entry = result.resolve("/product/widget/")
    assert entry is not None
    assert entry.target == "/products/m/"
    assert [i.kind for i in result.issues] == [rm.ISSUE_CONFLICT]


def test_equal_origin_conflict_is_refused_not_guessed() -> None:
    p1 = ProductIn(
        key="1", status="publish", targets={"en": "/products/1/"}, custom_old_slugs={"en": ("x",)}
    )
    p2 = ProductIn(
        key="2", status="publish", targets={"en": "/products/2/"}, custom_old_slugs={"en": ("x",)}
    )
    result = build(
        products=(p1, p2), blog_posts=(), tags=(), categories=(), brands=(), retired_skus=()
    )
    assert result.resolve("/product/x/") is None
    assert [i.kind for i in result.issues] == [rm.ISSUE_CONFLICT]


def test_duplicate_identical_entries_are_not_conflicts() -> None:
    result = build(
        products=(),
        blog_posts=(),
        categories=(),
        brands=(),
        retired_skus=(),
        tags=(TAGS[0], TAGS[0]),
    )
    assert result.issues == ()
    assert len(result.entries) == 1


def test_paths_are_normalised_and_off_site_targets_refused() -> None:
    tags = (
        TagIn("en", "caf%C3%A9", "/models/cafe"),
        TagIn("en", "bad", "https://elsewhere.example/x/"),
    )
    result = build(products=(), blog_posts=(), categories=(), brands=(), retired_skus=(), tags=tags)
    entry = result.resolve("/tag-product/café/")
    assert entry is not None
    assert entry.target == "/models/cafe/"
    assert [i.kind for i in result.issues] == [rm.ISSUE_INVALID]


def test_entry_helpers_split_slug_history_from_redirect_rows() -> None:
    result = build()
    rows = result.slug_history_rows()
    assert ("a", "en", "widget-old") in rows
    assert ("a", "it", "widget-vecchio") in rows
    assert all(isinstance(e, MapEntry) for e in result.redirect_entries())
    assert not any(e.origin == rm.ORIGIN_BLOG for e in result.slug_history_entries())


def test_the_build_is_deterministic() -> None:
    assert build() == build(products=tuple(reversed((PRODUCT_A, PRODUCT_TRASH, PRODUCT_DRAFT))))


# --- Hardening: real URL names, open redirects, one-hop everywhere, idempotency ---


def _reverse_in(language: str, name: str, **kwargs: str) -> str:
    from django.urls import reverse
    from django.utils import translation

    from apps.catalog import slugs

    segments = {"catalog:product": slugs.PRODUCT_SEGMENTS, "catalog:brand": slugs.BRAND_SEGMENTS}
    with translation.override(language):
        return reverse(name, kwargs={"segment": segments[name][language], **kwargs})


def test_targets_built_with_reverse_resolve_to_the_real_catalog_urls() -> None:
    en = _reverse_in("en", "catalog:product", slug="widget")
    it = _reverse_in("it", "catalog:product", slug="widget")
    brand = _reverse_in("en", "catalog:brand", slug="acme")
    product = ProductIn(
        key="r",
        status="publish",
        targets={"en": en, "it": it},
        current_slugs={"en": "widget-old-en", "it": "widget-old-it"},
    )
    result = build(
        products=(product,),
        blog_posts=(),
        tags=(),
        categories=(),
        retired_skus=(),
        brands=(BrandIn(language="en", slug="old-acme", target=brand),),
    )
    en_entry = result.resolve("/product/widget-old-en/")
    it_entry = result.resolve("/it/prodotto/widget-old-it/")
    brand_entry = result.resolve("/brand/old-acme/")
    assert en_entry is not None
    assert en_entry.target == en
    assert it_entry is not None
    assert it_entry.target == it
    assert brand_entry is not None
    assert brand_entry.target == brand
    assert not result.issues


def test_open_redirect_targets_are_refused() -> None:
    bad = (
        "//evil.example.test/x/",
        "https://evil.example.test/x/",
        "http://evil.example.test/",
        "/\\evil.example.test/",
        "javascript:alert(1)",
        "/ok/\n/evil/",
        "/a/../b/",
    )
    tags = tuple(TagIn("en", f"t{i}", target) for i, target in enumerate(bad))
    result = build(products=(), blog_posts=(), categories=(), brands=(), retired_skus=(), tags=tags)
    assert not result.entries
    assert len(result.issues) == len(bad)
    assert {i.kind for i in result.issues} == {rm.ISSUE_INVALID}


def test_every_redirect_target_is_a_local_path_and_never_a_source() -> None:
    result = build()
    sources = {e.source for e in result.entries}
    for entry in result.entries:
        if entry.target is None:
            continue
        assert entry.target.startswith("/")
        assert not entry.target.startswith("//")
        assert "://" not in entry.target
        assert "\\" not in entry.target
        assert entry.target not in sources
        assert entry.source != entry.target


def test_two_sources_chaining_through_another_source_stay_one_hop() -> None:
    tags = (
        TagIn("en", "a", "/tag-product/b/"),
        TagIn("en", "b", "/models/final/"),
    )
    result = build(products=(), blog_posts=(), categories=(), brands=(), retired_skus=(), tags=tags)
    first = result.resolve("/tag-product/a/")
    assert first is not None
    assert first.target == "/models/final/"
    assert all(e.target not in {x.source for x in result.entries} for e in result.entries)


def test_feeding_the_output_back_changes_nothing() -> None:
    first = build()
    second = build()
    assert first == second
    tags = tuple(
        TagIn("it" if e.source.startswith("/it/") else "en", e.source.rsplit("/", 2)[-2], e.target)
        for e in first.entries
        if e.target and e.origin == rm.ORIGIN_MERGED_TAG
    )
    again = build(products=(), blog_posts=(), categories=(), brands=(), retired_skus=(), tags=tags)
    assert {(e.source, e.target) for e in again.entries} == {
        (e.source, e.target) for e in first.entries if e.origin == rm.ORIGIN_MERGED_TAG
    }
