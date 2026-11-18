"""The JSON behind the cascading node picker of the Fits page (FIT.3 follow-up 2/2).

`GET /manager/shop/fitment/nodes/?parent=<uuid>` lists the approved children of a node (without
`parent`, the roots) for the picker's next select. The nodes are the platform's shared vocabulary,
not vendor data, so the only gate is the shop section of the Manager (members of a shop with the
Fits section; everyone else gets a 404 like any Manager page). Read-only, bounded to `LIMIT` rows.
"""

import uuid
from typing import Any

from django.http import Http404, HttpRequest, HttpResponse, JsonResponse

from apps.fitment.models import FitmentNode, NodeStatus

from .fitment_views import level_label
from .views import ManagerView

SECTION = "shop.fitment"
LIMIT = 100


class FitmentNodeChildren(ManagerView):
    """The approved children of one node: `{label, results: [{id, name, has_children}], more}`."""

    section = SECTION

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        raw = request.GET.get("parent", "")
        rows = FitmentNode.objects.filter(status=NodeStatus.ACTIVE).select_related("level")
        if raw:
            try:
                parent_id = uuid.UUID(raw)
            except ValueError:
                raise Http404("No such node.") from None
            parent = rows.filter(public_id=parent_id).first()
            if parent is None:
                raise Http404("No such node.")
            rows = rows.filter(path__startswith=parent.path, depth=parent.depth + 1)
        else:
            rows = rows.filter(depth=1)
        found = list(rows.prefetch_related("level__translations")[: LIMIT + 1])
        shown = found[:LIMIT]
        response = JsonResponse(
            {
                "label": level_label(shown[0].level) if shown else "",
                "results": [
                    {"id": str(n.public_id), "name": n.name, "has_children": n.numchild > 0}
                    for n in shown
                ],
                "more": len(found) > LIMIT,
            }
        )
        response["Cache-Control"] = "private, no-store"
        return response
