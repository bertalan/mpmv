"""`page_graph`: the one schema.org `@graph` of a public page (SEO.7, spec 10).

Each page type registers a builder (a registry, like the sitemaps): it returns the page's own
nodes, including its `WebPage` (or subtype) and `BreadcrumbList`. `page_graph` adds the shared
nodes, the operator `Organization` and the `WebSite`, and composes them; one entity is one `@id`
site-wide. Absolute URLs come from the site root, never the Host header. The layout prints the
result once (`{% page_graph_jsonld %}`), so a page has exactly one JSON-LD script.

A public page type without a builder fails the sweep test: register one (`register`) or state
why the type has no graph (`exempt`).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from django.http import HttpRequest
from django.utils import translation

from . import graph_engine as ge

Node = dict[str, Any]


@dataclass(frozen=True)
class PageContext:
    """What a builder gets: the page object, the request and the template context."""

    request: HttpRequest
    obj: Any
    language: str
    site: str  # the origin, without a trailing slash
    context: Mapping[str, Any] = field(default_factory=dict)


Builder = Callable[[PageContext], Sequence[Node]]
Provider = Callable[[PageContext], Sequence[Node]]

_BUILDERS: dict[type, Builder] = {}
_EXEMPT: dict[type, str] = {}
_PROVIDERS: dict[type, list[Provider]] = {}


def register(model: type) -> Callable[[Builder], Builder]:
    """Register the builder of a page type (decorator)."""

    def decorate(builder: Builder) -> Builder:
        _BUILDERS[model] = builder
        return builder

    return decorate


def exempt(model: type, reason: str) -> None:
    """A page type that has no graph, and why (not public, noindex without content)."""
    if not reason.strip():
        raise ValueError("an exemption needs a reason")
    _EXEMPT[model] = reason


def add_nodes(model: type, provider: Provider) -> None:
    """Let another app add nodes to the graph of a page type (EVT.10: the `Event` node)."""
    _PROVIDERS.setdefault(model, []).append(provider)


def _lookup[T](table: Mapping[type, T], model: type) -> T | None:
    for klass in model.__mro__:
        if klass in table:
            return table[klass]
    return None


def covers(model: type) -> bool:
    return _lookup(_BUILDERS, model) is not None or _lookup(_EXEMPT, model) is not None


def registered_types() -> list[type]:
    return list(_BUILDERS)


def exemptions() -> dict[type, str]:
    return dict(_EXEMPT)


def _site_nodes(ctx: PageContext) -> list[Node]:
    from apps.core.services import NoPlatformOperator, current_operator

    from .site_nodes import operator_graph_node, website_graph_node

    try:
        operator = current_operator()
    except NoPlatformOperator:
        return [website_graph_node(ctx.request, ctx.site, ctx.language, publisher=False)]
    return [
        operator_graph_node(operator, ctx.site, ctx.language),
        website_graph_node(ctx.request, ctx.site, ctx.language, publisher=True),
    ]


def page_graph(
    request: HttpRequest,
    obj: Any,
    language: str | None = None,
    context: Mapping[str, Any] | None = None,
) -> Node | None:
    """The graph of `obj` in `language`; None for an object whose type has no builder."""
    from apps.core.site_url import site_root_url

    builder = _lookup(_BUILDERS, type(obj))
    if builder is None:
        return None
    lang = (language or translation.get_language() or "").split("-")[0]
    ctx = PageContext(
        request=request,
        obj=obj,
        language=lang,
        site=site_root_url(request),
        context=context or {},
    )
    nodes = [*_site_nodes(ctx), *builder(ctx)]
    for provider in _lookup(_PROVIDERS, type(obj)) or []:
        nodes += provider(ctx)
    return ge.compose(nodes)


def graph_text(graph: Mapping[str, Any]) -> str:
    from apps.catalog.engine import jsonld_text

    return jsonld_text(graph)
