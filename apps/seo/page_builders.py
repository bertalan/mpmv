"""The page graph builders of every public page type (SEO.7); registered in `SeoConfig.ready`.

A builder returns the page's own nodes: its `WebPage` (or subtype), `BreadcrumbList` and the
content nodes. The shared nodes (operator, `WebSite`) come from `page_graph`. A page type that
is not public (cart, checkout, account, Manager) has no builder and renders no graph.
"""

from collections.abc import Iterable, Sequence
from typing import Any
from urllib.parse import quote

from django.conf import settings
from django.utils import translation

from apps.vendors.models import Vendor

from . import graph_engine as ge
from . import product_graph as pg
from . import seller as seller_name
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


def _is_in_language(path: str, language: str) -> bool:
    """Whether a site path is one of `language`'s URLs (the prefix of a language that is not
    the primary one): a list never carries a page of another language (no fallback)."""
    default = settings.LANGUAGE_CODE
    if not path.startswith("/"):
        return True  # an absolute URL (CMS pages carry their own language)
    others = [f"/{code}/" for code, _name in settings.LANGUAGES if code != default]
    if language == default:
        return not any(path.startswith(prefix) for prefix in others)
    return path.startswith(f"/{language}/")


def _absolute(site: str, path: str) -> str:
    return path if path.startswith("http") else f"{site}{path}"


def product_entries(ctx: PageContext, cards: Iterable[Any]) -> list[ge.ListEntry]:
    """The listed product cards that link to a page in the page language."""
    return [
        ge.ListEntry(
            url=_absolute(ctx.site, card.url),
            name=str(card.title),
            kind="Product",
            image=_absolute(ctx.site, card.image_url) if card.image_url else "",
        )
        for card in cards
        if card.url and _is_in_language(card.url, ctx.language)
    ]


def _items(ctx: PageContext, url: str, name: str, entries: Iterable[ge.ListEntry]) -> Node:
    return ge.item_list_node(
        list_id=f"{url}#items", name=name, language=ctx.language, entries=entries
    )


def cms_page_nodes(
    ctx: PageContext,
    kind: str = "WebPage",
    *,
    url: str = "",
    name: str = "",
    crumbs: Sequence[pg.Crumb] | None = None,
    main_entity_id: str = "",
    extra: Sequence[Node] = (),
    items: Node | None = None,
) -> list[Node]:
    """The `WebPage`, its breadcrumb (none on the home page), the page's FAQ, the named lists of
    its widgets and `extra` nodes. `items` is the page's own main `ItemList` (a collection
    page's `mainEntity`)."""
    from apps.cms.blocks import page_graph_nodes
    from apps.cms.engine import faq_questions

    page = ctx.obj
    url = url or page.get_full_url(ctx.request) or ""
    if not url:
        return []
    language = page.locale.language_code
    trail = list(crumbs) if crumbs is not None else _page_crumbs(ctx)
    breadcrumb = _breadcrumb(url, trail) if len(trail) > 1 else None
    faq = ge.faq_node(url=url, language=language, questions=faq_questions(page))
    body = getattr(page, "body", None)
    widgets: list[Node] = []
    for number, widget in enumerate(page_graph_nodes(body, ctx.context) if body else [], 1):
        widgets.append({**widget, "@id": f"{url}#list-{number}", "inLanguage": language})
    parts = [str(node["@id"]) for node in (faq, *widgets) if node]
    if items and main_entity_id:
        parts.append(str(items["@id"]))
    elif items:
        main_entity_id = str(items["@id"])
    web = ge.webpage_node(
        kind=kind,
        url=url,
        site=ctx.site,
        name=name or str(getattr(page, "seo_title", "") or page.title),
        language=language,
        description=str(getattr(page, "search_description", "") or ""),
        breadcrumb_id=str(breadcrumb["@id"]) if breadcrumb else "",
        main_entity_id=main_entity_id,
        has_part_ids=parts,
    )
    nodes = [web, *([breadcrumb] if breadcrumb else []), *([faq] if faq else []), *widgets]
    return [*nodes, *([items] if items else []), *extra]


def register_all() -> None:
    from apps.catalog.product_pages import ProductPage
    from apps.catalog.search_page import SearchGraphPage
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
        cms.FormPage,
    ):
        register(model)(_web_page)
    register(events.EventPage)(_event_page)
    register(events.EventIndexPage)(_event_index)
    register(cms.BlogIndexPage)(_blog_index)
    register(cms.BlogPostPage)(_blog_post)
    register(ProductPage)(_product)
    register(ListingAnchor)(_listing)
    register(SearchGraphPage)(_search)


def _web_page(ctx: PageContext) -> list[Node]:
    return cms_page_nodes(ctx)


def _post_items(ctx: PageContext, url: str, name: str) -> Node:
    """The posts of the shown page of the blog (or author) list."""
    posts = ctx.context.get("posts") or ()
    entries = [
        ge.ListEntry(
            url=post.get_full_url(ctx.request) or "", name=str(post.title), kind="BlogPosting"
        )
        for post in posts
    ]
    return _items(ctx, url, name, entries)


