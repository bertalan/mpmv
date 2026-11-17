"""Site-wide schema.org graph nodes (SEO.7), pure functions: plain data in, nodes out. No ORM.

Every public page's `@graph` holds the operator `Organization`, the `WebSite`, the page's own
`WebPage` (or subtype) and its `BreadcrumbList`. Ids are the canonical URL of the thing plus a
fragment, so one entity is one node site-wide. An empty value is left out, never defaulted.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

SCHEMA: Final = "https://schema.org"
PAGE_TYPES: Final = frozenset(
    {
        "WebPage",
        "ItemPage",
        "CollectionPage",
        "ProfilePage",
        "AboutPage",
        "ContactPage",
        "SearchResultsPage",
    }
)


def _clean(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in ("", None, [], ())}


def operator_id(site: str) -> str:
    return f"{site}/#organization"


def website_id(site: str) -> str:
    return f"{site}/#website"


def webpage_id(url: str) -> str:
    return f"{url}#webpage"


def operator_node(
    *,
    site: str,
    name: str,
    logo_url: str = "",
    email: str = "",
    vat_id: str = "",
    profiles: Sequence[str] = (),
    language: str = "",
) -> dict[str, Any]:
    """The platform operator: legal name, logo, VAT ID, profiles and a support contact point."""
    return _clean(
        {
            "@type": "Organization",
            "@id": operator_id(site),
            "name": name,
            "legalName": name,
            "url": f"{site}/",
            "logo": logo_url,
            "vatID": vat_id,
            "sameAs": list(profiles),
            "contactPoint": (
                {"@type": "ContactPoint", "contactType": "customer support", "email": email}
                if email
                else ""
            ),
        }
    )


def website_node(
    *, site: str, name: str, language: str, search_template: str = "", publisher: bool = True
) -> dict[str, Any]:
    """The `WebSite`; a `SearchAction` only when the site has a search page (`search_template`
    holds the `{search_term_string}` placeholder)."""
    action = (
        {
            "@type": "SearchAction",
            "target": {"@type": "EntryPoint", "urlTemplate": search_template},
            "query-input": "required name=search_term_string",
        }
        if search_template
        else ""
    )
    return _clean(
        {
            "@type": "WebSite",
            "@id": website_id(site),
            "url": f"{site}/",
            "name": name,
            "inLanguage": language,
            "publisher": {"@id": operator_id(site)} if publisher else "",
            "potentialAction": action,
        }
    )


def webpage_node(
    *,
    kind: str,
    url: str,
    site: str,
    name: str,
    language: str,
    description: str = "",
    breadcrumb_id: str = "",
    image: str = "",
    main_entity_id: str = "",
    has_part_ids: Iterable[str] = (),
    date_modified: str = "",
) -> dict[str, Any]:
    if kind not in PAGE_TYPES:
        raise ValueError(f"not a schema.org page type: {kind}")
    return _clean(
        {
            "@type": kind,
            "@id": webpage_id(url),
            "url": url,
            "name": name,
            "description": description,
            "inLanguage": language,
            "isPartOf": {"@id": website_id(site)},
            "breadcrumb": {"@id": breadcrumb_id} if breadcrumb_id else "",
            "primaryImageOfPage": image,
            "mainEntity": {"@id": main_entity_id} if main_entity_id else "",
            "hasPart": [{"@id": part} for part in has_part_ids],
            "dateModified": date_modified,
        }
    )


def faq_node(*, url: str, language: str, questions: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """The page's one `FAQPage` for (question, answer HTML) pairs, empty for none."""
    items = [
        {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
        for q, a in questions
    ]
    if not items:
        return {}
    return {
        "@type": "FAQPage",
        "@id": f"{url}#faq",
        "inLanguage": language,
        "mainEntity": items,
    }


def compose(nodes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """One `@context` and a `@graph`; a node id described twice stays once, the first wins."""
    seen: set[str] = set()
    graph: list[dict[str, Any]] = []
    for node in nodes:
        if not node:
            continue
        node_id = node.get("@id")
        if node_id is not None:
            if node_id in seen:
                continue
            seen.add(str(node_id))
        graph.append({k: v for k, v in node.items() if k != "@context"})
    return {"@context": SCHEMA, "@graph": graph}
