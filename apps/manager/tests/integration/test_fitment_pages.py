"""FIT.1 part 2: the Manager pages of the fitment scheme, its nodes and its categories.

The scheme is Configuration and tax (step-up, dry-run preview first), the nodes and the
categories are Catalog and moderation. The rules live in `apps.fitment.services`; these tests
prove the pages are thin adapters over them.
"""

from typing import Any

import pytest
from django.test import Client

from apps.accounts import levels
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.catalog.tests.factories import CategoryFactory
from apps.core.models import AuditLog
from apps.fitment import services
from apps.fitment.models import (
    FitmentCategory,
    FitmentLevel,
    FitmentNode,
    FitmentNodeAlias,
    FitmentNodeMerge,
)
from apps.fitment.tests.factories import FitmentLevelFactory, FitmentNodeFactory

pytestmark = pytest.mark.django_db

BASE = "/manager/platform/fitment/"
SCHEME = f"{BASE}scheme/"
NODES = f"{BASE}nodes/"
PROPOSALS = f"{BASE}nodes/proposals/"
NEW = f"{BASE}nodes/new/"
CATEGORIES = f"{BASE}categories/"


def sign_in(client: Client, role: str, **kwargs: Any) -> None:
    verified_login(client, UserFactory.create(platform_role=role), **kwargs)


def add_level(client: Client, code: str, label: str, *, confirm: bool = True, **extra: str) -> Any:
    data = {
        "action": "add_level",
        "code": code,
        "label_en": label,
        "plural_en": f"{label}s",
        "placeholder_en": "",
        **extra,
    }
    if confirm:
        data["confirm"] = "1"
    return client.post(SCHEME, data)


# --- Access


def test_configuration_staff_open_the_scheme_and_not_the_nodes(client: Client) -> None:
    sign_in(client, "config")
    assert client.get(SCHEME).status_code == 200
    assert client.get(NODES).status_code == 404
    assert client.get(CATEGORIES).status_code == 200


def test_catalog_staff_open_the_nodes_and_not_the_scheme(client: Client) -> None:
    sign_in(client, "catalog")
    assert client.get(NODES).status_code == 200
    assert client.get(PROPOSALS).status_code == 200
    assert client.get(NEW).status_code == 200
    assert client.get(SCHEME).status_code == 404


@pytest.mark.parametrize("role", ["finance", "support"])
def test_other_roles_do_not_open_the_scheme_or_the_categories(client: Client, role: str) -> None:
    sign_in(client, role)
    assert client.get(SCHEME).status_code == 404
    assert client.get(CATEGORIES).status_code == 404


def test_a_vendor_member_and_a_customer_are_refused(client: Client) -> None:
    verified_login(client, UserFactory.create())
    assert client.get(SCHEME).status_code in (403, 404)
    assert client.get(NODES).status_code in (403, 404)


def test_the_owner_reads_the_scheme_but_cannot_change_it(client: Client) -> None:
    sign_in(client, "owner")
    assert client.get(SCHEME).status_code == 200
    response = add_level(client, "make", "Make")
    assert response.status_code == 404
    assert not FitmentLevel.objects.exists()


def test_the_menu_lists_the_pages_of_the_role(client: Client) -> None:
    sign_in(client, "catalog")
    body = client.get("/manager/platform/").content.decode()
    assert NODES in body
    assert SCHEME not in body


# --- The scheme


def test_a_two_level_and_a_four_level_scheme_are_created_in_two_vocabularies(
    client: Client,
) -> None:
    """Done when: one installation sells vehicle parts (four levels), another sewing
    machines (two levels): the same pages build either scheme."""
    sign_in(client, "config")
    for code, label in (
        ("make", "Make"),
        ("model", "Model"),
        ("engine", "Engine"),
        ("year", "Year"),
    ):
        assert add_level(client, code, label).status_code == 302
    assert list(FitmentLevel.objects.order_by("position").values_list("code", flat=True)) == [
        "make",
        "model",
        "engine",
        "year",
    ]
    assert add_level(client, "extra", "Extra").status_code == 302  # refused: a message, no level
    assert FitmentLevel.objects.count() == 4
    FitmentLevel.objects.all().delete()
    for code, label in (("brand", "Brand"), ("machine", "Machine")):
        add_level(client, code, label)
    assert FitmentLevel.objects.count() == 2


