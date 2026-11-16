"""Consent views (SEO.5): save a choice, and the page where a visitor changes it.

The banner is the same on every page, so a choice cannot carry a CSRF token that a cached page
would hold for another visitor. `consent_save` is exempt from CSRF and checks instead that the
request comes from this site (`Origin`, else `Referer`): a form on another site cannot give or
withdraw consent for a visitor. It is rate limited per IP address.
"""

import datetime as dt
import math
from urllib.parse import urlsplit

from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.http.response import HttpResponseBase
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_safe

from apps.core.http import client_ip
from apps.core.public_forms import Protection, public_form
from apps.core.services import consume_rate_limit

from . import engine, site_links
from .providers import category_texts, get_provider

CHOICE_LIMIT = 20  # a visitor decides once or twice; 20 a minute per IP is generous
CHOICE_WINDOW = dt.timedelta(minutes=1)


def _same_site(request: HttpRequest) -> bool:
    """The request was made by a page of this site: `Origin`, else `Referer`, names this host."""
    source = request.headers.get("Origin") or request.headers.get("Referer") or ""
    parts = urlsplit(source)
    return bool(parts.netloc) and parts.netloc == request.get_host()


def _next_url(request: HttpRequest) -> str:
    for candidate in (request.POST.get("next", ""), request.headers.get("Referer", "")):
        if candidate and url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return candidate
    return "/"


def _with_saved_marker(target: str) -> str:
    """Add `?saved=1` when the redirect goes back to the settings page (the visible notice)."""
    parts = urlsplit(target)
    if parts.path.rstrip("/") != reverse("consent_preferences").rstrip("/"):
        return target
    query = [q for q in parts.query.split("&") if q and not q.startswith("saved=")]
    return parts._replace(query="&".join([*query, "saved=1"])).geturl()


def _set_cookie(
    response: HttpResponseBase, request: HttpRequest, state: engine.ConsentState
) -> None:
    response.set_cookie(
        engine.COOKIE_NAME,
        engine.encode_cookie(state),
        max_age=engine.COOKIE_MAX_AGE,
        path="/",
        secure=request.is_secure(),
        httponly=False,  # the page script reads it to start gated tags without a round trip
        samesite="Lax",
    )


@public_form(
    protection=Protection.ALLOW,
    purpose="Cookie consent choice (banner and cookie settings page)",
    task="SEO.5",
    reason="stores a random id and the categories chosen; refused unless the request comes "
    "from this site, since a cached banner cannot hold a CSRF token",
    rate_limit="per IP, generous",
)
@csrf_exempt  # a cached page cannot carry a token; the same-site check below replaces it
@require_POST
def consent_save(request: HttpRequest) -> HttpResponseBase:
    provider = get_provider()
    if not provider.accepts_choices:
        return HttpResponse(status=404)
    if not _same_site(request):
        return HttpResponse("Cross-site request refused.", status=403)
    wait = consume_rate_limit(
        f"consent:ip:{client_ip(request)}", limit=CHOICE_LIMIT, window=CHOICE_WINDOW
    )
    if wait:
        retry = str(max(1, math.ceil(wait.total_seconds())))
        return HttpResponse("Too many requests.", status=429, headers={"Retry-After": retry})
    action = request.POST.get("action", "")
    if action not in engine.ACTIONS:
        return HttpResponse("Unknown choice.", status=400)
    state = provider.record(request, action, request.POST.getlist("categories"))
    response: HttpResponseBase
    if "application/json" in request.headers.get("Accept", ""):
        response = JsonResponse(
            {
                "granted": sorted(state.granted),
                "banner_version": state.banner_version,
                "signals": engine.consent_signals(state),
            }
        )
    else:
        response = HttpResponseRedirect(_with_saved_marker(_next_url(request)), status=303)
    response["Cache-Control"] = "no-store"
    _set_cookie(response, request, state)
    return response


@require_GET
@never_cache  # shows this visitor's own choice
def consent_preferences(request: HttpRequest) -> HttpResponse:
    """Where a visitor looks at the choice and changes it, with or without JavaScript."""
    provider = get_provider()
    if not provider.accepts_choices:
        raise Http404("This site's consent tool has no settings page of ours")
    state = provider.state(request)
    categories = category_texts()
    saved = state.decided and request.GET.get("saved") == "1"
    return render(
        request,
        "seo/consent/preferences.html",
        {
            "categories": categories,
            "saved": saved,
            "optional_on": [
                c["label"] for c in categories if not c["locked"] and c["code"] in state.granted
            ],
            "granted": sorted(state.granted),
            "decided": state.decided,
            "action_url": reverse("consent_save"),
            "preferences_url": reverse("consent_preferences"),
        },
    )


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
