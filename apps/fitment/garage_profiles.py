"""Account garage profiles (D-38, FIT.5, spec 17 §17.4.1): the services.

A signed-in customer keeps up to `FitmentScheme.max_profiles` profiles (default 10), one active
at most. Reset clears the active flag and keeps every profile; deleting is the only way to lose
one and needs `confirmed=True`. Every mutation accepts `dry_run=True` (the `ChangeSet` comes
back, nothing is written) and writes `AuditLog` entries. Audit snapshots hold the node's public
id, the year and the flag, never the profile name (free text, and the audit table is
append-only: it could not be erased with the account).

Callers (the "My garage" page, FIT.5 2/3) check that the profile belongs to the signed-in user.
A merge moves profiles through `apps.fitment.references`; a retired node keeps them.
"""

from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import run_mutation

from . import engine
from .models import FitmentNode, GarageProfile
from .services import Applied, FitmentError, _lock_scheme, _year_bounds, current_scheme

PROFILE_CREATED = "fitment.garage_profile.created"
PROFILE_UPDATED = "fitment.garage_profile.updated"
PROFILE_ACTIVATED = "fitment.garage_profile.activated"
PROFILE_RESET = "fitment.garage_profile.reset"
PROFILE_DELETED = "fitment.garage_profile.deleted"
MODEL = "fitment.garageprofile"


def profiles_of(user: User) -> list[GarageProfile]:
    return list(GarageProfile.objects.filter(user=user).select_related("node"))


def retired_profiles(user: User) -> list[GarageProfile]:
    """The user's profiles whose node was retired: shown with "no longer in the catalogue"."""
    return [p for p in profiles_of(user) if p.node_retired]


def _snapshot(profile: GarageProfile) -> dict[str, Any]:
    return {
        "node": str(profile.node.public_id),
        "year": profile.year,
        "is_active": profile.is_active,
    }


def _checked_name(name: str) -> str:
    cleaned = engine.clean_name(name)
    if error := engine.profile_name_error(cleaned):
        raise FitmentError(error)
    return cleaned


def _lock_user(user: User) -> None:
    """Serialise one customer's profile writes (limit and single active flag)."""
    User.objects.select_for_update().get(pk=user.pk)


def _check_limit(user: User) -> None:
    count = GarageProfile.objects.filter(user=user).count()
    if error := engine.profile_limit_error(count, current_scheme().max_profiles):
        raise FitmentError(error)


def _labels(node: FitmentNode) -> list[str]:
    return [*(a.name for a in node.get_ancestors()), node.name]


def _deactivate_others(user: User, changeset: ChangeSet, *, keep: int | None) -> None:
    for other in GarageProfile.objects.filter(user=user, is_active=True).select_related("node"):
        if other.pk != keep:
            changeset.update(
                MODEL,
                str(other.public_id),
                _snapshot(other),
                {**_snapshot(other), "is_active": False},
            )


def create_profile(
    user: User,
    node: FitmentNode,
    *,
    name: str = "",
    year: int | None = None,
    activate: bool = False,
    ctx: AuditContext,
    dry_run: bool = False,
) -> Applied[GarageProfile]:
    """Save `node` (and a year, if the scheme uses one) as a profile, optionally active."""
    with transaction.atomic():
        _lock_scheme()
        _lock_user(user)
        node = FitmentNode.objects.select_for_update().get(pk=node.pk)
        if node.status != engine.ACTIVE:
            raise FitmentError("Only an item offered in the garage can be saved.")
        scheme = current_scheme()
        low, high = _year_bounds(scheme)
        if error := engine.profile_year_error(
            year, enabled=scheme.year_enabled, low=low, high=high
        ):
            raise FitmentError(error)
        _check_limit(user)
        cleaned = _checked_name(name or engine.default_profile_name(_labels(node), year))
        changeset = ChangeSet()
        if activate:
            _deactivate_others(user, changeset, keep=None)
        key = f"{user.pk}:{node.public_id}"
        changeset.create(
            MODEL, key, {"node": str(node.public_id), "year": year, "is_active": activate}
        )
        applied: Applied[GarageProfile] = Applied(changeset)

        def apply() -> None:
            if activate:
                GarageProfile.objects.filter(user=user, is_active=True).update(is_active=False)
            applied.obj = GarageProfile.objects.create(
                user=user, name=cleaned, node=node, year=year, is_active=activate
            )

        applied.changes = run_mutation(
            changeset, apply, ctx=ctx, action=PROFILE_CREATED, dry_run=dry_run
        )
        return applied


