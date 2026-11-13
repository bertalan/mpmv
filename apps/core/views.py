"""Core HTTP endpoints: the email provider webhook (ARC.16).

The webhook is provider neutral: `services.feedback_parser()` checks the signature and reads the
events (the signed JSON stub of `apps.core.mail` until ARC.19 plugs in the provider's format),
and `services.record_email_feedback` records them. It is off (404) while no secret is set.
"""

import time

from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.urls import NoReverseMatch, reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from . import indexing, mail, services
from .audit import AuditContext
from .http import client_ip
from .models import AuditLog
from .public_forms import Protection, public_form

WEBHOOK_ACTOR = "webhook:email"


@public_form(
    protection=Protection.ALLOW,
    purpose="Email provider delivery feedback (bounces, complaints)",
    task="ARC.16",
    reason="machine to machine: the provider's signature and a replay window are checked first",
    no_rate_limit="an unsigned request is refused before any work; off (404) with no secret",
)
@csrf_exempt  # the provider signs the request; it has no session or CSRF token
@require_POST
def email_webhook(request: HttpRequest) -> HttpResponse:
    parser = services.feedback_parser()
    if parser is None:
        raise Http404("email webhook not configured")
    body = request.body
    if not parser.verify(request.headers, body, now=time.time()):
        return HttpResponse(status=403)
    try:
        events = parser.parse(body)
    except mail.InvalidFeedback:
        return HttpResponse(status=400)
    ctx = AuditContext(
        actor_label=WEBHOOK_ACTOR, source=AuditLog.Source.SYSTEM, ip=client_ip(request)
    )
    with transaction.atomic():
        for event in events:
            services.record_email_feedback(event, ctx=ctx)
    return HttpResponse(status=204)


@require_safe
def robots_view(request: HttpRequest) -> HttpResponse:
    """`/robots.txt` (SEO.8): public, no language prefix, no vendor data."""
    forced = indexing.is_noindex(request)
    try:
        sitemap_url = "" if forced else request.build_absolute_uri(reverse("sitemap"))
    except NoReverseMatch:  # the sitemaps (SEO.2) add the route
        sitemap_url = ""
    return HttpResponse(
        indexing.robots_txt(forced=forced, sitemap_url=sitemap_url),
        content_type="text/plain; charset=utf-8",
    )
