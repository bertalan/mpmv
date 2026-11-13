"""Public, printable page of a seller's sales terms in force (VEN.35, VEN.32 exception).

Readable by anyone through a signed, expiring, multi-use link; every read is logged. A superseded
or tampered link is a plain 404.
"""

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.core.http import client_ip

from . import terms_services as services


@require_GET
def read(request: HttpRequest, token: str) -> HttpResponse:
    version = services.read_terms(token, ctx=services.anonymous_reader(client_ip(request)))
    if version is None:
        raise Http404("These terms are not available.")
    response = render(
        request,
        "vendors/terms/read.html",
        {
            "version": version,
            "vendor": version.vendor,
            "business": services.recap(version, buyer_is_business=True),
            "consumer": services.recap(version, buyer_is_business=False),
        },
    )
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
