"""Merging the anonymous garage into the account at sign-in (D-38, FIT.6, spec 17 §17.4.3).

There is no silent merge: a shared computer must not copy a previous visitor's garage into a new
account. At sign-in the receiver only *stages* the vehicles in the visitor's server-side session
(`stage_on_login`); the customer's *Add* (the notice is FIT.7's UI) calls `merge_pending`, which
creates the profiles through `garage_profiles` and tells the caller to clear the cookie. *Not now*
calls `dismiss_pending`: the cookie stays and the next sign-in asks again.

Nothing here may break a sign-in: the receiver catches and logs every error. The audit summary
holds counts only, never a vehicle label (they are the visitor's personal data).
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit import AuditContext, audit_context, record
from apps.core.changeset import ChangeSet

from . import engine, garage, garage_profiles, services
from .models import FitmentNode, GarageProfile, NodeStatus

logger = logging.getLogger(__name__)

SESSION_KEY = "fitment_garage_pending"
MERGED = "fitment.garage.merged"
CLEAR_ATTR = "_fitment_clear_garage_cookie"


@dataclass(frozen=True)
class MergeResult:
    added: int
    skipped: int  # already in the account, not in the catalogue any more, or over the limit
    changes: ChangeSet = field(default_factory=ChangeSet)  # the profiles created (or to create)


def _node_for(vehicle: garage.Vehicle) -> FitmentNode | None:
    root = FitmentNode.objects.filter(
        depth=1, status=NodeStatus.ACTIVE, normalized_name=engine.normalize_name(vehicle.make)
    ).first()
    if root is None:
        return None
    child: FitmentNode | None = (
        root.get_children()
        .filter(status=NodeStatus.ACTIVE, normalized_name=engine.normalize_name(vehicle.model))
        .first()
    )
    return child


def _year_of(vehicle: garage.Vehicle) -> int | None:
    if services.current_scheme().year_enabled and vehicle.year.isdigit():
        return int(vehicle.year)
    return None


def _new_vehicles(request: HttpRequest, user: User) -> garage.Garage:
    """The cookie's vehicles the account lacks (same node and year is the same); empty when the
    storage is off or the scheme holds sensitive items."""
    current = services.read_garage(request)
    known = {(p.node_id, p.year) for p in GarageProfile.objects.filter(user=user)}
    fresh: list[garage.Vehicle] = []
    active: int | None = None
    for index, vehicle in enumerate(current.vehicles):
        node = _node_for(vehicle)
        if node is None or (node.pk, _year_of(vehicle)) in known:
            continue
        if index == current.active:
            active = len(fresh)
        fresh.append(vehicle)
    return garage.Garage(tuple(fresh), active) if fresh else garage.EMPTY


def stage_on_login(
    sender: Any, request: HttpRequest | None, user: AbstractBaseUser, **kwargs: Any
) -> None:
    """`user_logged_in` receiver: remember in the session that the cookie holds vehicles the
    account lacks. It adds nothing to the account. Never raises."""
    if request is None or not isinstance(user, User) or not hasattr(request, "session"):
        return
    try:
        request.session.pop(SESSION_KEY, None)
        fresh = _new_vehicles(request, user)
        if fresh.vehicles:
            request.session[SESSION_KEY] = {
                "vehicles": [v.as_dict() for v in fresh.vehicles],
                "active": fresh.active,
            }
    except Exception:  # any failure: the sign-in goes on without the notice
        logger.exception("Could not stage the anonymous garage for user %s", user.pk)


def pending_count(request: HttpRequest) -> int:
    """How many vehicles the merge notice offers (0: no notice)."""
    staged = request.session.get(SESSION_KEY)
    if not isinstance(staged, dict) or not isinstance(staged.get("vehicles"), list):
        return 0
    return len(staged["vehicles"])


def dismiss_pending(request: HttpRequest) -> None:
    """*Not now*: nothing is merged, the cookie stays; the next sign-in offers it again."""
    request.session.pop(SESSION_KEY, None)


def flag_sign_out(
    sender: Any, request: HttpRequest | None, user: AbstractBaseUser | None, **kwargs: Any
) -> None:
    """`user_logged_out` receiver (spec 17 §17.4.3): the garage cookie goes with the sign-out only
    when it was created from the account's active profile, that is when it holds exactly that
    vehicle (same node and year). The flag is read by `GarageSignOutMiddleware`, which clears the
    cookie on the response. Holds no identifier. Never raises, never logs the cookie."""
    if request is None or not isinstance(user, User):
        return
    try:
        profile = GarageProfile.objects.filter(user=user, is_active=True).first()
        current = services.read_garage(request)
        if profile is None or len(current.vehicles) != 1:
            return
        vehicle = current.vehicles[0]
        node = _node_for(vehicle)
        if node is not None and (node.pk, _year_of(vehicle)) == (profile.node_id, profile.year):
            setattr(request, CLEAR_ATTR, True)
    except Exception:  # sign-out must succeed: the cookie then simply stays
        logger.exception("Could not check the garage cookie at sign-out for user %s", user.pk)


def clear_cookie(response: HttpResponse) -> HttpResponse:
    response.delete_cookie(garage.COOKIE_NAME, path="/", samesite="Lax")
    return response


def _unique_name(base: str, taken: list[str]) -> str:
    used = {engine.normalize_name(name) for name in taken}
    candidate, number = base, 2
    while engine.normalize_name(candidate) in used:
        suffix = f" {number}"
        candidate = base[: engine.MAX_PROFILE_NAME - len(suffix)].rstrip() + suffix
        number += 1
    return candidate


def merge_pending(
    request: HttpRequest,
    user: User,
    *,
    ctx: AuditContext | None = None,
    dry_run: bool = False,
) -> MergeResult:
    """*Add*: copy the staged vehicles into the account's profiles, within the profile limit. The
    cookie's active vehicle becomes active only when the account has no active profile. The
    caller then clears the cookie (`clear_cookie`). The staged vehicles are re-checked against
    the cookie, so a stale session entry cannot add what the browser no longer holds.

    `dry_run=True` returns what *Add* would do (`MergeResult.changes`) and writes nothing: no
    profile, no audit entry, and the session entry stays."""
    ctx = ctx or audit_context(request)
    if not dry_run:
        request.session.pop(SESSION_KEY, None)
    fresh = _new_vehicles(request, user)
    total = len(fresh.vehicles)
    changes = ChangeSet()
    added = 0
    has_active = GarageProfile.objects.filter(user=user, is_active=True).exists()
    maximum = services.current_scheme().max_profiles
    count = GarageProfile.objects.filter(user=user).count()
    taken = list(GarageProfile.objects.filter(user=user).values_list("name", flat=True))
    for index, vehicle in enumerate(fresh.vehicles):
        node = _node_for(vehicle)
        if node is None or count >= maximum:
            continue
        year = _year_of(vehicle)
        name = _unique_name(engine.default_profile_name(garage_profiles._labels(node), year), taken)
        make_active = not has_active and index == fresh.active
        try:
            applied = garage_profiles.create_profile(
                user, node, name=name, year=year, activate=make_active, ctx=ctx, dry_run=dry_run
            )
        except services.FitmentError:
            continue
        changes.changes.extend(applied.changes.changes)
        added += 1
        count += 1
        taken.append(name)
        has_active = has_active or make_active
    result = MergeResult(added=added, skipped=total - added, changes=changes)
    if not dry_run:
        record(
            ctx,
            MERGED,
            garage_profiles.MODEL,
            str(user.pk),
            None,
            {"added": result.added, "skipped": result.skipped, "at": timezone.now().isoformat()},
        )
    return result
