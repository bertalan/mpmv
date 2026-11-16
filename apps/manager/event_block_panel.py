"""The block panel of the vendor's event form (EVT.4 part 2): each block of the latest revision
with its actions, as the template shows it."""

from typing import Any

from django.urls import reverse

from apps.events import engine, services
from apps.events.models import EventPage

from .event_block_forms import FORMS

PANEL_ID = "event-blocks"


def block_types() -> list[tuple[str, Any]]:
    return [(kind, FORMS[kind].block_label) for kind in sorted(FORMS)]


def blocks_context(page: EventPage) -> dict[str, Any]:
    """What `_event_blocks.html` shows: each block of the latest revision, with its actions."""
    body = services.event_body(page)
    labels = dict(block_types())
    rows = []
    for number, block in enumerate(body, start=1):
        kind, block_id = str(block.get("type")), str(block.get("id"))
        args = [page.public_id, block_id]
        label = str(labels.get(kind, kind))
        rows.append(
            {
                "number": number,
                "id": block_id,
                "label": label,
                "summary": engine.block_summary(block),
                "edit_url": reverse("manager:shop:event_block_edit", args=args),
                "move_url": reverse("manager:shop:event_block_move", args=args),
                "remove_url": reverse("manager:shop:event_block_remove", args=args),
            }
        )
    return {
        "event": page,
        "blocks": rows,
        "block_types": block_types(),
        "add_url": reverse("manager:shop:event_block_add", args=[page.public_id]),
        "panel_id": PANEL_ID,
    }
