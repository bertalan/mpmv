"""Vendor services: the only way to change a vendor's status (VEN.1)."""

from django.db import transaction

from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import run_mutation

from . import engine
from .models import Vendor

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
