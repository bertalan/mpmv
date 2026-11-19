"""The JSON behind the cascading node picker of the Fits page (FIT.3 follow-up 2/2).

`GET /manager/shop/fitment/nodes/?parent=<uuid>` lists the approved children of a node (without
`parent`, the roots) for the picker's next select. The nodes are the platform's shared vocabulary,
not vendor data, so the only gate is the shop section of the Manager (members of a shop with the
Fits section; everyone else gets a 404 like any Manager page). Read-only, bounded to `LIMIT` rows.

`?q=<text>` (at least `MIN_QUERY` characters once normalised) is the typed search: active nodes
whose name (the published translation in the active language when the level translates names),
source name or alias contains the text, at most `SEARCH_LIMIT`, each with its path
"Make > Model > Variant". One bounded query plus one for the ancestors' names.
"""

import uuid
from typing import Any

from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.utils.translation import get_language

from apps.core import translations
from apps.fitment import engine
from apps.fitment.models import FitmentNode, NodeStatus

from .fitment_views import level_label
from .views import ManagerView

SECTION = "shop.fitment"
LIMIT = 100
SEARCH_LIMIT = 25
MIN_QUERY = 2
MAX_QUERY = 60


def _name(node: FitmentNode, language: str) -> str:
    """The node's name in `language` when its level translates names, else the source name."""
    if node.level.translate_names:
        row = translations.resolve(node.translations.all(), language).translation
        if row is not None and row.name:
            return str(row.name)
    return node.name


def _search(text: str, language: str) -> list[dict[str, Any]]:
    needle = engine.normalize_name(text)
    nodes = (
        FitmentNode.objects.filter(status=NodeStatus.ACTIVE)
        .filter(
            Q(normalized_name__contains=needle)
            | Q(
                Q(aliases__language__isnull=True) | Q(aliases__language=language),
                aliases__normalized__contains=needle,
            )
            | Q(
                level__translate_names=True,
                translations__language=language,
                translations__status=translations.PUBLISHED,
                translations__name__icontains=text,
            )
        )
        .distinct()
        .select_related("level")
        .prefetch_related("translations")
        .order_by("sort_key", "path")[:SEARCH_LIMIT]
    )
    found = list(nodes)
    steplen = FitmentNode.steplen
    prefixes = {n.path[:i] for n in found for i in range(steplen, len(n.path), steplen)}
    parents = {
        p.path: p
        for p in FitmentNode.objects.filter(path__in=prefixes)
        .select_related("level")
        .prefetch_related("translations")
    }
    results = []
    for node in found:
        chain = [parents.get(node.path[:i]) for i in range(steplen, len(node.path), steplen)]
        if any(p is None or p.status != NodeStatus.ACTIVE for p in chain):
            continue
        names = [_name(p, language) for p in chain if p is not None] + [_name(node, language)]
        results.append(
            {
                "id": str(node.public_id),
                "name": names[-1],
                "path": " > ".join(names),
                "has_children": node.numchild > 0,
            }
        )
    return results


class FitmentNodeChildren(ManagerView):
    """The approved children of one node: `{label, results: [{id, name, has_children}], more}`."""

    section = SECTION

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        text = request.GET.get("q", "").strip()
        if text:
            if len(engine.normalize_name(text)) < MIN_QUERY or len(text) > MAX_QUERY:
                return JsonResponse({"error": "invalid query"}, status=400)
            response = JsonResponse({"results": _search(text, get_language() or "en")})
            response["Cache-Control"] = "private, no-store"
            return response
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
