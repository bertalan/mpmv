"""Writing audit entries."""

from dataclasses import dataclass
from typing import Any

from .changeset import to_jsonable
from .models import AuditLog


@dataclass(frozen=True)
class AuditContext:
    """Who is acting and from where; passed explicitly by every adapter (view, API, command…)."""

    actor_label: str
    source: str
    actor_id: int | None = None
    ip: str | None = None
    request_id: str = ""


def record(
    ctx: AuditContext,
    action: str,
    target_model: str,
    target_key: str,
    before: Any,
    after: Any,
) -> AuditLog:
    return AuditLog.objects.create(
        actor_id=ctx.actor_id,
        actor_label=ctx.actor_label,
        source=ctx.source,
        ip=ctx.ip,
        request_id=ctx.request_id,
        action=action,
        target_model=target_model,
        target_key=target_key,
        before=to_jsonable(before),
        after=to_jsonable(after),
    )
