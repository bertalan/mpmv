"""Public-side views of the seo app."""

from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from . import site_links


@never_cache
@require_safe
def site_links_page(request: HttpRequest) -> HttpResponse:
    """`/_links/`: the temporary staff "Site links" page (OPS.12; remove before LIVE).

    A visitor is sent to the site sign-in page (`LOGIN_URL`) and back; a signed-in user
    without staff status gets 404, so the page does not exist for them. Never indexed (meta
    tag, `X-Robots-Tag`, robots.txt) nor stored (`never_cache`).
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    if not (request.user.is_active and request.user.is_staff):
        raise Http404
    response = render(
        request,
        "seo/site_links.html",
        {"sections": site_links.site_link_sections(request)},
    )
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
