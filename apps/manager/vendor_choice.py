"""The selected vendor of the shop space (CORE.17, spec 02 §Backoffice).

The choice is a vendor public id in the session, checked against the user's memberships on
every request. A value that matches none of them (forged, or a membership removed since) is
forgotten and answers 404, like a vendor id in the request that is not one of the user's.
"""

from collections.abc import Sequence

from django.http import Http404, HttpRequest

from apps.vendors.models import VendorMember

SESSION_KEY = "manager.vendor"


def _find(memberships: Sequence[VendorMember], public_id: object) -> VendorMember | None:
    wanted = str(public_id)
    return next((m for m in memberships if str(m.vendor.public_id) == wanted), None)


def selected_membership(
    request: HttpRequest, memberships: Sequence[VendorMember]
) -> VendorMember | None:
    """The membership of the selected vendor; None when the user must still choose one.

    A member of a single vendor works on it without choosing.
    """
    stored = request.session.get(SESSION_KEY)
    if stored is None:
        if len(memberships) == 1:
            request.session[SESSION_KEY] = str(memberships[0].vendor.public_id)
            return memberships[0]
        return None
    found = _find(memberships, stored)
    if found is None:
        del request.session[SESSION_KEY]
        raise Http404("No vendor matches the given query.")
    return found


def choose(request: HttpRequest, memberships: Sequence[VendorMember], value: str) -> VendorMember:
    """Select the vendor with public id `value` among the user's own vendors, else 404."""
    found = _find(memberships, value.strip()) if value.strip() else None
    if found is None:
        raise Http404("No vendor matches the given query.")
    request.session[SESSION_KEY] = str(found.vendor.public_id)
    return found
