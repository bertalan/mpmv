"""A vendor's return policy: propose, staff review, and the public accessor (VEN.40).

The owner proposes a version; it is private until platform staff approve it, so the structured
data of SEO.14 never states what the sales terms of VEN.35 do not say. A new proposal never
changes a stored one: it adds the next version and withdraws the previous pending one. The
newest approved version is the public one; a vendor with none is on the default of
`return_engine.default_policy` (the 14-day floor, the shop pays the return shipping).
"""

from collections.abc import Mapping
from typing import Any, Final

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import run_mutation

from . import return_engine as engine
from .models import ReturnPolicyVersion, Vendor
from .services import PROFILE_LOCKED, can_reopen, has_vendor_permission

PROPOSED: Final = "vendor.return_policy.proposed"
APPROVED: Final = "vendor.return_policy.approved"
REJECTED: Final = "vendor.return_policy.rejected"
MODEL: Final = "vendors.returnpolicyversion"

Status = ReturnPolicyVersion.Status


def _as_policy(version: ReturnPolicyVersion) -> engine.ReturnPolicy:
    return engine.ReturnPolicy(
        return_window_days=version.return_window_days,
        return_shipping_paid_by=version.return_shipping_paid_by,
        return_methods=tuple(version.return_methods),
        returns_page_url=version.returns_page_url,
        return_country=str(version.return_country),
    )


def public_return_policy(vendor: Vendor) -> engine.ReturnPolicy | None:
    """The newest staff-approved policy of `vendor`, or None. This is what SEO.14 states.

    Pending, rejected and replaced versions are never public.
    """
    version = (
        ReturnPolicyVersion.objects.filter(vendor=vendor, status=Status.APPROVED)
        .order_by("-number")
        .first()
    )
    return None if version is None else _as_policy(version)


def effective_return_policy(vendor: Vendor) -> engine.ReturnPolicy:
    """`public_return_policy`, or the default when the vendor has no approved data."""
    return public_return_policy(vendor) or engine.default_policy(str(vendor.country))


def latest_version(vendor: Vendor) -> ReturnPolicyVersion | None:
    """The newest version of any status, for the owner's own page."""
    return ReturnPolicyVersion.objects.filter(vendor=vendor).order_by("-number").first()


def propose_return_policy(
    vendor: Vendor,
    values: Mapping[str, Any],
    *,
    actor: User,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """The vendor owner proposes the next version of its return policy (pending staff review).

    Owners only (`profile` in the role matrix); a suspended or closed vendor cannot. Invalid
    values (a window under 14 days, a non-https page, no method) raise `ValidationError` with
    one message per field, also on dry-run. The previous pending version, if any, is withdrawn.
    """
    if not has_vendor_permission(actor, vendor, "profile"):
        raise PermissionDenied("Only the vendor owner proposes the return policy.")
    unknown = sorted(set(values) - set(engine.FIELDS))
    if unknown:
        raise ValidationError(f"Not return policy fields: {', '.join(unknown)}.")
    errors = engine.policy_errors(values)
    if errors:
        raise ValidationError(dict(errors))
    changes = ChangeSet()
    with transaction.atomic():
        locked = Vendor.objects.select_for_update().get(pk=vendor.pk)
        if locked.status in PROFILE_LOCKED:
            raise PermissionDenied("A suspended or closed vendor cannot change its policy.")
        last = (
            ReturnPolicyVersion.objects.filter(vendor=vendor)
            .order_by("-number")
            .values_list("number", flat=True)
            .first()
        )
        version = ReturnPolicyVersion(
            vendor=vendor,
            number=(last or 0) + 1,
            return_window_days=int(values["return_window_days"]),
            return_shipping_paid_by=str(values["return_shipping_paid_by"]),
            return_methods=[m for m in engine.METHODS if m in values["return_methods"]],
            returns_page_url=str(values.get("returns_page_url") or "").strip(),
            return_country=str(values["return_country"]),
        )
        previous = ReturnPolicyVersion.objects.filter(vendor=vendor, status=Status.PENDING)
        changes.create(MODEL, f"{vendor.public_id}:{version.number}", version.snapshot())

        def apply() -> None:
            previous.update(status=Status.WITHDRAWN, updated_at=timezone.now())
            version.save()

        run_mutation(changes, apply, ctx=ctx, action=PROPOSED, dry_run=dry_run)
    return changes


def review_return_policy(
    version: ReturnPolicyVersion,
    *,
    approve: bool,
    actor: User,
    ctx: AuditContext,
    note: str = "",
    dry_run: bool = False,
) -> ChangeSet:
    """Platform staff approve or reject a pending version (never a vendor member as such).

    Raises `PermissionDenied` (see `vendors.services.can_reopen`: active staff with the vendor
    change permission) and `ValidationError` for a version that is not pending.
    """
    if not can_reopen(actor):
        raise PermissionDenied("Only platform staff review a return policy.")
    with transaction.atomic():
        current = ReturnPolicyVersion.objects.select_for_update().get(pk=version.pk)
        if current.status != Status.PENDING:
            raise ValidationError("Only a version waiting for review can be reviewed.")
        target = Status.APPROVED if approve else Status.REJECTED
        before = current.snapshot()
        changes = ChangeSet()
        changes.update(
            MODEL,
            f"{current.vendor.public_id}:{current.number}",
            before,
            {**before, "status": target, "review_note": note.strip()},
        )

        def apply() -> None:
            current.status = target
            current.reviewed_by = actor
            current.reviewed_at = timezone.now()
            current.review_note = note.strip()
            current.save(
                update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"]
            )

        run_mutation(
            changes, apply, ctx=ctx, action=APPROVED if approve else REJECTED, dry_run=dry_run
        )
    return changes
