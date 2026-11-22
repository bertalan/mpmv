"""Public, printable page of a seller's sales terms in force (VEN.35, VEN.32 exception).

Readable by anyone through a signed, expiring, multi-use link; every read is logged. A superseded
or tampered link is a plain 404. The same link downloads the version as a PDF (VEN.36).
"""

import hashlib
from typing import Any

from django.core.cache import cache
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils.http import parse_etags
from django.views.decorators.http import require_GET

from apps.core.http import client_ip
from apps.core.i18n import language_switch_home
from apps.core.pdf import render_html
from apps.core.services import AttachmentUnavailable

from . import terms_services as services
from .models import SalesTermsVersion

PDF_CACHE_SECONDS = 24 * 60 * 60
PDF_CACHE_CONTROL = "private, no-cache"


def _context(version: SalesTermsVersion, token: str) -> dict[str, Any]:
    return {
        "version": version,
        "vendor": version.vendor,
        "token": token,
        "business": services.recap(version, buyer_is_business=True),
        "consumer": services.recap(version, buyer_is_business=False),
    }


@language_switch_home
@require_GET
def read(request: HttpRequest, token: str) -> HttpResponse:
    version = services.read_terms(token, ctx=services.anonymous_reader(client_ip(request)))
    if version is None:
        raise Http404("These terms are not available.")
    response = render(request, "vendors/terms/read.html", _context(version, token))
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@language_switch_home
@require_GET
def download(request: HttpRequest, token: str) -> HttpResponse:
    """The same signed link as `read`, as a PDF of the version it names (VEN.36)."""
    version = services.read_terms(
        token, ctx=services.anonymous_reader(client_ip(request)), fmt="pdf"
    )
    if version is None:
        raise Http404("These terms are not available.")
    # The text of a version never changes, but the page also shows the shop name (live for rows
    # without a snapshot) and the operator's data. Key the cache and the ETag on the rendered
    # HTML, so a rename never serves a stale PDF. The ETag is revalidated, never kept for long:
    # the URL carries a signed, expiring token.
    html = render_to_string(
        "documents/vendors/sales_terms.html", _context(version, token), request=request
    )
    digest = hashlib.sha256(html.encode()).hexdigest()[:32]
    etag = f'"{version.pk}-{digest}"'
    sent = {tag.removeprefix("W/") for tag in parse_etags(request.headers.get("If-None-Match", ""))}
    if "*" in sent or etag in sent:
        response = HttpResponse(status=304)
        response["ETag"] = etag
        response["Cache-Control"] = PDF_CACHE_CONTROL
        return response
    key = f"vendors:terms-pdf:{version.pk}:{digest}"
    pdf = cache.get(key)
    if pdf is None:
        try:
            pdf = render_html(html)
        except AttachmentUnavailable:
            return HttpResponse("The PDF is not available now, try again later.", status=503)
        cache.set(key, pdf, PDF_CACHE_SECONDS)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["ETag"] = etag
    response["Cache-Control"] = PDF_CACHE_CONTROL
    name = f"sales-terms-{version.vendor.slug}-v{version.number}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
