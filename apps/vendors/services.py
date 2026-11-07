"""Vendor services: vendor data and status changes (VEN.1, SEC.4), memberships with their
permissions (CORE.2) and the legal identity with its payout requirements (VEN.12)."""

from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import audit_changes, run_mutation

from . import engine
from .models import LegalFieldRequirement, Vendor, VendorMember
from .vies import VatNumberValidator

STATUS_CHANGED = "vendor.status_changed"
CREATED = "vendor.created"
UPDATED = "vendor.updated"
DELETED = "vendor.deleted"
MODEL_LABEL = "vendors.vendor"

# Everything staff can see about a vendor's identity, tax position and contacts (SEC.4), the
# whole legal identity included (VEN.12).
VENDOR_FIELDS = (
    "legal_name",
    "display_name",
    "slug",
    "status",
    "country",
    *engine.LEGAL_FIELDS,
    "contact_email",
    "phone",
    "sales_mode_default",
    "enquiries_enabled",
    "legacy_wp_user_id",
)


def vendor_snapshot(vendor: Vendor) -> dict[str, Any]:
    """The audited fields of `vendor`, with the IBAN masked (it is sensitive, SEC.3)."""
    snapshot = {name: getattr(vendor, name) for name in VENDOR_FIELDS}
    snapshot["iban"] = engine.mask_iban(vendor.iban)
    return snapshot


