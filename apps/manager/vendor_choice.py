"""The selected vendor of the shop space (CORE.17, spec 02 §Backoffice).

The choice is a vendor public id in the session, checked against the user's memberships on
every request. A value that matches none of them (forged, or a membership removed since) is
forgotten and answers 404, like a vendor id in the request that is not one of the user's.

A closed vendor is never selected (lane-a decision on #170): it is left out of the choices, and a
session value or a switch that names one of the user's closed vendors is forgotten and refused
with the closed-vendor page (see `refused_closed`).
"""

from collections.abc import Sequence

from django.http import Http404, HttpRequest

from apps.vendors.models import Vendor, VendorMember

SESSION_KEY = "manager.vendor"


def _find(memberships: Sequence[VendorMember], public_id: object) -> VendorMember | None:
    wanted = str(public_id)
    return next((m for m in memberships if str(m.vendor.public_id) == wanted), None)


def is_closed(membership: VendorMember) -> bool:
    return membership.vendor.status == Vendor.Status.CLOSED


def open_memberships(memberships: Sequence[VendorMember]) -> tuple[VendorMember, ...]:
    """The memberships the user may work on: every one but those of closed vendors."""
    return tuple(m for m in memberships if not is_closed(m))


def refused_closed(
    request: HttpRequest, memberships: Sequence[VendorMember], switch_to: str | None
) -> bool:
    """Whether this request names one of the user's closed vendors, in the session or as the
    vendor to switch to (`switch_to`). A closed vendor in the session is forgotten."""
    closed = [m for m in memberships if is_closed(m)]
    if not closed:
        return False
    stored = request.session.get(SESSION_KEY)
    if stored is not None and _find(closed, stored) is not None:
        del request.session[SESSION_KEY]
        return True
    return switch_to is not None and _find(closed, switch_to.strip()) is not None


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
