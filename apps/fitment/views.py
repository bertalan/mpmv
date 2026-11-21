"""The anonymous garage cookie endpoints (D-38 Q-G1, FIT.6): *Save*, *Remove vehicle* and
*Forget my garage*. Thin adapters over `garage` and `services`. They write only the visitor's own
cookie: no server record, no log of its value."""

import uuid

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.utils.cache import add_never_cache_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext, ngettext
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.audit import audit_context
from apps.core.public_forms import Protection, public_form

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


def _back(request: HttpRequest) -> HttpResponseRedirect:
    target = request.POST.get("next", "")
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


@require_http_methods(["GET", "HEAD"])
def choose_garage(request: HttpRequest) -> HttpResponse:
    """The chooser (FIT.7): the profiles or cookie vehicles to pick from, with an explicit *Apply*.
    It changes nothing by itself; the page holds no garage state in its URL."""
    state = garage_state.garage_state(request)
    if not state:
        raise Http404("the garage is off")
    response = render(
        request,
        "fitment/garage_choose.html",
        {**state, "next": _safe_next(request, request.GET.get("next", ""))},
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
    response = _back(request)
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


@require_POST
def merge_garage(request: HttpRequest) -> HttpResponse:
    """The sign-in notice's two buttons (spec 17 §17.4.3): `action=add` copies the staged vehicles
    into the account and clears the cookie; `action=dismiss` (*Not now*) only drops the notice.
    Both are explicit posts; nothing is ever merged without *Add*."""
    response = _back(request)
    user = request.user
    action = request.POST.get("action", "")
    if not user.is_authenticated or action not in {"add", "dismiss"}:
        return HttpResponse(status=400)
    if action == "dismiss":
        garage_merge.dismiss_pending(request)
        return response
    result = garage_merge.merge_pending(request, user, ctx=audit_context(request))
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


merge_garage = _declared_fit7(
    merge_garage,
    purpose="Garage merge notice: add the staged anonymous vehicles, or dismiss the notice",
    reason=(
        "a signed-in customer's explicit Add copies vehicles staged from their own browser cookie "
        "into their own profiles (audited, within the profile limit); Not now writes nothing"
    ),
    no_rate_limit="no external call: a bounded number of indexed lookups and at most the limit",
)
