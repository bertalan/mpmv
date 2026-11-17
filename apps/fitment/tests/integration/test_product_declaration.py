"""FIT.2: `set_product_fitment` (spec 17 §17.2.3, §17.2.4, §17.3.1)."""

import pytest
from django.core.exceptions import PermissionDenied
from django.test import override_settings

from apps.accounts.tests.factories import UserFactory
from apps.catalog.models import Product
from apps.catalog.tests.factories import CategoryFactory, ProductFactory
from apps.core.audit import AuditContext, user_context
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog
from apps.fitment import services
from apps.fitment.models import FitmentNode, ProductFitment, ProductFitmentState
from apps.fitment.services import FitmentError, RowInput
from apps.fitment.tests.factories import (
    FitmentCategoryFactory,
    FitmentNodeFactory,
    FitmentNoteFactory,
    ProductFitmentFactory,
    ProductFitmentStateFactory,
)
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="system:test", source="system")


@pytest.fixture
def tree() -> dict[str, FitmentNode]:
    make = FitmentNodeFactory.create(name="Make One")
    model_a = FitmentNodeFactory.create(name="Model A", parent=make)
    model_b = FitmentNodeFactory.create(name="Model B", parent=make)
    variant_2 = FitmentNodeFactory.create(name="Variant 2", parent=model_a)
    return {"make": make, "a": model_a, "b": model_b, "v2": variant_2}


@pytest.fixture
def years_on() -> None:
    services.save_scheme({"year_enabled": True}, ctx=CTX)


def declare(
    product: Product, mode: str, rows: list[RowInput], *, dry_run: bool = False
) -> ChangeSet:
    return services.set_product_fitment(product, mode, rows, "manual", ctx=CTX, dry_run=dry_run)


def test_specific_rows_are_stored_with_state_path_and_version(
    tree: dict[str, FitmentNode], years_on: None
) -> None:
    product = ProductFactory.create()
    note = FitmentNoteFactory.create()
    declare(
        product,
        "specific",
        [RowInput(tree["a"], 2016, 2021, note=note), RowInput(tree["v2"], is_exclusion=True)],
    )
    state = ProductFitmentState.objects.get(product=product)
    assert state.mode == "specific"
    assert state.rows_version == 1
    rows = {r.node_id: r for r in ProductFitment.objects.filter(product=product)}
    assert rows[tree["a"].pk].node_path == tree["a"].path
    assert rows[tree["a"].pk].years.lower == 2016
    assert rows[tree["a"].pk].years.upper - 1 == 2021
    assert rows[tree["a"].pk].note == note
    assert rows[tree["a"].pk].source == "manual"
    assert rows[tree["v2"].pk].is_exclusion
    assert rows[tree["v2"].pk].years is None
    declare(product, "specific", [RowInput(tree["a"])])
    assert ProductFitmentState.objects.get(product=product).rows_version == 2
    assert ProductFitment.objects.filter(product=product).count() == 1


def test_dry_run_returns_the_changeset_and_writes_nothing(tree: dict[str, FitmentNode]) -> None:
    product = ProductFactory.create()
    changes = declare(product, "specific", [RowInput(tree["a"])], dry_run=True)
    assert not changes.is_empty
    assert not ProductFitment.objects.exists()
    assert not ProductFitmentState.objects.exists()
    assert not AuditLog.objects.filter(action=services.PRODUCT_FITMENT_SET).exists()


def test_a_replace_of_several_rows_writes_one_audit_entry(tree: dict[str, FitmentNode]) -> None:
    product = ProductFactory.create()
    declare(product, "specific", [RowInput(tree["a"]), RowInput(tree["b"]), RowInput(tree["make"])])
    entries = AuditLog.objects.filter(action=services.PRODUCT_FITMENT_SET)
    assert entries.count() == 1
    entry = entries.get()
    assert entry.vendor_key == str(product.vendor.public_id)
    assert entry.after is not None
    assert len(entry.after["rows"]) == 3
    declare(product, "specific", [RowInput(tree["a"]), RowInput(tree["b"])])
    assert entries.count() == 2
    latest = entries.order_by("-id").first()
    assert latest is not None
    assert latest.before is not None
    assert len(latest.before["rows"]) == 3


