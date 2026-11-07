"""Writing audit entries (spec 01 conventions, spec 12 SEC.7; SEC.4).

Every sensitive mutation writes one entry per changed object: who, what, before/after and from
where. Services go through `apps.core.services.run_mutation`, which audits a ChangeSet; events
that are not a ChangeSet (logins, verifications) call `record` directly. The rules for new
models and services are in docs/dev/audit-trail.md.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .changeset import to_jsonable
from .models import AuditLog

# Snapshots of vendor-owned objects name their vendor under this key (its public id).
VENDOR_FIELD = "vendor"
VENDOR_MODEL = "vendors.vendor"


@dataclass(frozen=True)
class AuditContext:
    """Who is acting and from where; passed explicitly by every adapter (view, API, command…)."""

    actor_label: str
    source: str
    actor_id: int | None = None
    ip: str | None = None
    request_id: str = ""


def vendor_key_of(target_model: str, target_key: str, before: Any, after: Any) -> str:
    """The public id of the vendor an entry belongs to; "" for platform-level objects.

    A vendor is its own vendor; any other object names it in its snapshot (`"vendor": id`).
    """
    if target_model == VENDOR_MODEL:
        return target_key
    for snapshot in (after, before):
        if isinstance(snapshot, Mapping):
            value = snapshot.get(VENDOR_FIELD)
            if isinstance(value, str) and value:
                return value
    return ""


def record(
    ctx: AuditContext,
    action: str,
    target_model: str,
    target_key: str,
    before: Any,
    after: Any,
) -> AuditLog:
    before, after = to_jsonable(before), to_jsonable(after)
    return AuditLog.objects.create(
        actor_id=ctx.actor_id,
        actor_label=ctx.actor_label,
        source=ctx.source,
        ip=ctx.ip,
        request_id=ctx.request_id,
        action=action,
        target_model=target_model,
        target_key=target_key,
        before=before,
        after=after,
        vendor_key=vendor_key_of(target_model, target_key, before, after)[:64],
    )
