"""FIT.1: nodes of the scheme: CRUD, aliases, proposals, retire and merge."""

import pytest

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.fitment import services
from apps.fitment.models import FitmentLevel, FitmentNode, FitmentNodeAlias, FitmentNodeMerge
from apps.fitment.services import FitmentError
from apps.fitment.tests.factories import (
    FitmentLevelFactory,
    FitmentNodeAliasFactory,
    FitmentNodeFactory,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")


@pytest.fixture(autouse=True)
def three_levels() -> None:
    for position, code in enumerate(["make", "model", "variant"], start=1):
        FitmentLevelFactory.create(position=position, code=code)


def make_node(name: str, parent: FitmentNode | None = None) -> FitmentNode:
    node = services.create_node(name=name, parent=parent, ctx=CTX).obj
    assert node is not None
    return node


def test_nodes_form_a_tree_and_take_their_level_from_their_depth() -> None:
    make = make_node("Make One")
    model = make_node("Model A", make)
    variant = make_node("Variant 2", model)
    assert [n.level.code for n in (make, model, variant)] == ["make", "model", "variant"]
    assert list(variant.get_ancestors()) == [make, model]
    assert variant.sort_key
    assert variant.normalized_name == "variant 2"


def test_a_node_below_the_last_level_is_refused() -> None:
    node = make_node("Make One")
    for name in ("Model A", "Variant 2"):
        node = make_node(name, node)
    with pytest.raises(FitmentError):
        services.create_node(name="Too deep", parent=node, ctx=CTX)


def test_a_sibling_with_the_same_normalised_name_or_alias_is_refused() -> None:
    make = make_node("Make One")
    model = make_node("Model A", make)
    services.add_alias(model, "Model-A old", ctx=CTX)
    with pytest.raises(FitmentError):
        services.create_node(name="model  a", parent=make, ctx=CTX)
    with pytest.raises(FitmentError):
        services.create_node(name="Model A OLD", parent=make, ctx=CTX)
    make_node("Model A", make_node("Make Two"))  # same name under another parent is fine


def test_a_node_is_created_with_a_dry_run_first_and_audited() -> None:
    changes = services.create_node(name="Make One", ctx=CTX, dry_run=True).changes
    assert changes.summary()["create"] == 1
    assert not FitmentNode.objects.exists()
    make_node("Make One")
    entry = AuditLog.objects.get(action="fitment.node.created")
    assert entry.target_model == "fitment.fitmentnode"


def test_names_are_nfc_normalised_and_trimmed() -> None:
    node = make_node("  Citroën  ")
    assert node.name == "Citroën"


def test_a_translated_name_needs_a_level_that_translates_names() -> None:
    make = make_node("Make One")
    with pytest.raises(FitmentError):
        services.set_node_name(make, "it", "Marca Uno", ctx=CTX)
    services.set_translate_names(FitmentLevel.objects.get(code="model"), True, ctx=CTX)
    model = make_node("Model A", make)
    services.set_node_name(model, "it", "Modello A", ctx=CTX)
    services.set_node_name(model, "it", "Modello Uno", ctx=CTX)
    assert model.translations.get(language="it").name == "Modello Uno"


def test_aliases_are_added_and_removed_with_audit() -> None:
    node = make_node("Make One")
    alias = services.add_alias(node, "Make-1", language="en", ctx=CTX).obj
    assert alias is not None
    assert alias.normalized == "make 1"
    with pytest.raises(FitmentError):
        services.add_alias(node, "make 1", language="en", ctx=CTX)
    services.remove_alias(alias, ctx=CTX)
    assert not FitmentNodeAlias.objects.exists()
    assert AuditLog.objects.filter(action="fitment.alias.created").exists()
    assert AuditLog.objects.filter(action="fitment.alias.deleted").exists()


def test_a_proposed_node_is_invisible_to_pickers_until_approved() -> None:
    make = make_node("Make One")
    proposed = services.propose_node(name="Model New", parent=make, ctx=CTX).obj
    assert proposed is not None
    assert proposed.status == FitmentNode.Status.PROPOSED
    assert list(FitmentNode.objects.pickable()) == [make]
    assert list(FitmentNode.objects.proposals()) == [proposed]
    services.approve_node(proposed, ctx=CTX)
    proposed.refresh_from_db()
    assert set(FitmentNode.objects.pickable()) == {make, proposed}
    assert not FitmentNode.objects.proposals().exists()


def test_a_proposal_under_a_proposed_parent_is_refused_and_a_parent_is_approved_first() -> None:
    parent = services.propose_node(name="Make New", ctx=CTX).obj
    assert parent is not None
    with pytest.raises(FitmentError):
        services.create_node(name="Model", parent=parent, ctx=CTX)
    child = services.propose_node(name="Model", parent=parent, ctx=CTX).obj
    assert child is not None
    with pytest.raises(FitmentError):
        services.approve_node(child, ctx=CTX)


def test_a_refused_proposal_is_removed_with_its_reason_in_the_audit_log() -> None:
    proposed = services.propose_node(name="Typo Make", ctx=CTX).obj
    assert proposed is not None
    services.refuse_node(proposed, reason="Duplicate of an existing make", ctx=CTX)
    assert not FitmentNode.objects.filter(pk=proposed.pk).exists()
    entry = AuditLog.objects.get(action="fitment.node.refused")
    assert entry.after is not None
    assert entry.after["reason"] == "Duplicate of an existing make"
    with pytest.raises(FitmentError):
        services.refuse_node(make_node("Active Make"), reason="x", ctx=CTX)
    with pytest.raises(FitmentError):
        services.refuse_node(proposed_node(), reason="", ctx=CTX)


def proposed_node() -> FitmentNode:
    node = services.propose_node(name="Another", ctx=CTX).obj
    assert node is not None
    return node


def test_retiring_a_node_retires_its_subtree_and_hides_it_from_pickers() -> None:
    make = make_node("Make One")
    model = make_node("Model A", make)
    other = make_node("Make Two")
    services.retire_node(make, ctx=CTX)
    make.refresh_from_db()
    model.refresh_from_db()
    assert make.status == model.status == FitmentNode.Status.RETIRED
    assert list(FitmentNode.objects.pickable()) == [other]
    assert AuditLog.objects.filter(action="fitment.node.status_changed").count() == 2
    services.reactivate_node(make, ctx=CTX)
    make.refresh_from_db()
    assert make.status == FitmentNode.Status.ACTIVE
    with pytest.raises(FitmentError):
        services.reactivate_node(make, ctx=CTX)


def test_a_node_is_deleted_only_when_nothing_refers_to_it() -> None:
    make = make_node("Make One")
    model = make_node("Model A", make)
    with pytest.raises(FitmentError):
        services.delete_node(make, ctx=CTX)  # has children
    services.delete_node(model, ctx=CTX)
    assert not FitmentNode.objects.filter(pk=model.pk).exists()
    assert AuditLog.objects.filter(action="fitment.node.deleted").exists()


def test_rename_keeps_the_old_name_findable_as_an_alias() -> None:
    make = make_node("Make One")
    services.rename_node(make, "Make 1", ctx=CTX)
    make.refresh_from_db()
    assert make.name == "Make 1"
    assert make.aliases.filter(normalized="make one").exists()
    with pytest.raises(FitmentError):
        services.rename_node(make, "  ", ctx=CTX)


def test_a_merge_moves_the_references_and_the_old_id_answers_as_the_target() -> None:
    make = make_node("Make One")
    duplicate = make_node("Make Uno")
    kept_child = make_node("Model A", make)
    moved_child = make_node("Model B", duplicate)
    FitmentNodeAliasFactory.create(node=duplicate, alias="Mk Uno")
    earlier = FitmentNodeMerge.objects.create(old_node_id=987654, into_node=duplicate)
    old_pk = duplicate.pk

    changes = services.merge_nodes(duplicate, make, ctx=CTX, dry_run=True)
    assert not changes.is_empty
    assert FitmentNode.objects.filter(pk=old_pk).exists()

    services.merge_nodes(duplicate, make, ctx=CTX)

    assert not FitmentNode.objects.filter(pk=old_pk).exists()
    moved_child.refresh_from_db()
    assert moved_child.get_parent() == make
    assert kept_child.get_parent() == make
    assert set(make.aliases.values_list("normalized", flat=True)) >= {"mk uno", "make uno"}
    earlier.refresh_from_db()
    assert earlier.into_node == make
    assert services.resolve_node(old_pk) == make
    assert services.resolve_node(987654) == make
    assert services.resolve_node(make.pk) == make
    entry = AuditLog.objects.get(action="fitment.node.merged")
    assert entry.before is not None
    assert entry.before["moved_children"] == 1


def test_a_merge_with_a_clashing_child_name_is_refused_and_changes_nothing() -> None:
    make = make_node("Make One")
    duplicate = make_node("Make Uno")
    make_node("Model A", make)
    make_node("model-a", duplicate)
    with pytest.raises(FitmentError):
        services.merge_nodes(duplicate, make, ctx=CTX)
    assert FitmentNode.objects.filter(pk=duplicate.pk).exists()


def test_a_merge_across_levels_or_into_the_own_subtree_is_refused() -> None:
    make = make_node("Make One")
    model = make_node("Model A", make)
    with pytest.raises(FitmentError):
        services.merge_nodes(make, model, ctx=CTX)
    with pytest.raises(FitmentError):
        services.merge_nodes(model, make, ctx=CTX)
    with pytest.raises(FitmentError):
        services.merge_nodes(make, make, ctx=CTX)


def test_a_registered_reference_is_counted_moved_and_blocks_deletion() -> None:
    make = make_node("Make One")
    duplicate = make_node("Make Uno")
    moved: list[tuple[int, int]] = []
    services.register_reference(
        "test-reference",
        count=lambda node: 2 if node.pk == duplicate.pk else 0,
        move=lambda source, target: moved.append((source.pk, target.pk)),
    )
    try:
        assert services.reference_count(duplicate) >= 2
        with pytest.raises(FitmentError):
            services.delete_node(duplicate, ctx=CTX)
        services.merge_nodes(duplicate, make, ctx=CTX)
        assert moved
    finally:
        services.unregister_reference("test-reference")


def test_the_factory_makes_distinct_valid_nodes() -> None:
    first, second = FitmentNodeFactory.create_batch(2)
    assert first.pk != second.pk
    first.full_clean()
