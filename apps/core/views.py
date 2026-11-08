"""Core HTTP endpoints: the email provider webhook (ARC.16).

The webhook is provider neutral: `services.feedback_parser()` checks the signature and reads the
events (the signed JSON stub of `apps.core.mail` until ARC.19 plugs in the provider's format),
and `services.record_email_feedback` records them. It is off (404) while no secret is set.
"""

import time

from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import mail, services
from .audit import AuditContext
from .http import client_ip
from .models import AuditLog

WEBHOOK_ACTOR = "webhook:email"


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