def test_a_change_is_previewed_before_it_is_written(client: Client) -> None:
    sign_in(client, "config")
    response = add_level(client, "make", "Make", confirm=False)
    assert response.status_code == 200
    assert b"fitmentlevel" in response.content.lower()
    assert 'name="confirm"' in response.content.decode()
    assert not FitmentLevel.objects.exists()
    assert not AuditLog.objects.filter(action=services.LEVEL_CREATED).exists()
    add_level(client, "make", "Make")
    assert FitmentLevel.objects.filter(code="make").exists()
    assert AuditLog.objects.filter(action=services.LEVEL_CREATED).count() == 1


def test_the_scheme_settings_are_saved_through_the_service(client: Client) -> None:
    sign_in(client, "config")
    data = {
        "action": "save",
        "garage_noun_key": "Workshop",
        "year_enabled": "on",
        "year_min": "1950",
        "year_max_offset": "2",
        "max_profiles": "5",
        "confirm": "1",
    }
    assert client.post(SCHEME, data).status_code == 302
    scheme = services.current_scheme()
    assert (scheme.garage_noun_key, scheme.year_min, scheme.max_profiles) == ("Workshop", 1950, 5)
    assert scheme.contains_special_category is False
    assert AuditLog.objects.filter(action=services.SCHEME_UPDATED).exists()


def test_a_scheme_change_that_would_orphan_nodes_is_refused(client: Client) -> None:
    """Done when: no reorder, no removal, no naming change of a level that has nodes."""
    sign_in(client, "config")
    add_level(client, "make", "Make")
    add_level(client, "model", "Model")
    FitmentNodeFactory.create(name="Make One")
    FitmentNodeFactory.create(name="Model A", parent=FitmentNode.objects.get(name="Make One"))
    remove = client.post(SCHEME, {"action": "remove_level", "level": "model", "confirm": "1"})
    assert remove.status_code == 302
    reorder = client.post(
        SCHEME,
        {"action": "reorder", "position-make": "2", "position-model": "1", "confirm": "1"},
    )
    assert reorder.status_code == 302
    names = client.post(
        SCHEME,
        {"action": "translate_names", "level": "make", "value": "1", "confirm": "1"},
    )
    assert names.status_code == 302
    assert list(FitmentLevel.objects.order_by("position").values_list("code", flat=True)) == [
        "make",
        "model",
    ]
    assert not FitmentLevel.objects.get(code="make").translate_names
    page = client.get(SCHEME).content.decode()
    assert "Make One" not in page  # the scheme page lists levels and counts, not nodes


def test_an_empty_level_can_be_removed_and_levels_reordered(client: Client) -> None:
    sign_in(client, "config")
    add_level(client, "make", "Make")
    add_level(client, "model", "Model")
    client.post(
        SCHEME, {"action": "reorder", "position-make": "2", "position-model": "1", "confirm": "1"}
    )
    assert FitmentLevel.objects.get(code="model").position == 1
    client.post(SCHEME, {"action": "remove_level", "level": "make", "confirm": "1"})
    assert list(FitmentLevel.objects.values_list("code", flat=True)) == ["model"]


def test_a_level_is_renamed_in_a_language(client: Client) -> None:
    sign_in(client, "config")
    add_level(client, "make", "Make")
    client.post(
        SCHEME,
        {
            "action": "rename_level",
            "level": "make",
            "label_it": "Marca",
            "plural_it": "Marche",
            "placeholder_it": "Scegli la marca",
            "confirm": "1",
        },
    )
    row = FitmentLevel.objects.get(code="make").translations.get(language="it")
    assert (row.label, row.plural_label) == ("Marca", "Marche")


def test_the_scheme_change_needs_the_passkey_window(client: Client) -> None:
    sign_in(client, "config", level=levels.NO_WINDOW)
    response = add_level(client, "make", "Make")
    assert not FitmentLevel.objects.exists()
    assert response.status_code != 404


# --- Categories


def test_no_category_is_fitment_relevant_until_staff_choose_some(client: Client) -> None:
    CategoryFactory.create()
    assert not FitmentCategory.objects.exists()
    sign_in(client, "config")
    assert client.get(CATEGORIES).status_code == 200


