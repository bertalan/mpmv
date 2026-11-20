"""What the header garage menu and the chooser show (FIT.7 1/n, spec 17 §17.4.4).

One read of the visitor's garage: the signed-in customer's profiles, or the vehicles of the
anonymous cookie. Nothing here writes; the choice itself is `views.apply_garage`.
"""

from typing import Any

from django.http import HttpRequest
from django.utils.translation import gettext

from . import garage, garage_profiles
from .services import FitmentError, anon_storage_enabled, current_scheme, read_garage


def vehicle_label(vehicle: garage.Vehicle) -> str:
    return ", ".join(part for part in (vehicle.make, vehicle.model, vehicle.year) if part)


def account_garage_on(request: HttpRequest) -> bool:
    """Whether profiles back this request's garage (a signed-in customer, scheme not sensitive)."""
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and not current_scheme().contains_special_category)


def garage_state(request: HttpRequest) -> dict[str, Any]:
    """`noun`, `options` (value, label, active), `active_label`, `signed_in` and `has_vehicles`;
    empty when the garage is off for this visitor (no scheme row, or an anonymous visitor with the
    browser storage off)."""
    try:
        scheme = current_scheme()
    except FitmentError:
        return {}
    options: list[dict[str, Any]] = []
    signed_in = account_garage_on(request)
    if signed_in:
        for profile in garage_profiles.profiles_of(request.user):  # type: ignore[arg-type]
            options.append(
                {
                    "value": f"profile:{profile.public_id}",
                    "label": profile.name,
                    "active": profile.is_active,
                }
            )
    elif anon_storage_enabled():
        value = read_garage(request)
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
