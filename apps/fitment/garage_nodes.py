"""The node the visitor's active garage stands for (FIT.11, spec 17 §17.5.5).

Events (and later other editorial content) tag themselves with nodes; this answers "which node is
the visitor's vehicle?" without exposing the garage itself: a signed-in customer's active profile
node, or the model node of the anonymous cookie's active vehicle. Read-only, cached per request,
`None` for the generic state, an off garage or a retired node. Nothing here is stored or logged.
"""

from django.http import HttpRequest

from . import engine, garage_state
from .models import FitmentNode, GarageProfile, NodeStatus
from .services import FitmentError, read_garage

_CACHE = "_garage_active_node"


def active_node(request: HttpRequest) -> FitmentNode | None:
    """The active vehicle's node (a profile's node at any depth, a cookie vehicle's model)."""
    if hasattr(request, _CACHE):
        cached: FitmentNode | None = getattr(request, _CACHE)
        return cached
    node = _read(request)
    setattr(request, _CACHE, node)
    return node


def _read(request: HttpRequest) -> FitmentNode | None:
    state = garage_state.garage_state(request)
    if not state or not state["active_label"]:
        return None
    if garage_state.account_garage_on(request):
        profile = (
            GarageProfile.objects.filter(user=request.user, is_active=True)  # type: ignore[misc]
            .select_related("node")
            .first()
        )
        node = profile.node if profile is not None else None
    else:
        node = _cookie_node(request)
    return node if node is not None and node.status == NodeStatus.ACTIVE else None


def _cookie_node(request: HttpRequest) -> FitmentNode | None:
    try:
        value = read_garage(request)
    except FitmentError:
        return None
    if value.active is None or not value.vehicles:
        return None
    vehicle = value.vehicles[value.active]
    root = FitmentNode.objects.filter(
        depth=1, normalized_name=engine.normalize_name(vehicle.make)
    ).first()
    if root is None:
        return None
    child: FitmentNode | None = (
        root.get_children().filter(normalized_name=engine.normalize_name(vehicle.model)).first()
    )
    return child


def ancestor_paths(node: FitmentNode) -> list[str]:
    """The paths of the node and of each ancestor: what a tag on any of them covers."""
    step = FitmentNode.steplen
    return [node.path[:end] for end in range(step, len(node.path) + 1, step)]