def _event_organizer(ctx: PageContext, page: Any) -> dict[str, Any] | None:
    """The vendor as an `Organization` by its public seller name (SEO.15), omitted when it has
    none; an event of the platform references the operator node."""
    from apps.core.services import NoPlatformOperator, current_operator

    vendor = page.vendor
    if vendor is not None:
        name = seller_name.public_name(vendor)
        return {"@type": "Organization", "name": name} if name else None
    try:
        current_operator()
    except NoPlatformOperator:
        return None
    return {"@id": ge.operator_id(ctx.site)}


def _event_image(ctx: PageContext, page: Any) -> str:
    from apps.cms.models import ARTICLE_IMAGE_FILTER
    from apps.core.images import rendition_or_original

    image = page.listing_image
    if image is None or not image.file:
        return ""
    return _absolute(ctx.site, rendition_or_original(image, ARTICLE_IMAGE_FILTER)[0])


def _event_node(ctx: PageContext, url: str) -> Node:
    import datetime as dt

    from django.utils import timezone

    from apps.events import engine as events_engine

    from . import event_graph

    page = ctx.obj
    if not page.is_publicly_visible() or page.event_type == page.EventType.PROMOTION:
        return {}
    start: Any = page.starts_at if not page.all_day else page.start_date
    end: Any = page.ends_at if not page.all_day else page.end_date
    if page.rrule.strip():
        now = timezone.now()
        found = events_engine.expand(page.recurrence_spec(), now, now + dt.timedelta(days=366), 1)
        start, end = event_graph.occurrence_bounds(found, (start, end))
    if start is None or end is None:
        return {}
    if isinstance(start, dt.datetime):
        start, end = start.astimezone(page.zone()), end.astimezone(page.zone())
    latitude = float(page.latitude) if page.latitude is not None else None
    longitude = float(page.longitude) if page.longitude is not None else None
    return event_graph.event_node(
        event_graph.EventFacts(
            url=url,
            name=str(page.title),
            description=str(page.summary),
            start=start,
            end=end,
            language=page.locale.language_code,
            attendance=str(page.attendance),
            cancelled=page.cancelled_at is not None,
            venue_name=str(page.venue_name),
            street=str(page.street),
            postcode=str(page.postcode),
            city=str(page.city),
            region=str(page.region),
            country=str(getattr(page.country, "code", page.country) or ""),
            latitude=latitude if longitude is not None else None,
            longitude=longitude if latitude is not None else None,
            online_url=str(page.online_url),
            image=_event_image(ctx, page),
            organizer=_event_organizer(ctx, page),
        )
    )


def _event_page(ctx: PageContext) -> list[Node]:
    """The event page: its `WebPage` is about the `Event` node (none for a promotion, a hidden
    event or one with no place and no link)."""
    url = ctx.obj.get_full_url(ctx.request) or ""
    event = _event_node(ctx, url) if url else {}
    if not event:
        return cms_page_nodes(ctx)
    return cms_page_nodes(ctx, main_entity_id=str(event["@id"]), extra=[event])


def _blog_index(ctx: PageContext) -> list[Node]:
    blog = ctx.obj
    author = ctx.context.get("author")
    if author is None:
        url = blog.get_full_url(ctx.request) or ""
        items = _post_items(ctx, url, str(blog.title))
        return cms_page_nodes(ctx, "CollectionPage", items=items)
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
        items=_post_items(ctx, url, str(author.name)),
    )


def _event_index(ctx: PageContext) -> list[Node]:
    """The events of the shown list (the live list, the archive or a month), soonest first;
    an original-language fallback (an event in another language) is left out."""
    index = ctx.obj
    url = index.get_full_url(ctx.request) or ""
    entries = [
        ge.ListEntry(
            url=item.page.get_full_url(ctx.request) or "",
            name=str(item.page.title),
            kind="Event",
        )
        for item in ctx.context.get("items") or ()
        if not item.language
    ]
    return cms_page_nodes(ctx, "CollectionPage", items=_items(ctx, url, str(index.title), entries))


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
    widgets = _product_widget_lists(ctx, url)
    web = ge.webpage_node(
        kind="ItemPage",
        url=url,
        site=ctx.site,
        name=page.title,
        language=page.shown,
        description=str(page.short_description or ""),
        breadcrumb_id=f"{url}#breadcrumb",
        main_entity_id=str(entity["@id"]),
        has_part_ids=[str(node["@id"]) for node in widgets],
    )
    return [web, *nodes, *widgets]