def save_vendor(vendor: Vendor, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Validate, create or update `vendor`, auditing its data with before/after (keyed by public
    id, IBAN masked). `before` is read from the database with the row locked.

    Incomplete legal data is always accepted: completeness is checked when a payout is
    requested (`assert_payout_allowed`), never here. A value that is present must be valid:
    `full_clean` (field rules plus `engine.identity_errors`) runs for every adapter, also on
    dry-run, and raises `ValidationError`; it also stores identifiers compact. The status is
    not changed here: it moves only through `change_status`.
    """
    vendor.normalize()
    vendor.full_clean()
    key = str(vendor.public_id)
    with transaction.atomic():
        stored = (
            None
            if vendor._state.adding
            else Vendor.objects.select_for_update().filter(pk=vendor.pk).first()
        )
        after = vendor_snapshot(vendor)
        changes = ChangeSet()
        if stored is None:
            changes.create(MODEL_LABEL, key, after)
        else:
            before = vendor_snapshot(stored)
            if stored.iban != vendor.iban and before["iban"] == after["iban"]:
                after["iban"] += " (replaced)"  # same mask, different IBAN
            changes.update(MODEL_LABEL, key, before, after)
        if dry_run:
            return changes
        vendor.save()
        audit_changes(changes, ctx=ctx, action=UPDATED if stored else CREATED)
    return changes


def delete_vendor(vendor: Vendor, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Delete `vendor` with its translations and memberships; the vendor and each membership
    are audited (translations are storefront texts, not audited)."""
    with transaction.atomic():
        members = list(VendorMember.objects.select_related("vendor", "user").filter(vendor=vendor))
        changes = ChangeSet()
        changes.delete(MODEL_LABEL, str(vendor.public_id), vendor_snapshot(vendor))
        for member in members:
            changes.delete(MEMBER_MODEL, str(member.public_id), member_snapshot(member))
        run_mutation(changes, vendor.delete, ctx=ctx, action=DELETED, dry_run=dry_run)
    return changes


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


def is_vendor_member(user: AbstractBaseUser | AnonymousUser) -> bool:
    """Whether `user` is a member (owner or staff) of at least one vendor.

    Listed in `settings.MFA_REQUIRED_POLICIES`: everyone who reaches a store's management area
    must use MFA (CORE.4, owner decision Q-09), whatever their role.
    """
    if not user.is_authenticated:
        return False
    return VendorMember.objects.filter(user_id=user.pk).exists()


# --- Legal and e-invoicing identity (VEN.12)

VAT_CHECKED = "vendor.vat_checked"


def check_vat_number(
    vendor: Vendor, validator: VatNumberValidator, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Ask VIES (through `validator`) about the vendor's VAT number; store and audit the answer.

    Raises `ValueError` when the vendor has no VAT number or the validator answers something
    that is not a VIES result.
    """
    if not vendor.vat_number:
        raise ValueError(f"vendor {vendor.public_id} has no VAT number to check")
    _, number = engine.split_vat_number(vendor.vat_number, vendor.country)
    status = validator.check(engine.vat_prefix(vendor.country), number)
    if status not in engine.VIES_RESULTS:
        raise ValueError(f"unexpected VIES result {status!r}")
    checked_at = timezone.now()

    def state(vies_status: str, at: Any) -> dict[str, Any]:
        return {"vat_number": vendor.vat_number, "vies_status": vies_status, "vies_checked_at": at}

    changes = ChangeSet()
    changes.update(
        MODEL_LABEL,
        str(vendor.public_id),
        state(vendor.vies_status, vendor.vies_checked_at),
        state(status, checked_at),
    )

    def apply() -> None:
        Vendor.objects.filter(pk=vendor.pk).update(
            vies_status=status, vies_checked_at=checked_at, updated_at=checked_at
        )

    run_mutation(changes, apply, ctx=ctx, action=VAT_CHECKED, dry_run=dry_run)
    if not dry_run:
        vendor.vies_status = status
        vendor.vies_checked_at = checked_at
    return changes


def requirement_rules(purpose: str = engine.PAYOUT) -> list[engine.Requirement]:
    """The active `LegalFieldRequirement` rows for `purpose`, as pure rules."""
    rows = LegalFieldRequirement.objects.filter(active=True, purpose=purpose)
    return [row.as_rule() for row in rows]


def legal_profile_completeness(vendor: Vendor, purpose: str = engine.PAYOUT) -> engine.Completeness:
    """Which fields required for `purpose` the vendor is missing, or holds invalid values for."""
    return engine.completeness(vendor.legal_identity(), requirement_rules(purpose), purpose=purpose)


def assert_payout_allowed(vendor: Vendor) -> None:
    """Guard for payout requests (VEN.6, VEN.7): raise `engine.PayoutNotAllowed` listing the
    required legal fields that are missing or invalid.

    Checks the stored vendor, not unsaved changes on the instance.
    """
    stored = Vendor.objects.get(pk=vendor.pk)
    result = legal_profile_completeness(stored, engine.PAYOUT)
    if not result.complete:
        raise engine.PayoutNotAllowed(str(stored), result)


REQUIREMENT_MODEL = "vendors.legalfieldrequirement"
REQUIREMENT_SAVED = "vendor.legal_requirement_saved"
REQUIREMENT_DELETED = "vendor.legal_requirement_deleted"
REQUIREMENT_FIELDS = (
    "country",
    "region",
    "legal_form",
    "field_name",
    "purpose",
    "active",
    "note",
)


def requirement_snapshot(requirement: LegalFieldRequirement) -> dict[str, Any]:
    return {name: getattr(requirement, name) for name in REQUIREMENT_FIELDS}


def save_requirement(
    requirement: LegalFieldRequirement, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Create or change a requirement rule (validated, audited). Raises `ValidationError`."""
    requirement.full_clean()
    with transaction.atomic():
        stored = (
            LegalFieldRequirement.objects.select_for_update().filter(pk=requirement.pk).first()
            if requirement.pk
            else None
        )
        changes = ChangeSet()
        key = str(requirement.public_id)
        if stored is None:
            changes.create(REQUIREMENT_MODEL, key, requirement_snapshot(requirement))
        else:
            changes.update(
                REQUIREMENT_MODEL,
                key,
                requirement_snapshot(stored),
                requirement_snapshot(requirement),
            )
        return run_mutation(
            changes, requirement.save, ctx=ctx, action=REQUIREMENT_SAVED, dry_run=dry_run
        )


def delete_requirement(
    requirement: LegalFieldRequirement, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Delete a requirement rule (audited). Deactivating it keeps the history visible."""
    changes = ChangeSet()
    changes.delete(REQUIREMENT_MODEL, str(requirement.public_id), requirement_snapshot(requirement))

    def apply() -> None:
        requirement.delete()

    return run_mutation(changes, apply, ctx=ctx, action=REQUIREMENT_DELETED, dry_run=dry_run)