def test_categories_are_set_in_bulk_with_a_preview_first(client: Client) -> None:
    first, second = CategoryFactory.create(), CategoryFactory.create()
    sign_in(client, "config")
    data = {
        "action": "relevant",
        "selected": [str(first.public_id), str(second.public_id)],
    }
    preview = client.post(CATEGORIES, data)
    assert preview.status_code == 200
    assert not FitmentCategory.objects.exists()
    assert client.post(CATEGORIES, {**data, "confirm": "1"}).status_code == 302
    assert set(FitmentCategory.objects.values_list("category_id", flat=True)) == {
        first.pk,
        second.pk,
    }
    assert AuditLog.objects.filter(action=services.CATEGORY_RELEVANCE_CHANGED).count() == 2
    client.post(
        CATEGORIES, {"action": "not_relevant", "selected": [str(first.public_id)], "confirm": "1"}
    )
    assert set(FitmentCategory.objects.values_list("category_id", flat=True)) == {second.pk}
    listed = client.get(f"{CATEGORIES}?relevant=yes").content.decode()
    assert f'value="{second.public_id}"' in listed
    assert f'value="{first.public_id}"' not in listed


def test_the_catalog_role_reads_the_categories_but_cannot_set_them(client: Client) -> None:
    category = CategoryFactory.create()
    sign_in(client, "catalog")
    assert client.get(CATEGORIES).status_code == 200
    response = client.post(
        CATEGORIES, {"action": "relevant", "selected": [str(category.public_id)], "confirm": "1"}
    )
    assert response.status_code == 404
    assert not FitmentCategory.objects.exists()


# --- Nodes


@pytest.fixture
def levels_three() -> None:
    for position, code in enumerate(["make", "model", "variant"], start=1):
        FitmentLevelFactory.create(position=position, code=code)


def detail(node: FitmentNode) -> str:
    return f"{NODES}{node.public_id}/"


def test_a_node_is_created_with_its_parent_and_listed(client: Client, levels_three: None) -> None:
    sign_in(client, "catalog")
    assert client.post(NEW, {"name": "Make One", "status": "active"}).status_code == 302
    make = FitmentNode.objects.get(name="Make One")
    response = client.post(
        NEW, {"name": "Model A", "parent": str(make.public_id), "status": "active"}
    )
    assert response.status_code == 302
    model = FitmentNode.objects.get(name="Model A")
    assert model.level.code == "model"
    assert model.get_parent() == make
    listed = client.get(NODES).content.decode()
    assert "Make One" in listed
    assert "Model A" in listed
    assert AuditLog.objects.filter(action=services.NODE_CREATED).count() == 2


def test_a_clashing_name_is_refused_with_a_message(client: Client, levels_three: None) -> None:
    FitmentNodeFactory.create(name="Make One")
    sign_in(client, "catalog")
    response = client.post(NEW, {"name": "make-one", "status": "active"})
    assert response.status_code == 200
    assert FitmentNode.objects.count() == 1


def test_the_list_is_filtered_by_status_and_level(client: Client, levels_three: None) -> None:
    make = FitmentNodeFactory.create(name="Make One")
    FitmentNodeFactory.create(name="Model A", parent=make)
    FitmentNodeFactory.create(name="Make Two", status="retired")
    sign_in(client, "catalog")
    only_retired = client.get(f"{NODES}?status=retired").content.decode()
    assert "Make Two" in only_retired
    assert "Make One" not in only_retired
    only_models = client.get(f"{NODES}?level=2").content.decode()
    assert "Model A" in only_models
    assert "Make One" not in only_models


def test_a_node_is_renamed_and_its_aliases_and_names_are_kept(
    client: Client, levels_three: None
) -> None:
    node = FitmentNodeFactory.create(name="Make One")
    FitmentLevel.objects.filter(code="make").update(translate_names=True)
    sign_in(client, "catalog")
    assert client.get(detail(node)).status_code == 200
    client.post(detail(node), {"action": "rename", "name": "Make 1"})
    node.refresh_from_db()
    assert node.name == "Make 1"
    assert node.aliases.filter(alias="Make One").exists()  # the old name stays findable
    client.post(detail(node), {"action": "add_alias", "alias": "M-One"})
    assert node.aliases.filter(alias="M-One").exists()
    alias = FitmentNodeAlias.objects.get(node=node, alias="M-One")
    client.post(detail(node), {"action": "remove_alias", "alias": str(alias.public_id)})
    assert not node.aliases.filter(alias="M-One").exists()
    client.post(detail(node), {"action": "set_name", "language": "it", "name": "Marca Uno"})
    assert node.translations.get(language="it").name == "Marca Uno"