def rename_profile(
    profile: GarageProfile, name: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    cleaned = _checked_name(name)
    changeset = ChangeSet()
    changeset.update(
        MODEL,
        str(profile.public_id),
        {"name_length": len(profile.name)},
        {"name_length": len(cleaned)},
    )

    def apply() -> None:
        profile.name = cleaned
        profile.save(update_fields=["name", "updated_at"])

    return run_mutation(changeset, apply, ctx=ctx, action=PROFILE_UPDATED, dry_run=dry_run)


def duplicate_profile(
    profile: GarageProfile, *, ctx: AuditContext, dry_run: bool = False
) -> Applied[GarageProfile]:
    """A copy with the same node and year, named "<name> (copy)", never active."""
    user = profile.user
    with transaction.atomic():
        _lock_user(user)
        _check_limit(user)
        taken = list(GarageProfile.objects.filter(user=user).values_list("name", flat=True))
        new_name = engine.copy_name(profile.name, taken)
        changeset = ChangeSet()
        changeset.create(
            MODEL,
            f"{user.pk}:{profile.node.public_id}",
            {**_snapshot(profile), "is_active": False, "copy_of": str(profile.public_id)},
        )
        applied: Applied[GarageProfile] = Applied(changeset)

        def apply() -> None:
            applied.obj = GarageProfile.objects.create(
                user=user, name=new_name, node=profile.node, year=profile.year
            )

        applied.changes = run_mutation(
            changeset, apply, ctx=ctx, action=PROFILE_CREATED, dry_run=dry_run
        )
        return applied


def activate_profile(
    profile: GarageProfile, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Make `profile` the active one (the previous active profile is switched off)."""
    user = profile.user
    with transaction.atomic():
        _lock_user(user)
        profile.refresh_from_db()
        changeset = ChangeSet()
        _deactivate_others(user, changeset, keep=profile.pk)
        if not profile.is_active:
            changeset.update(
                MODEL,
                str(profile.public_id),
                _snapshot(profile),
                {**_snapshot(profile), "is_active": True},
            )

        def apply() -> None:
            GarageProfile.objects.filter(user=user, is_active=True).exclude(pk=profile.pk).update(
                is_active=False
            )
            profile.is_active = True
            profile.last_used_at = timezone.now()
            profile.save(update_fields=["is_active", "last_used_at", "updated_at"])

        return run_mutation(changeset, apply, ctx=ctx, action=PROFILE_ACTIVATED, dry_run=dry_run)


def reset_garage(user: User, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Back to the generic state: no profile active, every profile kept."""
    with transaction.atomic():
        _lock_user(user)
        changeset = ChangeSet()
        _deactivate_others(user, changeset, keep=None)

        def apply() -> None:
            GarageProfile.objects.filter(user=user, is_active=True).update(is_active=False)

        return run_mutation(changeset, apply, ctx=ctx, action=PROFILE_RESET, dry_run=dry_run)


def delete_profile(
    profile: GarageProfile, *, confirmed: bool, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """The only way to lose a profile; refused unless the customer confirmed."""
    if not confirmed:
        raise FitmentError("Deleting a profile needs a confirmation.")
    changeset = ChangeSet()
    changeset.delete(MODEL, str(profile.public_id), _snapshot(profile))
    return run_mutation(changeset, profile.delete, ctx=ctx, action=PROFILE_DELETED, dry_run=dry_run)
