"""The payment provider webhook endpoint (PAY.2): a thin adapter over `services.process_webhook`."""

from django.http import Http404, HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.core.http import client_ip
from apps.core.public_forms import Protection, public_form

from . import services

SIGNATURE_HEADER = "Stripe-Signature"
MAX_BODY_BYTES = 512 * 1024


@public_form(
    protection=Protection.ALLOW,
    purpose="Payment provider events (payment succeeded)",
    task="PAY.2",
    reason="machine to machine: the provider's signature and a timestamp tolerance are checked "
    "first; a replayed event id does nothing",
    no_rate_limit="an unsigned request is refused before any work; off (404) with no provider",
)
@csrf_exempt  # Stripe signs the request; it has no session or CSRF token
@require_POST
def payment_webhook(request: HttpRequest) -> HttpResponse:
    if int(request.headers.get("Content-Length") or 0) > MAX_BODY_BYTES:
        return HttpResponse(status=413)
    result = services.process_webhook(
        request.body, request.headers.get(SIGNATURE_HEADER, ""), ip=client_ip(request)
    )
    if result.outcome is services.Outcome.NOT_CONFIGURED:
        raise Http404("payment webhook not configured")
    if result.outcome in (services.Outcome.BAD_SIGNATURE, services.Outcome.MALFORMED):
        return HttpResponse(status=400)
    return HttpResponse(status=200)
