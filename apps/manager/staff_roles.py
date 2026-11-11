"""Assigning platform staff roles (CORE.18 2/2, spec 02 §Role-permission matrix).

The one service behind "Staff and roles" in the Manager platform space. Only the platform owner
assigns (the matrix row `platform.staff_roles`); the technical superuser works in the Django
admin instead. Rules enforced here, whatever the adapter:

- platform roles and vendor membership never mix: a vendor member is refused;
- incompatible pairs (`roles.INCOMPATIBLE_PAIRS`) are refused, self-assignment included, unless
  the audited one-person-team setting (feature flag `one_person_team`, BO-13) is on;
- the last platform owner is never removed;
- an owner giving themselves another role must state a reason, and the other platform owners and
  the superusers get an email (BO-13);
- role holders get `is_staff` (admin access); it goes away with the last role, unless the account
  has other groups, permissions or superuser status that need it.

Step-up (`role_grant`, and `owner_role_grant` for the owner role) is a session concern and sits in
the view, as for the Django admin. Every mutation takes `dry_run=True` and writes the AuditLog.
"""

import uuid
from dataclasses import replace
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from apps.core.audit import AuditContext, user_context
from apps.core.changeset import ChangeSet
from apps.core.models import PLATFORM_OWNER_CODENAME
from apps.core.services import enqueue_email, flag_enabled, run_mutation
from apps.vendors.services import is_vendor_member

from . import roles
from .role_groups import effective_roles, user_roles

GRANT_ACTION = "manager.staff_role.grant"
REVOKE_ACTION = "manager.staff_role.revoke"
USER_MODEL = "accounts.user"
ONE_PERSON_TEAM = "one_person_team"
SELF_ASSIGNED_EMAIL = "manager.role_self_assigned"


class RoleAssignmentError(ValueError):
    """A rule of the assignment was not met; the message is meant for the owner to read."""


def _snapshot(
    user: Any, held: frozenset[str], is_staff: bool, reason: str | None = None
) -> dict[str, object]:
    data: dict[str, object] = {"roles": sorted(held), "is_staff": is_staff}
    if reason:
        data["reason"] = reason
    return data


def _authorise(actor: Any) -> None:
    if not roles.allows("platform.staff_roles", effective_roles(actor), write=True):
        raise PermissionDenied


def _audit_context(actor: Any, ctx: AuditContext) -> AuditContext:
    return replace(user_context(actor, ctx.ip), request_id=ctx.request_id)


def _check_role(role_key: str) -> None:
    if role_key not in roles.ROLE_KEYS:
        raise RoleAssignmentError(f"Unknown role {role_key!r}.")


def platform_owners() -> Any:
    """Active users holding the platform owner permission, directly or through a group."""
    return (
        get_user_model()
        .objects.filter(is_active=True)
        .filter(
            Q(user_permissions__codename=PLATFORM_OWNER_CODENAME)
            | Q(groups__permissions__codename=PLATFORM_OWNER_CODENAME)
        )
        .distinct()
    )


def _holds_owner_directly(user: Any) -> bool:
    return bool(user.user_permissions.filter(codename=PLATFORM_OWNER_CODENAME).exists())