def test_a_proposed_node_is_invisible_to_pickers_until_approved(
    client: Client, levels_three: None
) -> None:
    """Done when: the proposals queue lists it, the pickers do not, approval offers it."""
    proposed = FitmentNodeFactory.create(name="Make Proposed", status="proposed")
    sign_in(client, "catalog")
    assert "Make Proposed" in client.get(PROPOSALS).content.decode()
    assert proposed not in FitmentNode.objects.pickable()
    data = {"action": "approve", "selected": [str(proposed.public_id)]}
    assert client.post(PROPOSALS, data).status_code == 200  # the preview
    proposed.refresh_from_db()
    assert proposed.status == "proposed"
    assert client.post(PROPOSALS, {**data, "confirm": "1"}).status_code == 302
    proposed.refresh_from_db()
    assert proposed.status == "active"
    assert proposed in FitmentNode.objects.pickable()
    assert "Make Proposed" not in client.get(PROPOSALS).content.decode()


def test_a_proposal_is_refused_with_a_reason_and_removed(
    client: Client, levels_three: None
) -> None:
    proposed = FitmentNodeFactory.create(name="Make Proposed", status="proposed")
    sign_in(client, "catalog")
    client.post(detail(proposed), {"action": "refuse", "reason": ""})
    assert FitmentNode.objects.filter(pk=proposed.pk).exists()
    client.post(detail(proposed), {"action": "refuse", "reason": "Not a real make"})
    assert not FitmentNode.objects.filter(pk=proposed.pk).exists()
    assert AuditLog.objects.filter(action=services.NODE_REFUSED).exists()


def test_a_node_is_retired_and_offered_again(client: Client, levels_three: None) -> None:
    node = FitmentNodeFactory.create(name="Make One")
    sign_in(client, "catalog")
    client.post(detail(node), {"action": "retire"})
    node.refresh_from_db()
    assert node.status == "retired"
    assert node not in FitmentNode.objects.pickable()
    client.post(detail(node), {"action": "reactivate"})
    node.refresh_from_db()
    assert node.status == "active"


def test_a_merge_is_previewed_then_moves_the_children_and_the_old_id(
    client: Client, levels_three: None
) -> None:
    """Done when: a node merge moves what points at the source (children here, references in
    the service tests) and the old id still answers as the target."""
    source = FitmentNodeFactory.create(name="Make Old")
    target = FitmentNodeFactory.create(name="Make New")
    child = FitmentNodeFactory.create(name="Model A", parent=source)
    sign_in(client, "catalog")
    assert client.get(f"{detail(source)}merge/").status_code == 200
    data = {"target": str(target.public_id)}
    preview = client.post(f"{detail(source)}merge/", data)
    assert preview.status_code == 200
    assert FitmentNode.objects.filter(pk=source.pk).exists()
    assert client.post(f"{detail(source)}merge/", {**data, "confirm": "1"}).status_code == 302
    child.refresh_from_db()
    assert child.get_parent().pk == target.pk
    assert not FitmentNode.objects.filter(pk=source.pk).exists()
    assert FitmentNodeMerge.objects.filter(old_node_id=source.pk, into_node=target).exists()
    assert services.resolve_node(source.pk) == target
    assert AuditLog.objects.filter(action=services.NODE_MERGED).exists()


def test_a_merge_into_itself_or_a_node_of_another_level_is_refused(
    client: Client, levels_three: None
) -> None:
    make = FitmentNodeFactory.create(name="Make One")
    model = FitmentNodeFactory.create(name="Model A", parent=make)
    sign_in(client, "catalog")
    response = client.post(
        f"{detail(model)}merge/", {"target": str(make.public_id), "confirm": "1"}
    )
    assert response.status_code in (200, 302)
    assert FitmentNode.objects.filter(pk=model.pk).exists()


def test_the_nodes_pages_are_closed_to_a_read_only_role(client: Client, levels_three: None) -> None:
    node = FitmentNodeFactory.create(name="Make One")
    sign_in(client, "support")
    assert client.get(NODES).status_code == 200
    assert client.post(detail(node), {"action": "retire"}).status_code == 404
    assert client.post(NEW, {"name": "Make Two", "status": "active"}).status_code == 404
    node.refresh_from_db()
    assert node.status == "active"
    assert FitmentNode.objects.count() == 1


def test_an_unknown_node_is_a_404(client: Client) -> None:
    sign_in(client, "catalog")
    nil = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"{NODES}{nil}/").status_code == 404
    assert client.get(f"{NODES}{nil}/merge/").status_code == 404