def test_an_unchanged_declaration_changes_and_audits_nothing(
    tree: dict[str, FitmentNode], years_on: None
) -> None:
    product = ProductFactory.create()
    declare(product, "specific", [RowInput(tree["a"], 2016, 2021)])
    changes = declare(product, "specific", [RowInput(tree["a"], 2016, 2021)])
    assert changes.is_empty
    assert AuditLog.objects.filter(action=services.PRODUCT_FITMENT_SET).count() == 1
    assert ProductFitmentState.objects.get(product=product).rows_version == 1


def test_an_overlapping_contradictory_row_is_refused(tree: dict[str, FitmentNode]) -> None:
    product = ProductFactory.create()
    with pytest.raises(FitmentError, match="contradict"):
        declare(
            product,
            "specific",
            [RowInput(tree["a"], 2016, 2021), RowInput(tree["a"], 2020, 2023, is_exclusion=True)],
        )
    assert not ProductFitment.objects.exists()


def test_a_proposed_or_unknown_node_is_refused(tree: dict[str, FitmentNode]) -> None:
    product = ProductFactory.create()
    proposed = FitmentNodeFactory.create(name="Model P", parent=tree["make"], status="proposed")
    with pytest.raises(FitmentError, match="not active"):
        declare(product, "specific", [RowInput(proposed)])


def test_years_follow_the_scheme_range(tree: dict[str, FitmentNode]) -> None:
    product = ProductFactory.create()
    with pytest.raises(FitmentError, match="year"):  # the scheme does not use years by default
        declare(product, "specific", [RowInput(tree["a"], 2016, 2021)])
    services.save_scheme({"year_enabled": True}, ctx=CTX)
    with pytest.raises(FitmentError, match="year"):
        declare(product, "specific", [RowInput(tree["a"], 1800, 2021)])
    with pytest.raises(FitmentError, match="year"):
        declare(product, "specific", [RowInput(tree["a"], 2021, 2016)])


@override_settings(FITMENT_MAX_ROWS_PER_PRODUCT=2)
def test_the_row_limit_is_a_setting(tree: dict[str, FitmentNode]) -> None:
    product = ProductFactory.create()
    with pytest.raises(FitmentError, match="at most 2"):
        declare(product, "specific", [RowInput(tree[k]) for k in ("a", "b", "make")])


def test_mode_rules(tree: dict[str, FitmentNode]) -> None:
    product = ProductFactory.create()
    with pytest.raises(FitmentError):
        declare(product, "specific", [])
    with pytest.raises(FitmentError):
        declare(product, "universal", [RowInput(tree["a"])])
    declare(product, "universal", [])
    assert ProductFitmentState.objects.get(product=product).mode == "universal"


def test_always_visible_follows_mode_and_category(tree: dict[str, FitmentNode]) -> None:
    relevant = FitmentCategoryFactory.create().category
    plain = CategoryFactory.create()
    part = ProductFactory.create(category=relevant)
    gift = ProductFactory.create(category=plain)
    declare(part, "specific", [RowInput(tree["a"])])
    declare(gift, "specific", [RowInput(tree["a"])])
    assert ProductFitmentState.objects.get(product=part).always_visible is False
    assert ProductFitmentState.objects.get(product=gift).always_visible is True
    declare(part, "universal", [])
    assert ProductFitmentState.objects.get(product=part).always_visible is True


def test_marking_a_category_refreshes_always_visible(tree: dict[str, FitmentNode]) -> None:
    category = CategoryFactory.create()
    product = ProductFactory.create(category=category)
    declare(product, "specific", [RowInput(tree["a"])])
    assert ProductFitmentState.objects.get(product=product).always_visible is True
    services.set_fitment_relevant([category], True, ctx=CTX)
    assert ProductFitmentState.objects.get(product=product).always_visible is False
    services.set_fitment_relevant([category], False, ctx=CTX)
    assert ProductFitmentState.objects.get(product=product).always_visible is True


