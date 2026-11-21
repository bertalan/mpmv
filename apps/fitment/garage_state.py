"""What the header garage menu and the chooser show (FIT.7 1/n, spec 17 §17.4.4).

One read of the visitor's garage: the signed-in customer's profiles, or the vehicles of the
anonymous cookie. Nothing here writes; the choice itself is `views.apply_garage`.
"""

from typing import Any

from django.conf import settings
from django.http import HttpRequest
from django.utils.translation import gettext

from . import garage, garage_profiles
from .services import FitmentError, current_scheme, validated_garage


def vehicle_label(vehicle: garage.Vehicle) -> str:
    return ", ".join(part for part in (vehicle.make, vehicle.model, vehicle.year) if part)


def account_garage_on(request: HttpRequest) -> bool:
    """Whether profiles back this request's garage (a signed-in customer, scheme not sensitive)."""
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and not current_scheme().contains_special_category)


_CACHE = "_garage_state"


def garage_state(request: HttpRequest) -> dict[str, Any]:
    """`noun`, `options` (value, label, active), `active_label`, `signed_in` and `has_vehicles`;
    empty when the garage is off for this visitor (no scheme row, or an anonymous visitor with the
    browser storage off)."""
    cached: dict[str, Any] | None = getattr(request, _CACHE, None)
    if cached is None:  # the header runs on every page: read once per request
        cached = _read(request)
        setattr(request, _CACHE, cached)
    return cached


def _read(request: HttpRequest) -> dict[str, Any]:
    try:
        scheme = current_scheme()
    except FitmentError:
        return {}
    options: list[dict[str, Any]] = []
    user = getattr(request, "user", None)
    signed_in = bool(user and user.is_authenticated and not scheme.contains_special_category)
    if signed_in:
        for profile in garage_profiles.profiles_of(request.user):  # type: ignore[arg-type]
            options.append(
                {
                    "value": f"profile:{profile.public_id}",
                    "label": profile.name,
                    "active": profile.is_active,
                }
            )
    elif (
        getattr(settings, "GARAGE_ANON_STORAGE", "exempt") == "exempt"
        and not scheme.contains_special_category
    ):  # `anon_storage_enabled` without a second scheme read
        value = validated_garage(garage.parse(request.COOKIES.get(garage.COOKIE_NAME)))
        for index, vehicle in enumerate(value.vehicles):
            options.append(
                {
                    "value": f"vehicle:{index}",
                    "label": vehicle_label(vehicle),
                    "active": index == value.active,
                }
            )
    else:
        return {}
    active = next((o["label"] for o in options if o["active"]), "")
    return {
        "noun": gettext(scheme.garage_noun_key),
        "options": options,
        "active_label": active,
        "signed_in": signed_in,
        "has_vehicles": bool(options),
    }
