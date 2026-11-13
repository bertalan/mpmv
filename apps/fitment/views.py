"""The anonymous garage cookie endpoints (D-38 Q-G1, FIT.6): *Save*, *Remove vehicle* and
*Forget my garage*. Thin adapters over `garage` and `services`. They write only the visitor's own
cookie: no server record, no log of its value."""

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core.public_forms import Protection, public_form

from . import garage, services

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
