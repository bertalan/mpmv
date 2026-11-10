"""Secure action link pages (CORE.15, spec 02 "Secure action links"). Thin adapters over
`services`.

- GET or HEAD on the emailed link (what a mail scanner prefetches) changes nothing: it shows a
  "Continue" page, or sends the recipient who is already signed in straight to the action page.
- The Continue POST (CSRF, rate limited per IP) carries the token in its body, not its URL. It
  spends the token's single sign-in use and signs the recipient in without a password; the
  action page then asks for the MFA step-up before its own POST. A recipient with no confirmed
  second factor, and a privileged recipient (staff, superuser, a group or a permission), gets
  no passwordless sign-in.
- An email-bound link (VEN.24, no account yet) signs nobody in: the Continue POST opens it in
  the session and leads to its page, which checks it again (`services.email_bound_link`).
- A usable link leads to the action page with the link's version (`?version=`); the page's own
  POST re-checks it with `services.action_version_matches`.
- A refused link (expired, used, revoked, another user's, an old version, no second factor, a
  staff recipient) leads to the normal login, then to the same action page; the service audits
  the attempt when it names a known account.

Every response carries `Referrer-Policy: strict-origin` (a Referer is only ever the bare origin,
never the path with the token) and is never cached, so the token does not leak to another site,
to our own Referer logs or to a shared cache. The token is in the link's path, so the request
logs redact it: `django.request` through `mpmv.log_redaction`, gunicorn's access log through
`mpmv.gunicorn_logging`. Nothing here logs the token itself.
"""

import math

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render, resolve_url
from django.utils.cache import add_never_cache_headers
from django.utils.translation import gettext, gettext_noop
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.audit import audit_context
from apps.core.http import client_ip
from apps.core.i18n import language_switch_home

from . import services
from .decorators import mfa_exempt
from .models import User

MAGIC_SIGN_IN_BACKEND = "django.contrib.auth.backends.ModelBackend"
TOO_MANY_ATTEMPTS = gettext_noop("Too many attempts. Please try again later.")
# Never a path in the Referer (the token is in the link's path), but still a real `Origin` on
# the Continue POST: with `no-referrer` browsers send `Origin: null` and the CSRF check fails.
REFERRER_POLICY = "strict-origin"


def _private(response: HttpResponse) -> HttpResponse:
    response["Referrer-Policy"] = REFERRER_POLICY
    response["X-Robots-Tag"] = "noindex, nofollow"
    add_never_cache_headers(response)
    return response


def _session_user(request: HttpRequest) -> User | None:
    user = request.user
    return user if isinstance(user, User) and user.is_authenticated else None


@language_switch_home
@mfa_exempt
@require_http_methods(["GET", "HEAD"])
def open_link(request: HttpRequest, token: str) -> HttpResponse:
    user = _session_user(request)
    check = services.check_action_token(token)
    if user is not None and check.refusal is None and check.user == user and check.action_url:
        return _private(HttpResponseRedirect(services.action_page_url(check)))
    context = {"token": token, "email_bound": check.email_bound}
    return _private(render(request, "accounts/action_link/continue.html", context))


@mfa_exempt
@require_POST
def continue_link(request: HttpRequest) -> HttpResponse:
    wait = services.action_link_retry_after(client_ip(request))
    if wait:
        retry = str(max(1, math.ceil(wait.total_seconds())))
        return _private(
            HttpResponse(gettext(TOO_MANY_ATTEMPTS), status=429, headers={"Retry-After": retry})
        )
    session_user = _session_user(request)
    ctx = audit_context(request)
    check = services.redeem_action_token(
        request.POST.get("token", ""), session_user=session_user, ctx=ctx
    )
    if check.email_bound and check.action_url:
        # VEN.24: nobody is signed in. A valid link is opened in the session; a refused one
        # (audited) still leads to its page, which explains that the link is no longer valid.
        # Opening is audited too, never with the token.
        if check.refusal is None:
            services.open_email_bound_link(request.session, check, ctx=ctx)
        return _private(HttpResponseRedirect(check.action_url))
    if check.refusal is not None or check.user is None or not check.action_url:
        login_url = resolve_url(settings.MFA_LOGIN_URL)
        return _private(redirect_to_login(check.action_url or "/", login_url))
    if session_user is None:
        login(request, check.user, backend=MAGIC_SIGN_IN_BACKEND)
    return _private(HttpResponseRedirect(services.action_page_url(check)))
