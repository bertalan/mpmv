"""MFA pages (CORE.4): enrolment, verification, passkey ceremonies, recovery codes, factors.

Thin adapters over `services`. The pages a user needs before the second factor is verified
are `@mfa_exempt`; enrolling another factor once one exists needs a recent verification, so a
stolen password alone can never add the attacker's authenticator.
"""

import json
import uuid
from typing import Any

import segno
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_http_methods, require_POST
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.core.audit import audit_context

from . import engine, services
from .decorators import mfa_exempt, mfa_redirect, step_up_required
from .http import relying_party, safe_next
from .mfa_session import complete_verification, is_recently_verified
from .models import PasskeyDevice, User

REGISTRATION_CHALLENGE = "mfa_passkey_registration_challenge"
AUTHENTICATION_CHALLENGE = "mfa_passkey_authentication_challenge"
PENDING_TOTP_DEVICE = "mfa_pending_totp_device"  # the TOTP device this session is enrolling

signed_in = login_required(login_url=settings.MFA_LOGIN_URL)


def _user(request: HttpRequest) -> User:
    user = request.user
    assert isinstance(user, User)  # noqa: S101  # guaranteed by `signed_in`
    return user


def _may_enrol(request: HttpRequest) -> bool:
    """First factor: always. Further factors: only right after a verification (step-up)."""
    return not services.has_second_factor(_user(request)) or is_recently_verified(request)


def _with_next(name: str, next_url: str) -> str:
    return f"{reverse(name)}?{urlencode({'next': next_url})}"


def _json_body(request: HttpRequest) -> dict[str, Any]:
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _bad_request(error: str) -> JsonResponse:
    return JsonResponse({"error": error}, status=400)


# --- Enrolment


@mfa_exempt
@signed_in
@require_http_methods(["GET"])
def setup(request: HttpRequest) -> HttpResponse:
    if not _may_enrol(request):
        return mfa_redirect(request, "mfa:verify")
    next_url = safe_next(request)
    return render(
        request,
        "accounts/mfa/setup.html",
        {
            "next": next_url,
            "totp_url": _with_next("mfa:totp", next_url),
            "register_options_url": reverse("mfa:passkey_register_options"),
            "register_url": _with_next("mfa:passkey_register", next_url),
            "required": services.mfa_required(_user(request)),
        },
    )


@mfa_exempt
@signed_in
@require_http_methods(["GET", "POST"])
def totp(request: HttpRequest) -> HttpResponse:
    if not _may_enrol(request):
        return mfa_redirect(request, "mfa:verify")
    user = _user(request)
    next_url = safe_next(request)
    pending_id = request.session.get(PENDING_TOTP_DEVICE)
    device = services.start_totp_enrolment(
        user, pending_id if isinstance(pending_id, int) else None
    )
    request.session[PENDING_TOTP_DEVICE] = device.pk
    error = ""
    if request.method == "POST":
        try:
            enrolment = services.confirm_totp_device(
                device, request.POST.get("token", "").strip(), ctx=audit_context(request)
            )
        except services.MfaError:
            error = "Invalid code. Check the time on your device and try again."
        else:
            request.session.pop(PENDING_TOTP_DEVICE, None)
            complete_verification(request, enrolment.device)
            return render(
                request,
                "accounts/mfa/recovery_codes_new.html",
                {"codes": enrolment.recovery_codes, "next": next_url, "enrolled": True},
            )
    qr = segno.make(device.config_url, error="m")
    return render(
        request,
        "accounts/mfa/totp.html",
        {
            "qr_svg": qr.svg_inline(scale=4, dark="#000", light="#fff"),
            "secret": engine.totp_secret(device.bin_key),
            "next": next_url,
            "error": error,
        },
        status=200,
    )


# --- Verification


@mfa_exempt
@signed_in
@require_http_methods(["GET", "POST"])
def verify(request: HttpRequest) -> HttpResponse:
    user = _user(request)
    if not services.has_second_factor(user):
        return mfa_redirect(request, "mfa:setup", next_url=safe_next(request))
    next_url = safe_next(request)
    error = ""
    if request.method == "POST":
        method = request.POST.get("method")
        device: object | None = None
        if method == "totp":
            device = services.verify_totp(user, request.POST.get("token", "").strip())
        elif method == "recovery":
            device = services.use_recovery_code(
                user, request.POST.get("code", ""), ctx=audit_context(request)
            )
        if device is not None:
            complete_verification(request, device)
            return HttpResponseRedirect(next_url)
        services.record_mfa_failure(user, str(method), ctx=audit_context(request))
        error = "Invalid code. Try again."
    return render(
        request,
        "accounts/mfa/verify.html",
        {
            "next": next_url,
            "error": error,
            "has_totp": TOTPDevice.objects.filter(user=user, confirmed=True).exists(),
            "has_passkey": PasskeyDevice.objects.filter(user=user, confirmed=True).exists(),
            "has_recovery": services.unused_recovery_codes(user) > 0,
            "auth_options_url": reverse("mfa:passkey_auth_options"),
            "auth_url": _with_next("mfa:passkey_auth", next_url),
        },
    )


# --- Passkeys (JSON endpoints used by accounts/passkey.js)


