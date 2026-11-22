"""The anonymous garage cookie endpoints (D-38 Q-G1, FIT.6): *Save*, *Remove vehicle* and
*Forget my garage*. Thin adapters over `garage` and `services`. They write only the visitor's own
cookie: no server record, no log of its value."""

import uuid
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext, ngettext
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.audit import audit_context
from apps.core.i18n import path_carries_secret
from apps.core.public_forms import Protection, not_anonymous, public_form

from . import garage, garage_merge, garage_profiles, garage_state, services
from .models import GarageProfile

_PURPOSE = "Anonymous garage cookie (technical, written on Save only)"
_REASON = (
    "it writes only the visitor's own technical cookie (at most three make, model, year labels "
    "checked against the scheme); nothing is stored or logged on the server"
)
_NO_LIMIT = "no server-side work beyond a cookie write and two indexed lookups"


def _declared[T](view: T) -> T:
    return public_form(
        protection=Protection.ALLOW,
        purpose=_PURPOSE,
        task="FIT.6",
        reason=_REASON,
        no_rate_limit=_NO_LIMIT,
    )(view)


def _declared_fit7[T](view: T, *, purpose: str, reason: str, no_rate_limit: str) -> T:
    return public_form(
        protection=Protection.ALLOW,
        purpose=purpose,
        task="FIT.7",
        reason=reason,
        no_rate_limit=no_rate_limit,
    )(view)


def _declared_fit7_line[T](view: T) -> T:
    return _declared_fit7(
        view,
        purpose="Dismiss the Add your garage line (a flag in the visitor's own session)",
        reason="it stores one boolean in the visitor's session and redirects; nothing is logged",
        no_rate_limit="no server-side work beyond one session write and a redirect",
    )


def _back(request: HttpRequest) -> HttpResponseRedirect:
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        target = "/"
    return HttpResponseRedirect(target)


def _back_to_referer(request: HttpRequest) -> HttpResponseRedirect:
    """Back to the page the visitor came from: the Referer, only when it is a local address."""
    target = request.headers.get("Referer", "")
    if not url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        target = "/"
    return HttpResponseRedirect(target)


def _write(response: HttpResponse, value: garage.Garage) -> HttpResponse:
    if not value.vehicles or not services.anon_storage_enabled():  # off never writes the cookie
        response.delete_cookie(garage.COOKIE_NAME, samesite="Lax")
        return response
    response.set_cookie(
        garage.COOKIE_NAME,
        garage.dumps(value),
        max_age=garage.cookie_max_age(int(getattr(settings, "GARAGE_COOKIE_DAYS", 180))),
        path="/",
        secure=bool(settings.SESSION_COOKIE_SECURE),
        httponly=True,  # only the server reads it
        samesite="Lax",
    )
    return response


@_declared
@require_POST
def save_vehicle(request: HttpRequest) -> HttpResponse:
    """*Save*: the cookie is written here and nowhere else, never before the visitor asks."""
    if not services.anon_storage_enabled():
        raise Http404("the anonymous garage is off")
    try:
        vehicle = services.vehicle_from_labels(
            request.POST.get("make"), request.POST.get("model"), request.POST.get("year", "")
        )
        value = garage.save(services.read_garage(request), vehicle)
    except (services.FitmentError, garage.GarageFull):
        return HttpResponse(status=400)
    return _write(_back(request), value)


@_declared
@require_POST
def remove_vehicle(request: HttpRequest) -> HttpResponse:
    """*Remove vehicle*: one vehicle leaves the cookie at once (the cookie goes with the last)."""
    try:
        index = int(request.POST.get("index", ""))
    except ValueError:
        return HttpResponse(status=400)
    # Also when storage is off (the cookie is then deleted by `_write`); an invalid one is cleared.
    current = services.validated_garage(garage.parse(request.COOKIES.get(garage.COOKIE_NAME)))
    return _write(_back(request), garage.remove(current, index))


@_declared
@require_POST
def forget_garage(request: HttpRequest) -> HttpResponse:
    """*Forget my garage*: the cookie is deleted."""
    return _write(_back(request), garage.EMPTY)


def _safe_next(request: HttpRequest, value: str) -> str:
    allowed = url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return value if allowed else "/"


_RETURN_KEY = "fitment_garage_return"


def _remember_return(request: HttpRequest) -> None:
    """Keep the page the visitor came from, server-side, for the chooser's *Apply*: only the path
    and query of a same-host Referer, in the session. It is never written into the page (the
    Referer can be a secret-token URL). The chooser's own address is not kept."""
    referer = request.headers.get("Referer", "")
    session = getattr(request, "session", None)
    if session is None or not url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return
    parts = urlsplit(referer)
    if parts.path == request.path or path_carries_secret(parts.path):
        return  # not the chooser itself, and never an action link or key URL (a token)
    session[_RETURN_KEY] = parts.path + (f"?{parts.query}" if parts.query else "")


