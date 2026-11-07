"""Vendor services: status changes (VEN.1) and memberships with their permissions (CORE.2)."""

from typing import Any

from django.db import transaction

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import run_mutation

from . import engine
from .models import Vendor, VendorMember

STATUS_CHANGED = "vendor.status_changed"
MODEL_LABEL = "vendors.vendor"


def change_status(
    vendor: Vendor, target: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Move `vendor` to `target` if the state machine allows it; audit the change.

    The check runs against the stored status (row locked), never a possibly stale instance.
    Raises `engine.InvalidTransition`, also on dry-run.
    """
    with transaction.atomic():
        current = (
            Vendor.objects.select_for_update().only("status", "slug", "public_id").get(pk=vendor.pk)
        )
        engine.check_transition(current.status, target)

        # Keyed by public_id: the slug is editable and can be reused by another vendor, so it
        # would not identify the vendor across its audit history. The slug is kept for reading.
        changes = ChangeSet()
        changes.update(
            MODEL_LABEL,
            str(current.public_id),
            before={"status": current.status, "slug": current.slug},
            after={"status": str(target), "slug": current.slug},
        )

        def apply() -> None:
            current.status = target
            current.save(update_fields=["status", "updated_at"])

        run_mutation(changes, apply, ctx=ctx, action=STATUS_CHANGED, dry_run=dry_run)

    if not dry_run:
        vendor.status = target
    return changes


# --- Memberships (CORE.2)

MEMBER_MODEL = "vendors.vendormember"
MEMBER_ADDED = "vendor.member_added"
MEMBER_ROLE_CHANGED = "vendor.member_role_changed"
MEMBER_REMOVED = "vendor.member_removed"


def member_snapshot(member: VendorMember) -> dict[str, Any]:
    """Public ids only: audit rows must stay readable after users or vendors change."""
    return {
        "vendor": str(member.vendor.public_id),
        "user": str(member.user.public_id),
        "role": member.role,
    }


def _lock_vendor(vendor: Vendor) -> Vendor:
    """Lock the vendor row: membership changes of one vendor run one at a time, so two
    concurrent removals cannot each see "another owner remains" and leave none."""
    return Vendor.objects.select_for_update().only("public_id").get(pk=vendor.pk)


def _roles(vendor: Vendor, *, excluding: VendorMember | None = None) -> list[str]:
    members = VendorMember.objects.filter(vendor=vendor)
    if excluding is not None:
        members = members.exclude(pk=excluding.pk)
    return list(members.values_list("role", flat=True))


def add_member(
    vendor: Vendor, user: User, role: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Make `user` a member of `vendor` with `role`; a vendor's first member must be an owner.

    Raises `engine.UnknownRole`, `engine.AlreadyMember` or `engine.NoOwnerLeft`, also on dry-run.
    """
    engine.check_role(role)
    with transaction.atomic():
        locked = _lock_vendor(vendor)
        if VendorMember.objects.filter(vendor=locked, user=user).exists():
            raise engine.AlreadyMember(f"{user} is already a member of vendor {locked.public_id}")
        engine.check_keeps_an_owner([*_roles(locked), role])

        member = VendorMember(vendor=locked, user=user, role=role)
        changes = ChangeSet()
        changes.create(MEMBER_MODEL, str(member.public_id), member_snapshot(member))
        run_mutation(changes, member.save, ctx=ctx, action=MEMBER_ADDED, dry_run=dry_run)
    return changes


def change_member_role(
    member: VendorMember, role: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Give `member` a new role; demoting the vendor's last owner is refused.

    The check runs against the stored roles (vendor locked), never a possibly stale instance.
    Raises `engine.UnknownRole` or `engine.NoOwnerLeft`, also on dry-run.
    """
    engine.check_role(role)
    with transaction.atomic():
        _lock_vendor(member.vendor)
        current = VendorMember.objects.select_related("vendor", "user").get(pk=member.pk)
        engine.check_keeps_an_owner([*_roles(current.vendor, excluding=current), role])

        before = member_snapshot(current)
        changes = ChangeSet()
        changes.update(MEMBER_MODEL, str(current.public_id), before, {**before, "role": role})

        def apply() -> None:
            current.role = role
            current.save(update_fields=["role", "updated_at"])

        run_mutation(changes, apply, ctx=ctx, action=MEMBER_ROLE_CHANGED, dry_run=dry_run)

    if not dry_run:
        member.role = role
    return changes


def remove_member(member: VendorMember, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """End `member`'s membership; removing the vendor's last owner is refused.

    Raises `engine.NoOwnerLeft`, also on dry-run.
    """
    with transaction.atomic():
        _lock_vendor(member.vendor)
        current = VendorMember.objects.select_related("vendor", "user").get(pk=member.pk)
        engine.check_keeps_an_owner(_roles(current.vendor, excluding=current))

        changes = ChangeSet()
        changes.delete(MEMBER_MODEL, str(current.public_id), member_snapshot(current))

        def apply() -> None:
            current.delete()

        run_mutation(changes, apply, ctx=ctx, action=MEMBER_REMOVED, dry_run=dry_run)
    return changes


# --- Permissions (CORE.2)


def member_role(user: User, vendor: Vendor) -> str | None:
    """The user's role in `vendor`, or None when they are not a member."""
    return (
        VendorMember.objects.filter(user=user, vendor=vendor).values_list("role", flat=True).first()
    )


def has_vendor_permission(user: User, vendor: Vendor, action: str) -> bool:
    """Whether `user` may perform `action` on `vendor` as one of its members.

    Only membership counts: platform staff reach vendors through the staff admin, not this
    check. Inactive users get nothing. An unknown action raises `engine.UnknownAction`.
    """
    checked = engine.to_action(action)
    if not user.is_active:
        return False
    role = member_role(user, vendor)
    return role is not None and engine.is_allowed(role, checked)
