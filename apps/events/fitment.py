"""What an event is tagged with and who it is for (FIT.11, spec 17 §17.5.5).

The tags live on the page the organiser edits; a translation reads the original-language page's
tags, so one edit covers every language. The garage never changes what is listed here: it only
decides whether the badge shows. Feeds and the sitemap never call this.
"""

from django.db.models import F
from django.http import HttpRequest

from apps.fitment.garage_nodes import active_node, ancestor_paths
from apps.fitment.models import FitmentNode

from .models import EventFitment, EventPage

_CACHE = "_event_fitment_keys"


def nodes_of(page: EventPage) -> list[FitmentNode]:
    """The nodes the event is tagged with (empty: a general event)."""
    rows = EventFitment.objects.filter(
        page__translation_key=page.translation_key, page__locale=F("page__original_locale")
    ).select_related("node")
    return [row.node for row in rows]


def _matching_keys(request: HttpRequest) -> frozenset[object]:
    cached: frozenset[object] | None = getattr(request, _CACHE, None)
    if cached is None:
        node = active_node(request)
        if node is None:
            cached = frozenset()
        else:
            cached = frozenset(
                EventFitment.objects.filter(
                    node__path__in=ancestor_paths(node), page__locale=F("page__original_locale")
                ).values_list("page__translation_key", flat=True)
            )
        setattr(request, _CACHE, cached)
    return cached


def matches_garage(request: HttpRequest, page: EventPage) -> bool:
    """Whether the active garage is covered by one of the event's tags: a tag on the vehicle's
    node or on an ancestor of it. One query per request, however long the list."""
    return page.translation_key in _matching_keys(request)
