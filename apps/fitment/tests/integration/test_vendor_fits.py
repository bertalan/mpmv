"""FIT.3: the shop-side services (proposals with a limit, bulk add, copy, the report)."""

import pytest
from django.core.exceptions import PermissionDenied
from django.test import override_settings

from apps.accounts.tests.factories import UserFactory
from apps.catalog.tests.factories import ProductFactory
from apps.core.audit import AuditContext, user_context
from apps.fitment import services
from apps.fitment.models import FitmentNode, FitmentNodeProposal, ProductFitment
from apps.fitment.services import FitmentError, RowInput
from apps.fitment.tests.factories import (
    FitmentLevelFactory,
    FitmentNodeFactory,
    ProductFitmentFactory,
)
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _top_level() -> None:
    FitmentLevelFactory.create(position=1)


SYSTEM = AuditContext(actor_label="system:test", source="system")


def test_a_proposal_is_recorded_for_the_shop_and_audited() -> None:
    vendor = VendorFactory.create()
    applied = services.propose_node_for_vendor(vendor, name="New Make", ctx=SYSTEM)
    assert applied.obj is not None
    assert applied.obj.status == "proposed"
    assert FitmentNodeProposal.objects.get(node=applied.obj).vendor == vendor
    assert services.pending_proposals(vendor) == 1


def test_dry_run_writes_nothing() -> None:
    vendor = VendorFactory.create()
    services.propose_node_for_vendor(vendor, name="New Make", ctx=SYSTEM, dry_run=True)
    assert not FitmentNode.objects.exists()
    assert not FitmentNodeProposal.objects.exists()


def test_the_limit_counts_pending_proposals_of_the_shop_only() -> None:
    vendor, other = VendorFactory.create(), VendorFactory.create()
    for n in range(20):
        services.propose_node_for_vendor(vendor, name=f"Make {n}", ctx=SYSTEM)
    with pytest.raises(FitmentError):
        services.propose_node_for_vendor(vendor, name="Make 21", ctx=SYSTEM)
    services.propose_node_for_vendor(other, name="Other Make", ctx=SYSTEM)  # its own budget
    node = FitmentNode.objects.get(name="Make 0")
    services.approve_node(node, ctx=SYSTEM)  # a reviewed proposal frees its place
    services.propose_node_for_vendor(vendor, name="Make 21", ctx=SYSTEM)


@override_settings(FITMENT_MAX_PENDING_PROPOSALS=1)
def test_the_limit_is_a_setting() -> None:
    vendor = VendorFactory.create()
    services.propose_node_for_vendor(vendor, name="A", ctx=SYSTEM)
    with pytest.raises(FitmentError):
        services.propose_node_for_vendor(vendor, name="B", ctx=SYSTEM)


def test_only_a_member_with_the_products_right_proposes() -> None:
    vendor = VendorFactory.create()
    stranger = UserFactory.create()
    with pytest.raises(PermissionDenied):
        services.propose_node_for_vendor(vendor, name="X", ctx=user_context(stranger, None))
    member = VendorMemberFactory.create(vendor=vendor, role="owner").user
    services.propose_node_for_vendor(vendor, name="X", ctx=user_context(member, None))


def test_a_proposal_needs_an_approved_parent() -> None:
    vendor = VendorFactory.create()
    parent = FitmentNodeFactory.create(status="proposed")
    with pytest.raises(FitmentError):
        services.propose_node_for_vendor(vendor, name="Child", parent=parent, ctx=SYSTEM)


def test_bulk_add_keeps_existing_rows_and_is_all_or_nothing() -> None:
    vendor = VendorFactory.create()
    make = FitmentNodeFactory.create()
    other = FitmentNodeFactory.create()
    first = ProductFactory.create(vendor=vendor)
    ProductFitmentFactory.create(product=first, node=other)
    from apps.fitment.tests.factories import ProductFitmentStateFactory

    ProductFitmentStateFactory.create(product=first)
    second = ProductFactory.create(vendor=vendor)
    changes = services.add_product_fitment(
        [first, second], [RowInput(make)], ctx=SYSTEM, dry_run=True
    )
    assert not changes.is_empty
    assert ProductFitment.objects.count() == 1
    services.add_product_fitment([first, second], [RowInput(make)], ctx=SYSTEM)
    assert ProductFitment.objects.filter(product=first).count() == 2
    assert ProductFitment.objects.filter(product=second).count() == 1
    universal = ProductFactory.create(vendor=vendor)
    services.set_product_fitment(universal, "universal", [], "manual", ctx=SYSTEM)
    third = ProductFactory.create(vendor=vendor)
    with pytest.raises(FitmentError):
        services.add_product_fitment([third, universal], [RowInput(make)], ctx=SYSTEM)
    assert not ProductFitment.objects.filter(product=third).exists()


def test_copy_refuses_another_shops_product() -> None:
    one, two = ProductFactory.create(), ProductFactory.create()
    with pytest.raises(FitmentError):
        services.copy_product_fitment(one, two, ctx=SYSTEM)


def test_refusing_or_deleting_a_proposal_drops_the_record() -> None:
    vendor = VendorFactory.create()
    one = services.propose_node_for_vendor(vendor, name="One", ctx=SYSTEM).obj
    two = services.propose_node_for_vendor(vendor, name="Two", ctx=SYSTEM).obj
    assert one is not None
    assert two is not None
    services.refuse_node(one, reason="duplicate", ctx=SYSTEM)
    services.delete_node(two, ctx=SYSTEM)
    assert not FitmentNodeProposal.objects.exists()
    assert services.pending_proposals(vendor) == 0


def test_merging_a_proposal_drops_the_record() -> None:
    vendor = VendorFactory.create()
    proposed = services.propose_node_for_vendor(vendor, name="Mk", ctx=SYSTEM).obj
    target = FitmentNodeFactory.create(name="Make")
    assert proposed is not None
    services.merge_nodes(proposed, target, ctx=SYSTEM)
    assert not FitmentNodeProposal.objects.exists()
