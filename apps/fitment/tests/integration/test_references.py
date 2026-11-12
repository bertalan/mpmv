"""FIT.1: everything that points at a node is registered, so a merge moves it (spec 17 §17.2.2)."""

import pytest
from django.apps import apps
from django.db import models

from apps.core.audit import AuditContext
from apps.fitment import references
from apps.fitment.models import (
    FitmentCategory,
    FitmentNode,
    FitmentNodeAlias,
    FitmentNodeMerge,
    FitmentNodeTranslation,
    FitmentNote,
)
from apps.fitment.tests.factories import (
    FitmentCategoryFactory,
    FitmentLevelTranslationFactory,
    FitmentNodeFactory,
    FitmentNodeMergeFactory,
    FitmentNodeTranslationFactory,
    FitmentNoteFactory,
    FitmentNoteTranslationFactory,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")

# Tables owned by a node: a merge copies the aliases itself and the translations go with it.
NODE_OWNED = {FitmentNodeAlias, FitmentNodeTranslation}


def foreign_keys_to_nodes() -> list[tuple[type[models.Model], str, models.ForeignKey]]:  # type: ignore[type-arg]
    found = []
    for model in apps.get_models():
        for field in model._meta.get_fields():
            if (
                isinstance(field, models.ForeignKey)
                and field.related_model is FitmentNode
                and not field.auto_created
            ):
                found.append((model, field.name, field))
    return found


def test_the_scan_finds_the_foreign_keys_to_nodes() -> None:
    models_found = {model for model, _name, _field in foreign_keys_to_nodes()}
    assert {FitmentNodeMerge, FitmentNodeAlias, FitmentNodeTranslation} <= models_found


def test_every_foreign_key_to_a_node_is_registered_and_protects_the_node() -> None:
    """FIT.2 and FIT.5 register their product rows and profiles from their own `ready()`;
    this fails for the project when a relation to a node is added without it."""
    registered = references.registered_fields()
    for model, name, field in foreign_keys_to_nodes():
        if model in NODE_OWNED:
            continue
        assert (model._meta.label_lower, name) in registered, (
            f"Register {model._meta.label}.{name} with references.register_node_reference "
            "in the app's ready()"
        )
        assert field.remote_field.on_delete is models.PROTECT, (
            f"{model._meta.label}.{name} should use PROTECT: a node is merged, not deleted"
        )


def test_the_references_are_registered_once_the_apps_are_ready() -> None:
    assert ("fitment.fitmentnodemerge", "into_node") in references.registered_fields()


def test_registering_the_same_name_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="already registered"):
        references.register_node_reference(FitmentNodeMerge, "into_node")


def test_deleting_a_node_a_merge_points_at_is_blocked_by_the_database() -> None:
    merge = FitmentNodeMergeFactory.create()
    with pytest.raises(models.ProtectedError):
        FitmentNode.objects.get(pk=merge.into_node_id).delete()


def test_the_remaining_factories_make_valid_rows() -> None:
    for factory in (
        FitmentNoteFactory,
        FitmentNoteTranslationFactory,
        FitmentNodeTranslationFactory,
        FitmentLevelTranslationFactory,
        FitmentCategoryFactory,
    ):
        row = factory.create()
        row.full_clean()
    assert FitmentNote.objects.count() == 2  # the translation factory makes its own note
    assert FitmentCategory.objects.count() == 1
    assert FitmentNodeFactory.create().pk
