"""The dry-run + audit pattern shared by all mutating services (spec 01 conventions)."""

from collections.abc import Callable

from django.db import transaction

from .audit import AuditContext, record
from .changeset import ChangeSet


def run_mutation(
    changeset: ChangeSet,
    apply: Callable[[], None],
    *,
    ctx: AuditContext,
    action: str,
    dry_run: bool,
) -> ChangeSet:
    """Return the ChangeSet; unless dry-run, apply it and audit every change atomically."""
    if dry_run or changeset.is_empty:
        return changeset
    with transaction.atomic():
        apply()
        for change in changeset.changes:
            record(ctx, action, change.model, change.key, change.before, change.after)
    return changeset
