"""Error page handlers that never need the database (THM.3), and the styleguide page (THM.4).

Spec 14 §14.6, §14.9.

Django renders 403, 404 and the CSRF failure page with the request context, so the theme's
`403.html`, `404.html` and `403_csrf.html` get the full header and footer. The 400 and 500
handlers here do not: a 500 is often caused by the database, and a 400 by a bad `Host` header
that the site lookup would raise on again. They render the error layout with a plain context
(`minimal_chrome`: no CMS menus, no language switcher) and the theme the request already
resolved, or the default theme; if even that fails, a plain text response.
"""

import logging
from typing import Any

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotFound,
    HttpResponseRedirect,
    HttpResponseServerError,
)
from django.shortcuts import render
from django.template import loader
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .models import BrandPalette
from .palettes import ROLES
from .services import (
    active_theme,
    get_registry,
    icon_url,
    palette_stylesheet,
    resolved_theme,
    use_theme,
)
from .styleguide import (
    sample_breadcrumbs,
    sample_forms,
    sample_in_page_links,
    sample_library,
    sample_pages,
    sample_products,
    sample_tabs,
)

logger = logging.getLogger(__name__)


def _render_plain(request: HttpRequest, template_name: str) -> str:
    theme = resolved_theme(request)
    context: dict[str, Any] = {
        "minimal_chrome": True,
        "theme": theme,
        "help_url": settings.MPMV_HELP_URL,
        "site_name": settings.WAGTAIL_SITE_NAME,
    }
    with use_theme(theme.slug):
        return loader.get_template(template_name).render(context)


def server_error(request: HttpRequest) -> HttpResponse:
    """`handler500`: the theme's `500.html` without the database or the request context."""
    try:
        body = _render_plain(request, "500.html")
    except Exception:
        logger.exception("the 500 page could not be rendered")
        return HttpResponseServerError("<h1>Server Error (500)</h1>")
    return HttpResponseServerError(body)


def bad_request(request: HttpRequest, exception: Exception) -> HttpResponse:
    """`handler400`: the theme's `400.html`, rendered like the 500 page."""
    try:
        body = _render_plain(request, "400.html")
    except Exception:
        logger.exception("the 400 page could not be rendered")
        return HttpResponseBadRequest("<h1>Bad Request (400)</h1>")
    return HttpResponseBadRequest(body)


@never_cache
# The site login, not the loopback-only django-admin one.
@staff_member_required(login_url=settings.MFA_LOGIN_URL)  # type: ignore[untyped-decorator]
def styleguide(request: HttpRequest) -> HttpResponse:
    """`/_styleguide/` (THM.4, spec 14 §14.9): every component in every state, staff only.

    Staff only (the MFA middleware has already asked staff for their second factor), never
    indexed (meta tag and `X-Robots-Tag`) nor cached. It is not a CMS page, so no sitemap lists
    it. Accessibility and visual tests run against it.
    """
    response = render(
        request,
        "styleguide.html",
        {
            "forms": sample_forms(),
            "products": sample_products(),
            "breadcrumbs": sample_breadcrumbs(),
            "pages": sample_pages(5, 9),
            "tabs": sample_tabs(),
            "library": sample_library(),
            "skip_links": sample_in_page_links()["skip"],
            "page_links": sample_in_page_links()["page"],
            # ?focus=errors: the first error summary takes the focus on load (e2e tests).
            "focus_errors": request.GET.get("focus") == "errors",
        },
    )
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@require_GET
def palette_css(request: HttpRequest, theme: str, palette: str, digest: str) -> HttpResponse:
    """The stylesheet of a palette on a theme. The URL holds the content digest, so a matching
    one is cached for a year; a stale digest (the palette changed since the page was built)
    still gets the current CSS, with a short max-age so digest cycling cannot hammer the origin.

    The theme is checked against the registry before any query, and a miss is a bare 404 (no
    error page, so no site or menu queries for a probe)."""
    if theme not in get_registry():
        return HttpResponseNotFound()
    row = BrandPalette.objects.filter(slug=palette).only("slug", *ROLES).first()
    if row is None or row.violations():
        return HttpResponseNotFound()
    css, current = palette_stylesheet(theme, row)
    if not css:
        return HttpResponseNotFound()
    response = HttpResponse(css, content_type="text/css; charset=utf-8")
    response["Cache-Control"] = (
        "public, max-age=31536000, immutable" if digest == current else "public, max-age=60"
    )
    return response


@require_GET
def favicon(request: HttpRequest) -> HttpResponse:
    """`/favicon.ico` (THM.38): a redirect to the active theme's 32 px icon, never a 404.

    Browsers ask for this path whatever the page links. The theme is the site's own choice
    (`active_theme`, one cached query), not the environment default: no template renders here,
    so `resolved_theme` would never see it. The target is a hashed static file, so the redirect
    itself is cached for a day only (a site may switch theme)."""
    response = HttpResponseRedirect(icon_url(active_theme(request).slug, "icon-32.png"))
    response["Cache-Control"] = "public, max-age=86400"
    return response
