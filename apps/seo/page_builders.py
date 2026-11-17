"""The page graph builders of every public page type (SEO.7); registered in `SeoConfig.ready`.

A builder returns the page's own nodes: its `WebPage` (or subtype), `BreadcrumbList` and the
content nodes. The shared nodes (operator, `WebSite`) come from `page_graph`. A page type that
is not public (cart, checkout, account, Manager) has no builder and renders no graph.
"""

from collections.abc import Sequence
from typing import Any

from django.conf import settings
from django.utils import translation

from . import graph_engine as ge
from . import product_graph as pg
from .page_graph import Node, PageContext, register


def _home_crumb(site: str, language: str) -> pg.Crumb:
    with translation.override(language):
        label = str(translation.gettext("Home"))
    home = f"{site}/" if language == settings.LANGUAGE_CODE else f"{site}/{language}/"
    return pg.Crumb(label, home)


def _breadcrumb(url: str, crumbs: Sequence[pg.Crumb]) -> Node:
    return pg.breadcrumb_node(page_url=url, items=[c for c in crumbs if c.name and c.url])


def _page_crumbs(ctx: PageContext, *, include_self: bool = True) -> list[pg.Crumb]:
    """The Wagtail ancestors below the root, then the page itself."""
    page = ctx.obj
    crumbs = [
        pg.Crumb(str(ancestor.title), ancestor.get_full_url(ctx.request) or "")
        for ancestor in page.get_ancestors().filter(depth__gte=2).live().public()
    ]
    if include_self:
        crumbs.append(pg.Crumb(str(page.title), page.get_full_url(ctx.request) or ""))
    return crumbs


def cms_page_nodes(
    ctx: PageContext,
    kind: str = "WebPage",
    *,
    url: str = "",
    name: str = "",
    crumbs: Sequence[pg.Crumb] | None = None,
    main_entity_id: str = "",
    extra: Sequence[Node] = (),
) -> list[Node]:
    """The `WebPage`, its breadcrumb (none on the home page), the page's FAQ and `extra` nodes."""
    from apps.cms.engine import faq_questions

    page = ctx.obj
    url = url or page.get_full_url(ctx.request) or ""
    if not url:
        return []
    language = page.locale.language_code
    trail = list(crumbs) if crumbs is not None else _page_crumbs(ctx)
    breadcrumb = _breadcrumb(url, trail) if len(trail) > 1 else None
    faq = ge.faq_node(url=url, language=language, questions=faq_questions(page))
    web = ge.webpage_node(
        kind=kind,
        url=url,
        site=ctx.site,
        name=name or str(getattr(page, "seo_title", "") or page.title),
        language=language,
        description=str(getattr(page, "search_description", "") or ""),
        breadcrumb_id=str(breadcrumb["@id"]) if breadcrumb else "",
        main_entity_id=main_entity_id,
        has_part_ids=[str(faq["@id"])] if faq else (),
    )
    return [web, *([breadcrumb] if breadcrumb else []), *([faq] if faq else []), *extra]


def register_all() -> None:
    from apps.catalog.product_pages import ProductPage
    from apps.catalog.services import ListingAnchor
    from apps.cms import models as cms
    from apps.events import models as events

    for model in (
        cms.HomePage,
        cms.StandardPage,
        cms.LegalPage,
        cms.VendorRecruitmentPage,
        cms.TermLandingPage,
        cms.VendorLandingPage,
        events.EventPage,
    ):
        register(model)(_web_page)
    register(events.EventIndexPage)(lambda ctx: cms_page_nodes(ctx, "CollectionPage"))
    register(cms.BlogIndexPage)(_blog_index)
    register(cms.BlogPostPage)(_blog_post)
    register(ProductPage)(_product)
    register(ListingAnchor)(_listing)


def _web_page(ctx: PageContext) -> list[Node]:
    return cms_page_nodes(ctx)


def _blog_index(ctx: PageContext) -> list[Node]:
    blog = ctx.obj
    author = ctx.context.get("author")
    if author is None:
        return cms_page_nodes(ctx, "CollectionPage")
    url = blog.author_url(author, ctx.request)
    person = blog.person(author, ctx.request)
    crumbs = [*_page_crumbs(ctx), pg.Crumb(str(author.name), url)]
    return cms_page_nodes(
        ctx,
        "ProfilePage",
        url=url,
        name=str(author.name),
        crumbs=crumbs,
        main_entity_id=str(person["@id"]),
        extra=[person],
    )


def _blog_post(ctx: PageContext) -> list[Node]:
    post = ctx.obj
    posting = {k: v for k, v in post.blog_posting(ctx.request).items() if k != "@context"}
    url = str(posting["url"])
    posting["@id"] = f"{url}#article"
    posting["isPartOf"] = {"@id": ge.webpage_id(url)}
    posting["publisher"] = {"@id": ge.operator_id(ctx.site)}
    extra: list[Node] = [posting]
    author = posting.get("author")
    if isinstance(author, dict) and author.get("@id"):
        blog = post.get_parent().specific
        extra.append(blog.person(post.author, ctx.request))
        posting["author"] = {"@id": author["@id"]}
    return cms_page_nodes(ctx, main_entity_id=str(posting["@id"]), extra=extra)


def _product(ctx: PageContext) -> list[Node]:
    from . import product_jsonld

    page = ctx.obj
    nodes: list[Node] = list(
        product_jsonld.build_graph(page, site=ctx.site, request=ctx.request)["@graph"]
    )
    url = f"{ctx.site}{page.canonical_path}"
    [entity] = [n for n in nodes if n["@type"] in {"Product", "ProductGroup"}]
    web = ge.webpage_node(
        kind="ItemPage",
        url=url,
        site=ctx.site,
        name=page.title,
        language=page.shown,
        description=str(page.short_description or ""),
        breadcrumb_id=f"{url}#breadcrumb",
        main_entity_id=str(entity["@id"]),
    )
    return [web, *nodes]


def _listing(ctx: PageContext) -> list[Node]:
    from apps.catalog import services as catalog
    from apps.core import translations

    anchor = ctx.obj
    language = ctx.language
    shown = language if language in anchor.published else translations.source_language()
    path = anchor.paths.get(language) or anchor.paths.get(shown)
    if not path:
        return []
    url = f"{ctx.site}{path}"
    scope = anchor.scope
    kind = "ProfilePage" if scope.vendor is not None else "CollectionPage"
    crumbs = [_home_crumb(ctx.site, shown), pg.Crumb(str(anchor.name), url)]
    nodes: list[Node] = []
    if scope.brand is not None:
        nodes = list(
            catalog.brand_graph(scope.brand, shown, site=ctx.site, paths=anchor.paths)["@graph"]
        )
    faq_items: Any = ctx.context.get("editorial_faq") or ()
    faq = ge.faq_node(url=url, language=shown, questions=faq_items)
    breadcrumb = _breadcrumb(url, crumbs)
    web = ge.webpage_node(
        kind=kind,
        url=url,
        site=ctx.site,
        name=str(anchor.name),
        language=shown,
        breadcrumb_id=str(breadcrumb["@id"]),
        has_part_ids=[str(faq["@id"])] if faq else (),
    )
    return [web, breadcrumb, *([faq] if faq else []), *nodes]
