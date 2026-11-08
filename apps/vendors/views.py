"""The vendor claim page (VEN.24, owner decision MQ-1). A thin adapter over `services`.

The claimant reaches it through the email-bound claim link (CORE.15): the link's Continue POST
opens the link in the session, signs nobody in, and leads here. The page then asks, in order:

1. the vendor's VAT number or TIN (`services.confirm_claim_identity`: wrong answers are counted
   and audited, and lock the claim);
2. a password and an authenticator app (the key is kept in the session until the first code
   checks out); `services.submit_claim` then creates the account, spends the link and submits
   the claim for staff approval, all at once.

Every POST is rate limited per IP. A visitor who is signed in is refused: a claim never changes
an existing account. Responses are never cached and send no path in the Referer.
"""

import math
from typing import Any

import segno
from django.contrib.auth import login
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.cache import add_never_cache_headers
from django.utils.translation import get_language
from django.views.decorators.http import require_http_methods

from apps.accounts import engine as accounts_engine
from apps.accounts.decorators import mfa_exempt
from apps.accounts.mfa_session import complete_verification
from apps.accounts.services import (
    ActionLinkCheck,
    forget_email_bound_link,
    new_totp_key,
    pending_totp_device,
)
from apps.core.audit import audit_context
from apps.core.http import client_ip

from . import engine, services
from .models import Vendor

CLAIM_SESSION_KEY = "vendors.claim"
SIGN_IN_BACKEND = "django.contrib.auth.backends.ModelBackend"
TOO_MANY_ATTEMPTS = "Too many attempts. Please try again later."
NO_MATCH = "This does not match the VAT number or TIN we hold for this vendor."
INVALID_CODE = "Invalid code. Check the time on your device and try again."
ACCOUNT_EXISTS = (
    "An account already uses this email address. Contact the platform staff to link it to "
    "the vendor."
)


def _private(response: HttpResponse) -> HttpResponse:
    response["Referrer-Policy"] = "strict-origin"
    response["X-Robots-Tag"] = "noindex, nofollow"
    add_never_cache_headers(response)
    return response


def _page(
    request: HttpRequest, template: str, context: dict[str, Any], status: int = 200
) -> HttpResponse:
    return _private(render(request, f"vendors/claim/{template}.html", context, status=status))


def _refused(request: HttpRequest) -> HttpResponse:
    return _page(request, "invalid", {}, status=403)


def _pending_key(request: HttpRequest, vendor_key: str, link: ActionLinkCheck) -> str:
    """The authenticator key shown to this claimant, bound to the link that opened the page."""

    link_digest = services.claim_link_digest(link)
    pending = dict(request.session.get(CLAIM_SESSION_KEY) or {})
    entry = pending.get(vendor_key)
    if isinstance(entry, dict) and entry.get("link") == link_digest and entry.get("totp_key"):
        return str(entry["totp_key"])
    key = new_totp_key()
    pending[vendor_key] = {"link": link_digest, "totp_key": key}
    request.session[CLAIM_SESSION_KEY] = pending
    return key


def _forget(request: HttpRequest, vendor_key: str) -> None:
    pending = dict(request.session.get(CLAIM_SESSION_KEY) or {})
    pending.pop(vendor_key, None)
    request.session[CLAIM_SESSION_KEY] = pending
    forget_email_bound_link(request.session, services.CLAIM_PURPOSE, vendor_key)


def _account_page(
    request: HttpRequest,
    vendor: Vendor,
    link: ActionLinkCheck,
    errors: list[str] | None = None,
) -> HttpResponse:
    key = _pending_key(request, str(vendor.public_id), link)
    device = pending_totp_device(key, vendor.contact_email)
    qr = segno.make(device.config_url, error="m")
    context = {
        "vendor": vendor,
        "email": vendor.contact_email,
        "qr_svg": qr.svg_inline(scale=4, dark="#000", light="#fff"),
        "secret": accounts_engine.totp_secret(device.bin_key),
        "errors": errors or [],
        "full_name": request.POST.get("full_name", ""),
    }
    return _page(request, "account", context)


def _submit(request: HttpRequest, vendor: Vendor, link: ActionLinkCheck) -> HttpResponse:
    vendor_key = str(vendor.public_id)
    password = request.POST.get("password1", "")
    full_name = request.POST.get("full_name", "").strip()
    errors = []
    if not full_name:
        errors.append("Enter your full name.")
    if password != request.POST.get("password2", ""):
        errors.append("The two passwords do not match.")
    if errors:
        return _account_page(request, vendor, link, errors)
    try:
        submitted = services.submit_claim(
            link,
            full_name=full_name,
            password=password,
            totp_key=_pending_key(request, vendor_key, link),
            totp_code=request.POST.get("code", ""),
            language=get_language() or "",
            ctx=audit_context(request),
        )
    except ValidationError as exc:
        return _account_page(request, vendor, link, list(exc.messages))
    except services.ClaimCodeInvalid:
        return _account_page(request, vendor, link, [INVALID_CODE])
    except services.ClaimAccountExists:
        return _account_page(request, vendor, link, [ACCOUNT_EXISTS])
    except engine.ClaimError:
        return _refused(request)
    _forget(request, vendor_key)
    login(request, submitted.claimant, backend=SIGN_IN_BACKEND)
    complete_verification(request, submitted.enrolment.device)
    context = {"vendor": vendor, "codes": submitted.enrolment.recovery_codes}
    return _page(request, "submitted", context)


@mfa_exempt
@require_http_methods(["GET", "POST"])
def claim(request: HttpRequest, vendor_key: str) -> HttpResponse:
    link = services.claim_link(request.session, vendor_key)
    if link.refusal is not None:
        return _refused(request)
    if request.user.is_authenticated:
        return _page(request, "signed_in", {}, status=403)
    if request.method == "POST":
        wait = services.claim_retry_after(client_ip(request))
        if wait:
            retry = str(max(1, math.ceil(wait.total_seconds())))
            response = HttpResponse(TOO_MANY_ATTEMPTS, status=429, headers={"Retry-After": retry})
            return _private(response)
    vendor = Vendor.objects.get(public_id=vendor_key)  # the valid link names an open claim
    step = request.POST.get("step", "") if request.method == "POST" else ""

    if step == "identity":
        try:
            matched = services.confirm_claim_identity(
                link, request.POST.get("tax_id", ""), ctx=audit_context(request)
            )
        except engine.ClaimError:
            return _refused(request)
        if not matched:
            if services.claim_link(request.session, vendor_key).refusal is not None:
                return _refused(request)  # that answer locked the claim
            return _page(request, "identity", {"vendor": vendor, "error": NO_MATCH})
        return _account_page(request, vendor, link)

    if not services.identity_confirmed(link):
        if step:
            return _refused(request)
        return _page(request, "identity", {"vendor": vendor, "error": ""})
    if step == "account":
        return _submit(request, vendor, link)
    return _account_page(request, vendor, link)