def grant_role(
    target: Any,
    role_key: str,
    *,
    actor: Any,
    ctx: AuditContext,
    reason: str = "",
    dry_run: bool = False,
) -> ChangeSet:
    """Give `target` the platform role `role_key`; a role already held is a no-op."""
    _authorise(actor)
    _check_role(role_key)
    reason = reason.strip()
    if not target.is_active:
        raise RoleAssignmentError("That account is inactive.")
    if is_vendor_member(target):
        raise RoleAssignmentError(
            "That account is a vendor member. Platform roles and vendor membership never mix."
        )
    before = user_roles(target) | (
        frozenset({roles.OWNER}) if _holds_owner_directly(target) else frozenset()
    )
    if role_key in before:
        return ChangeSet()
    after = before | {role_key}
    pairs = roles.incompatible(after)
    if pairs and not flag_enabled(ONE_PERSON_TEAM):
        names = "; ".join(" + ".join(roles.role(k).label for k in pair) for pair in pairs)
        raise RoleAssignmentError(
            f"{names} cannot be held by one person unless a superuser switches on the "
            "one-person-team setting."
        )
    self_assigned = target.pk == actor.pk
    if self_assigned and not reason:
        raise RoleAssignmentError("Giving yourself a role needs a reason.")
    changes = ChangeSet()
    changes.update(
        USER_MODEL,
        str(target.public_id),
        _snapshot(target, before, target.is_staff),
        _snapshot(target, after, True, reason or None),
    )

    def apply() -> None:
        target.groups.add(Group.objects.get(name=roles.role(role_key).group))
        if not target.is_staff:
            target.is_staff = True
            target.save(update_fields=["is_staff"])
        if self_assigned:
            _notify_self_assignment(actor, role_key, reason)

    return run_mutation(
        changes, apply, ctx=_audit_context(actor, ctx), action=GRANT_ACTION, dry_run=dry_run
    )


def revoke_role(
    target: Any,
    role_key: str,
    *,
    actor: Any,
    ctx: AuditContext,
    reason: str = "",
    dry_run: bool = False,
) -> ChangeSet:
    """Take the platform role `role_key` from `target`; a role not held is a no-op."""
    _authorise(actor)
    _check_role(role_key)
    group = Group.objects.get(name=roles.role(role_key).group)
    if not target.groups.filter(pk=group.pk).exists():
        return ChangeSet()
    before = user_roles(target) | (
        frozenset({roles.OWNER}) if _holds_owner_directly(target) else frozenset()
    )
    if (
        role_key == roles.OWNER
        and not _holds_owner_directly(target)
        and not platform_owners().exclude(pk=target.pk).exists()
    ):
        raise RoleAssignmentError("The last platform owner cannot be removed or demoted.")
    remaining = before - {role_key}
    others = target.groups.exclude(pk=group.pk).exists() or target.user_permissions.exists()
    clears_staff = not remaining and not others and not target.is_superuser and target.is_staff
    changes = ChangeSet()
    changes.update(
        USER_MODEL,
        str(target.public_id),
        _snapshot(target, before, target.is_staff),
        _snapshot(target, remaining, target.is_staff and not clears_staff, reason.strip() or None),
    )

    def apply() -> None:
        target.groups.remove(group)
        if clears_staff:
            target.is_staff = False
            target.save(update_fields=["is_staff"])

    return run_mutation(
        changes, apply, ctx=_audit_context(actor, ctx), action=REVOKE_ACTION, dry_run=dry_run
    )


def _notify_self_assignment(actor: Any, role_key: str, reason: str) -> None:
    """ARC.16 email to the other platform owners and the superusers (BO-13)."""
    user_model = get_user_model()
    recipients = {u.pk: u for u in platform_owners().exclude(pk=actor.pk)}
    for user in user_model.objects.filter(is_active=True, is_superuser=True).exclude(pk=actor.pk):
        recipients[user.pk] = user
    for user in recipients.values():
        enqueue_email(
            recipient=user.email,
            template_key=SELF_ASSIGNED_EMAIL,
            language=getattr(user, "preferred_language", "en"),
            context={
                "actor_name": actor.full_name or actor.email,
                "role": roles.role(role_key).label,
                "reason": reason,
            },
            idempotency_key=f"role-self-assigned:{uuid.uuid4()}",
        )


def staff_members() -> list[tuple[Any, frozenset[str]]]:
    """Every active account holding a platform role, with its roles, for the page."""
    user_model = get_user_model()
    found = user_model.objects.filter(
        Q(groups__name__in=roles.group_names())
        | Q(user_permissions__codename=PLATFORM_OWNER_CODENAME)
    ).distinct()
    rows = [(u, effective_roles(u)) for u in found.order_by("email")]
    return [(u, r) for u, r in rows if r]


__all__ = [
    "GRANT_ACTION",
    "ONE_PERSON_TEAM",
    "REVOKE_ACTION",
    "RoleAssignmentError",
    "grant_role",
    "revoke_role",
    "staff_members",
]