def _return_target(request: HttpRequest) -> HttpResponseRedirect:
    """Where *Apply* goes when no `next` is posted: the remembered page when the post comes from
    the chooser, else the Referer (the banner's *Show all*); `/` as a last resort."""
    session = getattr(request, "session", None)
    kept = session.pop(_RETURN_KEY, "") if session is not None else ""
    referer = request.headers.get("Referer", "")
    if (
        kept
        and urlsplit(referer).path == reverse("garage_choose")
        and url_has_allowed_host_and_scheme(
            kept, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        )
    ):
        return HttpResponseRedirect(kept)
    if urlsplit(referer).path == reverse("garage_choose"):
        return HttpResponseRedirect("/")  # nothing remembered: not back onto the chooser
    if path_carries_secret(urlsplit(referer).path):
        return HttpResponseRedirect("/")  # a single-use link is never a return target
    return _back_to_referer(request)


@_declared_fit7_line
@require_POST
def dismiss_garage_line(request: HttpRequest) -> HttpResponse:
    """*Dismiss* on the *Add your garage* line: a flag in the session, then back to the page the
    visitor came from (the Referer, kept off secret-token URLs and the chooser). No `next` is
    read or written: the page URL can hold a secret."""
    request.session[garage_state.LINE_DISMISSED_KEY] = True
    return _return_target(request)


@require_http_methods(["GET", "HEAD"])
def choose_garage(request: HttpRequest) -> HttpResponse:
    """The chooser (FIT.7): the profiles or cookie vehicles to pick from, with an explicit *Apply*.
    It changes nothing by itself; the page holds no garage state in its URL."""
    state = garage_state.garage_state(request)
    if not state:
        raise Http404("the garage is off")
    given = request.GET.get("next", "")
    _remember_return(request)
    response = render(
        request,
        "fitment/garage_choose.html",
        {**state, "next": _safe_next(request, given) if given else ""},
    )
    add_never_cache_headers(response)
    return response


@require_POST
def apply_garage(request: HttpRequest) -> HttpResponse:
    """*Apply*: post, redirect, announce. `choice` is `generic`, `profile:<uuid>` (a signed-in
    customer's own) or `vehicle:<index>` (the anonymous cookie)."""
    if not garage_state.garage_state(request):
        raise Http404("the garage is off")
    choice = request.POST.get("choice", "")
    # The banner's *Show all* posts no `next` (the page URL can hold a secret): the Referer then.
    response = _back(request) if request.POST.get("next") else _return_target(request)
    signed_in = garage_state.account_garage_on(request)
    kind, _, ref = choice.partition(":")
    if choice == "generic":
        if signed_in:
            garage_profiles.reset_garage(request.user, ctx=audit_context(request))  # type: ignore[arg-type]
        else:
            current = services.read_garage(request)
            _write(response, garage.Garage(current.vehicles, None))
        messages.success(request, gettext("Garage reset: showing all items."))
    elif kind == "profile" and signed_in:
        try:
            public_id = uuid.UUID(ref)
        except ValueError:
            return HttpResponse(status=400)
        profile = get_object_or_404(GarageProfile, user=request.user, public_id=public_id)
        garage_profiles.activate_profile(profile, ctx=audit_context(request))
        messages.success(
            request, gettext("%(name)s is now your active vehicle.") % {"name": profile}
        )
    elif kind == "vehicle" and not signed_in and ref.isdigit():
        current = services.read_garage(request)
        if int(ref) >= len(current.vehicles):
            return HttpResponse(status=400)
        _write(response, garage.Garage(current.vehicles, int(ref)))
        messages.success(
            request,
            gettext("%(name)s is now your active vehicle.")
            % {"name": garage_state.vehicle_label(current.vehicles[int(ref)])},
        )
    else:
        return HttpResponse(status=400)
    return response


choose_garage = _declared_fit7(
    choose_garage,
    purpose="Garage chooser: list the visitor's vehicles or profiles (a read)",
    reason="a read of the visitor's own cookie or, signed in, own profiles; it changes nothing",
    no_rate_limit="no write and no external call: one scheme read and indexed lookups",
)
apply_garage = _declared_fit7(
    apply_garage,
    purpose="Garage chooser: apply a vehicle (cookie, or an audited profile activation)",
    reason=(
        "it writes the visitor's own technical cookie, or, signed in, activates one of that "
        "customer's own profiles (an audited write through the profile service); nothing else "
        "is stored and nothing external is called"
    ),
    no_rate_limit="no server-side work beyond a cookie write or one audited profile update",
)


@not_anonymous(
    reason="login_required: a signed-out request is redirected to sign in",
    purpose="Garage merge notice: add the staged anonymous vehicles, or dismiss the notice",
    task="FIT.7",
)
@login_required
@require_POST
def merge_garage(request: HttpRequest) -> HttpResponse:
    """The sign-in notice's two buttons (spec 17 §17.4.3): `action=add` copies the staged vehicles
    into the account and clears the cookie; `action=dismiss` (*Not now*) only drops the notice.
    Both are explicit posts; nothing is ever merged without *Add*."""
    response = _back_to_referer(request)
    user = request.user
    action = request.POST.get("action", "")
    if action not in {"add", "dismiss"}:
        return HttpResponse(status=400)
    if action == "dismiss":
        garage_merge.dismiss_pending(request)
        return response
    if not garage_merge.pending_count(request):  # nothing staged: a replayed or stale Add
        return response
    result = garage_merge.merge_pending(request, user, ctx=audit_context(request))  # type: ignore[arg-type]
    garage_merge.clear_cookie(response)
    messages.success(
        request,
        ngettext(
            "%(count)d profile added to your account.",
            "%(count)d profiles added to your account.",
            result.added,
        )
        % {"count": result.added},
    )
    return response
