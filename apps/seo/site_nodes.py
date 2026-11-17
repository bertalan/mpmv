"""The shared nodes of every page's graph, from the operator row (SEO.7)."""

from typing import Any
from urllib.parse import urljoin

from django.http import HttpRequest
from wagtail.models import Site

from . import graph_engine as ge


def _logo_url(operator: Any, site: str) -> str:
    logo = operator.logo
    if logo is None or not logo.file:
        return ""
    try:
        return urljoin(f"{site}/", str(logo.file.url))
    except ValueError:  # no file behind the field
        return ""


def operator_graph_node(operator: Any, site: str, language: str) -> dict[str, Any]:
    from apps.cms.engine import profile_links

    return ge.operator_node(
        site=site,
        name=str(operator.legal_name),
        logo_url=_logo_url(operator, site),
        email=str(operator.support_email),
        vat_id=str(operator.vat_number),
        profiles=profile_links(operator.official_profiles),
        language=language,
    )


def website_graph_node(
    request: HttpRequest, site: str, language: str, *, publisher: bool
) -> dict[str, Any]:
    row = Site.find_for_request(request)
    name = (row.site_name if row is not None and row.site_name else "") or "MPMV"
    return ge.website_node(
        site=site, name=name, language=language, search_template="", publisher=publisher
    )