def test_an_exclusion_without_cover_is_a_warning_not_an_error(tree: dict[str, FitmentNode]) -> None:
    product = ProductFactory.create()
    rows = [RowInput(tree["a"]), RowInput(tree["b"], is_exclusion=True)]
    assert services.product_fitment_warnings("specific", rows)
    declare(product, "specific", rows)
    assert ProductFitment.objects.filter(product=product).count() == 2


def test_a_feed_refresh_replaces_only_feed_rows(tree: dict[str, FitmentNode]) -> None:
    product = ProductFactory.create()
    declare(product, "specific", [RowInput(tree["a"])])
    services.set_product_fitment(product, "specific", [RowInput(tree["b"])], "feed", ctx=CTX)
    services.set_product_fitment(product, "specific", [RowInput(tree["make"])], "feed", ctx=CTX)
    rows = {(r.node_id, r.source) for r in ProductFitment.objects.filter(product=product)}
    assert rows == {(tree["a"].pk, "manual"), (tree["make"].pk, "feed")}


def test_rows_pointing_at_a_node_block_its_deletion_and_follow_a_merge(
    tree: dict[str, FitmentNode],
) -> None:
    product = ProductFactory.create()
    declare(product, "specific", [RowInput(tree["b"])])
    target = FitmentNodeFactory.create(name="Model C", parent=tree["make"])
    services.merge_nodes(tree["b"], target, ctx=CTX)
    assert list(
        ProductFitment.objects.filter(product=product).values_list("node_id", flat=True)
    ) == [target.pk]
    assert ProductFitment.objects.get(product=product).node_path == target.path


def test_the_factories_make_rows_the_vendor_scoped_manager_returns() -> None:
    state = ProductFitmentStateFactory.create()
    row = ProductFitmentFactory.create(product=state.product)
    assert row.node_path == row.node.path
    assert ProductFitmentState.objects.filter(pk=state.pk).exists()
    assert ProductFitment.objects.filter(pk=row.pk).exists()


def _as(user: object) -> AuditContext:
    ctx = user_context(user, "127.0.0.1")  # type: ignore[arg-type]
    return AuditContext(actor_id=ctx.actor_id, actor_label=ctx.actor_label, source="vendor")


def test_a_member_of_the_products_vendor_may_declare_but_another_vendors_may_not(
    tree: dict[str, FitmentNode],
) -> None:
    product = ProductFactory.create()
    own = VendorMemberFactory.create(vendor=product.vendor)
    other = VendorMemberFactory.create()
    rows = [RowInput(tree["a"])]
    services.set_product_fitment(product, "specific", rows, "manual", ctx=_as(own.user))
    assert ProductFitment.objects.filter(product=product).count() == 1
    with pytest.raises(PermissionDenied):
        services.set_product_fitment(product, "universal", [], "manual", ctx=_as(other.user))
    with pytest.raises(PermissionDenied):
        services.set_product_fitment(
            product, "universal", [], "manual", ctx=_as(other.user), dry_run=True
        )
    assert ProductFitmentState.objects.get(product=product).mode == "specific"


def test_an_actor_without_membership_or_a_vanished_actor_is_refused(
    tree: dict[str, FitmentNode],
) -> None:
    product = ProductFactory.create()
    stranger = UserFactory.create()
    with pytest.raises(PermissionDenied):
        services.set_product_fitment(product, "universal", [], "manual", ctx=_as(stranger))
    ghost = AuditContext(actor_id=987654, actor_label="vendor:x", source="vendor")
    with pytest.raises(PermissionDenied):
        services.set_product_fitment(product, "universal", [], "manual", ctx=ghost)


def test_platform_staff_may_declare_for_any_vendor(tree: dict[str, FitmentNode]) -> None:
    product = ProductFactory.create()
    staff = UserFactory.create(superuser=True)
    services.set_product_fitment(product, "universal", [], "manual", ctx=_as(staff))
    assert ProductFitmentState.objects.get(product=product).mode == "universal"