def _product_widget_lists(ctx: PageContext, url: str) -> list[Node]:
    """The product page's widgets as named `ItemList`s of product ids (spec 10, "Widgets feed
    the graph"): the related products and the other sellers of the same article. A list item
    references its product page and repeats no offer; an empty widget adds nothing. The graph is
    printed on noindex pages too (machine readers), as on the search results page."""
    with translation.override(ctx.language):
        widgets = (
            ("related", str(translation.gettext("You may also like"))),
            ("other_sellers", str(translation.gettext("Other sellers of this product"))),
        )
    nodes = [
        ge.item_list_node(
            list_id=f"{url}#{key.replace('_', '-')}",
            name=name,
            language=ctx.language,
            entries=product_entries(ctx, ctx.context.get(key) or ()),
        )
        for key, name in widgets
    ]
    return [node for node in nodes if node]


def _listing(ctx: PageContext) -> list[Node]:
    from apps.catalog import services as catalog
    from apps.catalog import slugs as catalog_slugs
    from apps.core import translations

    anchor = ctx.obj
    language = ctx.language
    shown = language if language in anchor.published else translations.source_language()
    path = anchor.paths.get(language) or anchor.paths.get(shown)
    if not path:
        return []
    url = f"{ctx.site}{path}"
    scope = anchor.scope
    crumbs = [_home_crumb(ctx.site, shown), pg.Crumb(str(anchor.name), url)]
    nodes: list[Node] = []
    if scope.brand is not None:
        nodes = list(
            catalog.brand_graph(scope.brand, shown, site=ctx.site, paths=anchor.paths)["@graph"]
        )
    faq_items: Any = ctx.context.get("editorial_faq") or ()
    faq = ge.faq_node(url=url, language=shown, questions=faq_items)
    breadcrumb = _breadcrumb(url, crumbs)
    items = _items(ctx, url, str(anchor.name), product_entries(ctx, ctx.context.get("cards") or ()))
    main_entity = str(items["@id"]) if items else ""
    parts = [str(faq["@id"])] if faq else []
    vendor = scope.vendor
    public_name = seller_name.public_name(vendor) if vendor is not None else None
    kind = "ProfilePage" if public_name else "CollectionPage"
    if (
        vendor is not None
        and public_name
        and catalog_slugs.vendor_path(shown, vendor.slug, default_language=settings.LANGUAGE_CODE)
        == path
    ):
        organization = pg.organization_node(
            node_id=f"{url}#organization", name=public_name, url=url
        )
        nodes.append(organization)
        if main_entity:
            parts.append(main_entity)
        main_entity = str(organization["@id"])
    web = ge.webpage_node(
        kind=kind,
        url=url,
        site=ctx.site,
        name=str(anchor.name),
        language=shown,
        breadcrumb_id=str(breadcrumb["@id"]),
        main_entity_id=main_entity,
        has_part_ids=parts,
    )
    return [web, breadcrumb, *([faq] if faq else []), *([items] if items else []), *nodes]


_SEARCH_GROUP_KINDS = {"events": "Event", "articles": "BlogPosting", "vendors": "Organization"}


def _vendor_names(items: Iterable[Any]) -> dict[int | None, str | None]:
    """The public seller name (SEO.15) of the vendors behind the search hits, by primary key."""
    pks = [item.ref for item in items if item.ref is not None]
    return {v.pk: seller_name.public_name(v) for v in Vendor.objects.filter(pk__in=pks)}


def _search(ctx: PageContext) -> list[Node]:
    """The results page: a `SearchResultsPage` whose one `ItemList` holds the products of the
    shown page, then the events, articles and vendors found (each referencing its own page). An
    item shown in another language than the page's is left out."""
    from apps.catalog.search_page import SEARCH_PARAMETER

    query = str(ctx.obj.query)
    language = ctx.language
    home = "/" if language == settings.LANGUAGE_CODE else f"/{language}/"
    url = f"{ctx.site}{home}" + (f"?{SEARCH_PARAMETER}={quote(query)}" if query else "")
    with translation.override(language):
        label = str(translation.gettext("Search"))
        name = str(translation.gettext("Search: %(query)s")) % {"query": query} if query else label
    entries = product_entries(ctx, ctx.context.get("cards") or ())
    for group in ctx.context.get("groups") or ():
        kind = _SEARCH_GROUP_KINDS.get(group.key, "")
        names = _vendor_names(group.items) if group.key == "vendors" else {}
        for item in group.items:
            if item.language:
                continue
            title = str(item.title)
            if group.key == "vendors":
                # SEO.15: the Organization carries the public seller name, or is left out.
                title = names.get(item.ref) or ""
                if not title:
                    continue
            entries.append(ge.ListEntry(url=_absolute(ctx.site, item.url), name=title, kind=kind))
    items = _items(ctx, url, name, entries)
    breadcrumb = _breadcrumb(url, [_home_crumb(ctx.site, language), pg.Crumb(label, url)])
    web = ge.webpage_node(
        kind="SearchResultsPage",
        url=url,
        site=ctx.site,
        name=name,
        language=language,
        breadcrumb_id=str(breadcrumb["@id"]),
        main_entity_id=str(items["@id"]) if items else "",
    )
    return [web, breadcrumb, *([items] if items else [])]
