"""What else points at a fitment node: FIT.2 product rows, FIT.5 customer profiles (spec 17).

This is start-up wiring, not a business mutation: whatever holds a foreign key to `FitmentNode`
registers it, so a merge moves it and a delete is refused while it exists. The registration is
explicit and happens in the owning app's `AppConfig.ready()`, never as an import side effect:

    def ready(self) -> None:
        from apps.fitment.references import register_node_reference
        register_node_reference(ProductFitment, "node")

Declare the key with `on_delete=PROTECT`: a node is merged or retired, never deleted under a
reference. `apps/fitment/tests/integration/test_references.py` lists every foreign key to a node
in the project and fails when one is not registered. A writer that inserts a reference (a FIT.2
product row, a FIT.5 profile) must lock the node first (`select_for_update`) in the same
transaction, so a concurrent merge cannot move references past it. Relations that are not plain
foreign keys use `register_reference` with their own `count` and `move`.
"""

from collections.abc import Callable
from dataclasses import dataclass

from django.db import models

from .models import FitmentNode, FitmentNodeMerge


@dataclass(frozen=True)
class Reference:
    count: Callable[[FitmentNode], int]
    move: Callable[[FitmentNode, FitmentNode], object]
    field: tuple[str, str] | None = None  # (model label, field name) of a registered foreign key


_references: dict[str, Reference] = {}


def register_reference(
    name: str,
    *,
    count: Callable[[FitmentNode], int],
    move: Callable[[FitmentNode, FitmentNode], object],
    field: tuple[str, str] | None = None,
) -> None:
    """Declare something that points at nodes: `count` how many point at one, `move` re-point
    them from `source` to `target`. A merge moves them, a delete is refused while any exist."""
    if name in _references:
        raise ValueError(f"Node reference {name!r} is already registered.")
    _references[name] = Reference(count, move, field)


def register_node_reference(
    model: type[models.Model], field: str, *, name: str | None = None
) -> None:
    """Register `model.field`, a foreign key to `FitmentNode`, from the owning app's `ready()`."""
    label = (model._meta.label_lower, field)

    def count(node: FitmentNode) -> int:
        return model._default_manager.filter(**{field: node}).count()

    def move(source: FitmentNode, target: FitmentNode) -> object:
        return model._default_manager.filter(**{field: source}).update(**{field: target})

    register_reference(name or f"{label[0]}.{field}", count=count, move=move, field=label)


def unregister_reference(name: str) -> None:
    _references.pop(name, None)


def registered_fields() -> set[tuple[str, str]]:
    """The (model label, field) pairs registered with `register_node_reference`."""
    return {r.field for r in _references.values() if r.field is not None}


def reference_counts(node: FitmentNode) -> dict[str, int]:
    """How many rows of each registered reference point at `node`."""
    return {name: reference.count(node) for name, reference in _references.items()}


def reference_count(node: FitmentNode) -> int:
    return sum(reference_counts(node).values())


def move_references(source: FitmentNode, target: FitmentNode) -> None:
    """Re-point every registered reference from `source` to `target` (inside a merge)."""
    for reference in _references.values():
        reference.move(source, target)


def register_builtin_references() -> None:
    """This app's own references, called from `FitmentConfig.ready()`."""
    if ("fitment.fitmentnodemerge", "into_node") not in registered_fields():
        register_node_reference(FitmentNodeMerge, "into_node")
