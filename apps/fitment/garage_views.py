"""The "My garage" page of the customer area (FIT.5 2/3, spec 17 §17.4.1 and §17.5.6).

Thin adapter over `garage_profiles`. Every view needs a signed-in customer and starts from that
customer's own profiles: another account's profile id is a 404, the same answer as for an unknown
id. Actions are POSTs (CSRF) that redirect back to the page; no state is ever put in a URL. With
the scheme's `contains_special_category` switch on, the account garage is unavailable (404).
"""

import uuid
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.utils.translation import gettext
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.models import User
from apps.core.audit import audit_context
from apps.core.public_forms import not_anonymous

from . import engine, garage_profiles
from .models import GarageProfile
from .services import FitmentError, current_scheme

type View = Callable[..., HttpResponse]

_REASON = "signed in: the customer changes only their own garage profiles"


def _signed_in(view: View) -> View:
    return login_required(login_url=settings.MFA_LOGIN_URL)(view)


def _customer(request: HttpRequest) -> User:
    if current_scheme().contains_special_category:
        raise Http404("the account garage is unavailable")
    return request.user  # type: ignore[return-value]


def _own(request: HttpRequest, public_id: uuid.UUID) -> GarageProfile:
    user = _customer(request)
    return get_object_or_404(
        GarageProfile.objects.select_related("node"), user=user, public_id=public_id
    )


def _back() -> HttpResponseRedirect:
    return HttpResponseRedirect(reverse("garage_account"))


def _path(profile: GarageProfile) -> str:
    labels = [*(a.name for a in profile.node.get_ancestors()), profile.node.name]
    if profile.year is not None:
        labels.append(str(profile.year))
    return " / ".join(labels)


def page_context(user: User) -> dict[str, Any]:
    rows = garage_profiles.profiles_of(user)
    maximum = current_scheme().max_profiles
    return {
        "profiles": [
            {
                "profile": p,
                "path": _path(p),
                "retired": p.node_retired,
                "delete_url": reverse("garage_profile_delete", args=[p.public_id]),
            }
            for p in rows
        ],
        "has_active": any(p.is_active for p in rows),
        "count": len(rows),
        "maximum": maximum,
        "full": len(rows) >= maximum,
    }


@never_cache
@_signed_in
@require_http_methods(["GET", "HEAD"])
def garage_page(request: HttpRequest) -> HttpResponse:
    return render(request, "fitment/garage_account.html", page_context(_customer(request)))


@not_anonymous(
    reason=_REASON, purpose="Back to the generic garage (profiles are kept)", task="FIT.5"
)
@never_cache
@_signed_in
@require_POST
def reset(request: HttpRequest) -> HttpResponse:
    garage_profiles.reset_garage(_customer(request), ctx=audit_context(request))
    messages.success(request, gettext("Your garage is reset. Your profiles are kept."))
    return _back()


@not_anonymous(reason=_REASON, purpose="Make a garage profile the active one", task="FIT.5")
@never_cache
@_signed_in
@require_POST
def activate(request: HttpRequest, public_id: uuid.UUID) -> HttpResponse:
    profile = _own(request, public_id)
    garage_profiles.activate_profile(profile, ctx=audit_context(request))
    messages.success(request, gettext("%(name)s is now your active vehicle.") % {"name": profile})
    return _back()


@not_anonymous(reason=_REASON, purpose="Rename a garage profile", task="FIT.5")
@never_cache
@_signed_in
@require_POST
def rename(request: HttpRequest, public_id: uuid.UUID) -> HttpResponse:
    profile = _own(request, public_id)
    name = engine.clean_name(request.POST.get("name", ""))
    if not name:
        messages.error(request, gettext("A profile needs a name."))
    elif len(name) > engine.MAX_PROFILE_NAME:
        messages.error(
            request,
            gettext("A profile name has at most %(max)d characters.")
            % {"max": engine.MAX_PROFILE_NAME},
        )
    else:
        garage_profiles.rename_profile(profile, name, ctx=audit_context(request))
        messages.success(request, gettext("Profile renamed."))
    return _back()


@not_anonymous(reason=_REASON, purpose="Duplicate a garage profile", task="FIT.5")
@never_cache
@_signed_in
@require_POST
def duplicate(request: HttpRequest, public_id: uuid.UUID) -> HttpResponse:
    profile = _own(request, public_id)
    maximum = current_scheme().max_profiles
    if engine.profile_limit_error(len(garage_profiles.profiles_of(profile.user)), maximum):
        messages.error(
            request,
            gettext("Your garage holds at most %(max)d profiles: delete one first.")
            % {"max": maximum},
        )
        return _back()
    try:
        garage_profiles.duplicate_profile(profile, ctx=audit_context(request))
    except FitmentError:  # a concurrent request filled the garage between the check and the write
        messages.error(request, gettext("The profile could not be duplicated."))
    else:
        messages.success(request, gettext("Profile duplicated."))
    return _back()


@not_anonymous(
    reason=_REASON, purpose="Delete a garage profile (asks for confirmation)", task="FIT.5"
)
@never_cache
@_signed_in
@require_http_methods(["GET", "POST"])
def delete(request: HttpRequest, public_id: uuid.UUID) -> HttpResponse:
    """GET shows the confirmation step; only a POST that carries it deletes."""
    profile = _own(request, public_id)
    if request.method == "POST" and request.POST.get("confirm") == "yes":
        name = str(profile)
        garage_profiles.delete_profile(profile, confirmed=True, ctx=audit_context(request))
        messages.success(request, gettext("%(name)s is deleted.") % {"name": name})
        return _back()
    response = render(
        request,
        "fitment/garage_delete.html",
        {"profile": profile, "path": _path(profile), "back_url": reverse("garage_account")},
    )
    add_never_cache_headers(response)
    return response
