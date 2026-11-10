"""Public compliance pages (CMP.5): the DSA points of contact (articles 11 and 12)."""

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.core.services import NoPlatformOperator

from . import services


@require_GET
def contact_points(request: HttpRequest) -> HttpResponse:
    """Who authorities and users contact, and in which languages, from the current operator."""
    try:
        points = services.contact_points()
    except NoPlatformOperator as exc:
        raise Http404("No platform operator is configured") from exc
    return render(
        request,
        "compliance/contact_points.html",
        {
            "points": points,
            "authority_languages": [services.language_name(c) for c in points.authority_languages],
            "user_languages": [services.language_name(c) for c in points.user_languages],
        },
    )