@mfa_exempt
@signed_in
@require_POST
def passkey_register_options(request: HttpRequest) -> JsonResponse:
    if not _may_enrol(request):
        return JsonResponse({"error": "mfa_verification_required"}, status=403)
    user = _user(request)
    options = engine.passkey_registration_options(
        relying_party(request),
        user_handle=user.public_id.bytes,
        user_name=user.email,
        display_name=user.full_name,
        exclude_credential_ids=list(
            PasskeyDevice.objects.filter(user=user).values_list("credential_id", flat=True)
        ),
    )
    request.session[REGISTRATION_CHALLENGE] = engine.b64url(options.challenge)
    return JsonResponse({"publicKey": options.public_key})


@mfa_exempt
@signed_in
@require_POST
def passkey_register(request: HttpRequest) -> JsonResponse:
    if not _may_enrol(request):
        return JsonResponse({"error": "mfa_verification_required"}, status=403)
    challenge = request.session.pop(REGISTRATION_CHALLENGE, None)
    body = _json_body(request)
    if not challenge or not isinstance(body.get("credential"), dict):
        return _bad_request("no pending passkey registration")
    try:
        passkey = engine.verify_passkey_registration(
            relying_party(request), body["credential"], challenge=engine.from_b64url(challenge)
        )
        enrolment = services.register_passkey(
            _user(request),
            passkey,
            name=str(body.get("name") or "Passkey"),
            ctx=audit_context(request),
        )
    except engine.PasskeyError:
        return _bad_request("the passkey could not be registered")
    complete_verification(request, enrolment.device)
    return JsonResponse(
        {"redirect": safe_next(request), "recovery_codes": enrolment.recovery_codes}
    )


@mfa_exempt
@signed_in
@require_POST
def passkey_auth_options(request: HttpRequest) -> JsonResponse:
    credential_ids = list(
        PasskeyDevice.objects.filter(user=_user(request), confirmed=True).values_list(
            "credential_id", flat=True
        )
    )
    if not credential_ids:
        return _bad_request("no passkey registered")
    options = engine.passkey_authentication_options(
        relying_party(request), credential_ids=credential_ids
    )
    request.session[AUTHENTICATION_CHALLENGE] = engine.b64url(options.challenge)
    return JsonResponse({"publicKey": options.public_key})


@mfa_exempt
@signed_in
@require_POST
def passkey_auth(request: HttpRequest) -> JsonResponse:
    challenge = request.session.pop(AUTHENTICATION_CHALLENGE, None)
    body = _json_body(request)
    if not challenge or not isinstance(body.get("credential"), dict):
        return _bad_request("no pending passkey authentication")
    try:
        device = services.authenticate_passkey(
            _user(request),
            relying_party(request),
            body["credential"],
            challenge=engine.from_b64url(challenge),
        )
    except engine.PasskeyError:
        services.record_mfa_failure(_user(request), services.PASSKEY, ctx=audit_context(request))
        return _bad_request("the passkey was not accepted")
    complete_verification(request, device)
    return JsonResponse({"redirect": safe_next(request)})


# --- Managing factors (behind the MFA middleware: the session is verified here)


@signed_in
@require_http_methods(["GET"])
def overview(request: HttpRequest) -> HttpResponse:
    user = _user(request)
    factors = [
        {
            "device": device,
            "kind": services.kind_of(device),
            "remove_url": reverse(
                "mfa:remove_device",
                kwargs={
                    "kind": services.kind_of(device),
                    "public_id": getattr(device, "public_id", device.pk),
                },
            ),
        }
        for device in services.second_factors(user)
    ]
    return render(
        request,
        "accounts/mfa/overview.html",
        {
            "factors": factors,
            "unused_codes": services.unused_recovery_codes(user),
            "totp_url": _with_next("mfa:totp", reverse("mfa:overview")),
            "register_options_url": reverse("mfa:passkey_register_options"),
            "register_url": _with_next("mfa:passkey_register", reverse("mfa:overview")),
        },
    )


@signed_in
@step_up_required()
@require_http_methods(["GET", "POST"])
def recovery_codes(request: HttpRequest) -> HttpResponse:
    user = _user(request)
    if request.method == "POST":
        result = services.regenerate_recovery_codes(user, ctx=audit_context(request))
        return render(
            request,
            "accounts/mfa/recovery_codes_new.html",
            {"codes": result.codes, "next": reverse("mfa:overview"), "enrolled": False},
        )
    return render(
        request,
        "accounts/mfa/recovery_codes.html",
        {"unused_codes": services.unused_recovery_codes(user)},
    )


def _own_device(user: User, kind: str, public_id: str) -> services.SecondFactor:
    try:
        if kind == services.PASSKEY:
            return get_object_or_404(PasskeyDevice, user=user, public_id=uuid.UUID(public_id))
        if kind == services.TOTP:
            return get_object_or_404(TOTPDevice, user=user, pk=int(public_id))
    except (ValueError, ValidationError) as exc:
        raise Http404 from exc
    raise Http404


@signed_in
@step_up_required()
@require_POST
def remove_device(request: HttpRequest, kind: str, public_id: str) -> HttpResponse:
    device = _own_device(_user(request), kind, public_id)
    try:
        services.remove_device(device, ctx=audit_context(request))
    except services.MfaPolicyError:
        messages.error(request, "Add another factor before removing your last one.")
    else:
        messages.success(request, f"{device.name} removed.")
    return HttpResponseRedirect(reverse("mfa:overview"))
