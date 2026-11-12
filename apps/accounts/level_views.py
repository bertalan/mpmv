"""Passkey window endpoints (D-32, CORE.25): open with a fresh passkey, "do it once", close now.

Thin adapters over `levels`. The passkey ceremony is the one of the MFA pages, with user
verification required; the challenge is kept in the session and used once. A session without the
passkey (a stolen cookie) cannot pass it, and a code is never accepted.
"""

import re
import time

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.views.decorators.http import require_POST

from apps.core.audit import audit_context
from apps.core.public_forms import not_anonymous

from . import engine, levels, services
from .http import relying_party, safe_next
from .mfa_views import _bad_request, _json_body, _user, signed_in
from .models import PasskeyDevice


def _options(
    request: HttpRequest, session_key: str, binding: dict[str, str] | None
) -> JsonResponse:
    credential_ids = list(
        PasskeyDevice.objects.filter(user=_user(request), confirmed=True).values_list(
            "credential_id", flat=True
        )
    )
    if not credential_ids:
        return _bad_request("no passkey registered")
    options = engine.passkey_authentication_options(
        relying_party(request), credential_ids=credential_ids, require_user_verification=True
    )
    request.session[session_key] = {
        "challenge": engine.b64url(options.challenge),
        "issued_at": time.time(),
        **(binding or {}),
    }
    return JsonResponse({"publicKey": options.public_key})


def _assert(request: HttpRequest, session_key: str) -> dict[str, str] | JsonResponse:
    """Verify the posted assertion against the pending challenge; the pending state or an error."""
    pending = request.session.pop(session_key, None)
    body = _json_body(request)
    if not isinstance(pending, dict) or not isinstance(body.get("credential"), dict):
        return _bad_request("no pending passkey authentication")
    issued_at = pending.get("issued_at")
    if not isinstance(issued_at, int | float) or time.time() - issued_at > levels.CHALLENGE_TTL:
        return _bad_request("the passkey request expired")
    try:
        services.authenticate_passkey(
            _user(request),
            relying_party(request),
            body["credential"],
            challenge=engine.from_b64url(pending["challenge"]),
            require_user_verification=True,
        )
    except engine.PasskeyError:
        services.record_mfa_failure(_user(request), services.PASSKEY, ctx=audit_context(request))
        return _bad_request("the passkey was not accepted")
    return {k: v for k, v in pending.items() if k not in {"challenge", "issued_at"}}


def _require_elevate(request: HttpRequest) -> None:
    if not levels.may_elevate(request.user):
        raise PermissionDenied


# Sensitive and critical actions (groups 2 and 3) may be done once; a critical one only with its
# own fresh passkey assertion with user
# verification (every `_assert` requires it, bound to this action and digest, single use, 120 s).
ONCE_GROUPS = (2, 3)
DIGEST = re.compile(r"[0-9a-f]{64}")


@not_anonymous(
    reason="signed in, a verified second factor and the elevate permission are required",
    purpose="Passkey options to open the passkey window",
    task="CORE.25",
)
@signed_in
@require_POST
def options(request: HttpRequest) -> JsonResponse:
    """Passkey request options for opening the passkey window. A challenge alone grants
    nothing: `window` checks the role and verifies the assertion."""
    return _options(request, levels.CHALLENGE_KEY, None)


@not_anonymous(
    reason="signed in, a verified second factor and the elevate permission are required",
    purpose="Open the passkey window with a passkey",
    task="CORE.25",
)
@signed_in
@require_POST
def window(request: HttpRequest) -> JsonResponse:
    """Verify a fresh passkey assertion and open the passkey window."""
    _require_elevate(request)
    result = _assert(request, levels.CHALLENGE_KEY)
    if isinstance(result, JsonResponse):
        return result
    levels.open_window(request)
    return JsonResponse({"redirect": safe_next(request)})


@not_anonymous(
    reason="signed in, a verified second factor and the elevate permission are required",
    purpose="Passkey options for a single authorisation",
    task="CORE.25",
)
@signed_in
@require_POST
def once_options(request: HttpRequest) -> JsonResponse:
    """Passkey request options for one action, bound to it and its parameters."""
    _require_elevate(request)
    body = _json_body(request)
    action, digest = body.get("action"), body.get("digest")
    if not isinstance(action, str) or not isinstance(digest, str) or not action or not digest:
        return _bad_request("an action and its parameters digest are required")
    if not DIGEST.fullmatch(digest):
        return _bad_request("the parameters digest must be 64 lowercase hex characters")
    if levels.policy().group_of(action) not in ONCE_GROUPS:
        return _bad_request("this action cannot be done once")
    return _options(request, levels.SINGLE_CHALLENGE_KEY, {"action": action, "digest": digest})


@not_anonymous(
    reason="signed in, a verified second factor and the elevate permission are required",
    purpose="Authorise one action once with a passkey",
    task="CORE.25",
)
@signed_in
@require_POST
def once(request: HttpRequest) -> JsonResponse:
    """Verify a fresh passkey assertion and authorise the bound action once."""
    _require_elevate(request)
    result = _assert(request, levels.SINGLE_CHALLENGE_KEY)
    if isinstance(result, JsonResponse):
        return result
    if (
        levels.policy().group_of(result["action"]) not in ONCE_GROUPS
    ):  # the policy may have changed meanwhile
        return _bad_request("this action cannot be done once")
    levels.grant_single(request, result["action"], result["digest"])
    return JsonResponse({"granted": True})


@not_anonymous(
    reason="signed in, a verified second factor and the elevate permission are required",
    purpose="Close the passkey window",
    task="CORE.25",
)
@signed_in
@require_POST
def close(request: HttpRequest) -> HttpResponse:
    """The banner's "Close now" button."""
    levels.close_window(request, "manual")
    return HttpResponseRedirect(safe_next(request))
