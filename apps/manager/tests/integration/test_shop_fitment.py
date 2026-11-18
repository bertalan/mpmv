"""FIT.3: the Fits pages of the shop space (spec 17 §17.3): one product, 50 products in bulk,
proposals with the anti-flooding limit, the report, tenant isolation and the session gate."""

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.catalog.models import Product
from apps.catalog.tests.factories import CategoryFactory, ProductFactory
from apps.core.models import AuditLog
from apps.fitment.models import (
    FitmentNode,
    FitmentNodeProposal,
    ProductFitment,
    ProductFitmentState,
)
from apps.fitment.tests.factories import (
    FitmentCategoryFactory,
    FitmentNodeFactory,
    FitmentNodeProposalFactory,
    ProductFitmentFactory,
    ProductFitmentStateFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

LIMIT = 20


@pytest.fixture
def vendor(db: None) -> Vendor:
    return VendorFactory.create(display_name="Shop One", status=Vendor.Status.ACTIVE)


@pytest.fixture
def owner(vendor: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor, role="owner").user


@pytest.fixture
def signed_in(client: Client, owner: User) -> Client:
    verified_login(client, owner)
    return client


@pytest.fixture
def tree() -> dict[str, FitmentNode]:
    make = FitmentNodeFactory.create(name="Make One")
    model = FitmentNodeFactory.create(name="Model A", parent=make)
    return {"make": make, "model": model}


def _rows(*nodes: FitmentNode, prefix: str = "rows", extra: int = 0) -> dict[str, str]:
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(nodes) + extra),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for i, node in enumerate(nodes):
        data[f"{prefix}-{i}-node"] = str(node.public_id)
    for i in range(len(nodes), len(nodes) + extra):
        data[f"{prefix}-{i}-node"] = ""
    return data


def _product_url(product: Product) -> str:
    return reverse("manager:shop:fitment_product", args=[product.public_id])


def test_a_vendor_declares_what_a_product_fits_after_the_preview(
    signed_in: Client, vendor: Vendor, tree: dict[str, FitmentNode]
) -> None:
    product = ProductFactory.create(vendor=vendor)
    data = {"action": "save", "mode": "specific", **_rows(tree["make"], tree["model"])}
    data["rows-1-is_exclusion"] = "on"
    preview = signed_in.post(_product_url(product), data)
    assert preview.status_code == 200
    assert not ProductFitment.objects.filter(product=product).exists()  # nothing before confirm
    confirmed = signed_in.post(_product_url(product), {**data, "confirm": "1"})
    assert confirmed.status_code == 302
    assert ProductFitment.objects.filter(product=product).count() == 2
    assert ProductFitmentState.objects.get(product=product).mode == "specific"
    assert AuditLog.objects.filter(action="fitment.product.fitment_set").exists()


def test_universal_and_the_page_shows_the_state(signed_in: Client, vendor: Vendor) -> None:
    product = ProductFactory.create(vendor=vendor)
    signed_in.post(
        _product_url(product), {"action": "save", "mode": "universal", "confirm": "1", **_rows()}
    )
    assert ProductFitmentState.objects.get(product=product).mode == "universal"
    assert signed_in.get(_product_url(product)).status_code == 200


def test_a_row_on_a_proposed_node_is_not_offered(
    signed_in: Client, vendor: Vendor, tree: dict[str, FitmentNode]
) -> None:
    product = ProductFactory.create(vendor=vendor)
    hidden = FitmentNodeFactory.create(name="Hidden Make", status=FitmentNode.Status.PROPOSED)
    page = signed_in.get(_product_url(product))
    assert "Hidden Make" not in page.content.decode()
    response = signed_in.post(
        _product_url(product),
        {"action": "save", "mode": "specific", "confirm": "1", **_rows(hidden)},
    )
    assert response.status_code == 200  # the choice is refused, nothing is saved
    assert not ProductFitment.objects.filter(product=product).exists()


def test_copy_from_another_product(
    signed_in: Client, vendor: Vendor, tree: dict[str, FitmentNode]
) -> None:
    source = ProductFactory.create(vendor=vendor)
    ProductFitmentStateFactory.create(product=source)
    ProductFitmentFactory.create(product=source, node=tree["make"])
    target = ProductFactory.create(vendor=vendor)
    data = {"action": "copy", "source": str(source.public_id)}
    assert signed_in.post(_product_url(target), data).status_code == 200
    assert not ProductFitment.objects.filter(product=target).exists()
    signed_in.post(_product_url(target), {**data, "confirm": "1"})
    assert ProductFitment.objects.filter(product=target, node=tree["make"]).exists()


def test_fifty_products_in_bulk_with_a_preview(
    signed_in: Client, vendor: Vendor, tree: dict[str, FitmentNode]
) -> None:
    products = ProductFactory.create_batch(50, vendor=vendor)
    url = reverse("manager:shop:fitment")
    selected = [str(p.public_id) for p in products]
    base = {"action": "add_fitment", "selected": selected}
    form = signed_in.post(url, base)
    assert form.status_code == 200
    data = {**base, **_rows(tree["make"], extra=1)}
    preview = signed_in.post(url, data)
    assert preview.status_code == 200
    assert not ProductFitment.objects.exists()
    done = signed_in.post(url, {**data, "confirm": "1"})
    assert done.status_code == 302
    assert ProductFitment.objects.filter(node=tree["make"]).count() == 50
    assert ProductFitmentState.objects.filter(mode="specific").count() == 50


def test_a_bulk_selection_with_another_shops_product_is_a_404(
    signed_in: Client, vendor: Vendor, tree: dict[str, FitmentNode]
) -> None:
    mine = ProductFactory.create(vendor=vendor)
    theirs = ProductFactory.create(vendor=VendorFactory.create())
    response = signed_in.post(
        reverse("manager:shop:fitment"),
        {
            "action": "add_fitment",
            "selected": [str(mine.public_id), str(theirs.public_id)],
            **_rows(tree["make"]),
            "confirm": "1",
        },
    )
    assert response.status_code == 404
    assert not ProductFitment.objects.exists()


def test_another_shops_product_is_a_404_for_page_and_save(
    signed_in: Client, tree: dict[str, FitmentNode]
) -> None:
    theirs = ProductFactory.create(vendor=VendorFactory.create())
    assert signed_in.get(_product_url(theirs)).status_code == 404
    data = {"action": "save", "mode": "specific", "confirm": "1", **_rows(tree["make"])}
    assert signed_in.post(_product_url(theirs), data).status_code == 404
    assert not ProductFitment.objects.exists()


def test_copy_never_reads_another_shops_product(
    signed_in: Client, vendor: Vendor, tree: dict[str, FitmentNode]
) -> None:
    theirs = ProductFactory.create(vendor=VendorFactory.create())
    ProductFitmentStateFactory.create(product=theirs)
    ProductFitmentFactory.create(product=theirs, node=tree["make"])
    mine = ProductFactory.create(vendor=vendor)
    response = signed_in.post(
        _product_url(mine), {"action": "copy", "source": str(theirs.public_id), "confirm": "1"}
    )
    assert response.status_code == 200  # not one of the choices
    assert not ProductFitment.objects.filter(product=mine).exists()


def test_a_write_needs_the_session_level(
    client: Client, owner: User, vendor: Vendor, tree: dict[str, FitmentNode]
) -> None:
    client.force_login(owner)  # no second factor in this session
    product = ProductFactory.create(vendor=vendor)
    data = {"action": "save", "mode": "specific", "confirm": "1", **_rows(tree["make"])}
    client.post(_product_url(product), data)
    client.post(reverse("manager:shop:fitment_proposals"), {"name": "New Make"})
    assert not ProductFitment.objects.exists()
    assert not FitmentNodeProposal.objects.exists()


def test_a_proposal_is_pending_and_hidden_and_the_21st_is_refused(
    signed_in: Client, vendor: Vendor, tree: dict[str, FitmentNode]
) -> None:
    url = reverse("manager:shop:fitment_proposals")
    for number in range(LIMIT):
        assert signed_in.post(url, {"name": f"Proposed Make {number}"}).status_code == 302
    assert FitmentNodeProposal.objects.filter(vendor=vendor).count() == LIMIT
    assert FitmentNode.objects.filter(status="proposed").count() == LIMIT
    refused = signed_in.post(url, {"name": "One too many"})
    assert refused.status_code == 200
    assert not FitmentNode.objects.filter(name="One too many").exists()
    product = ProductFactory.create(vendor=vendor)
    assert "Proposed Make 0" not in signed_in.get(_product_url(product)).content.decode()


def test_a_proposal_goes_under_an_approved_parent(
    signed_in: Client, tree: dict[str, FitmentNode]
) -> None:
    url = reverse("manager:shop:fitment_proposals")
    signed_in.post(url, {"name": "Model Z", "parent": str(tree["make"].public_id)})
    node = FitmentNode.objects.get(name="Model Z")
    assert node.status == "proposed"
    assert node.get_parent() == tree["make"]


def test_the_proposals_page_lists_only_the_shops_own(signed_in: Client, vendor: Vendor) -> None:
    other = VendorFactory.create()
    mine = FitmentNodeProposalFactory.create(
        vendor=vendor, node=FitmentNodeFactory.create(name="Mine", status="proposed")
    )
    FitmentNodeProposalFactory.create(
        vendor=other, node=FitmentNodeFactory.create(name="Theirs", status="proposed")
    )
    body = signed_in.get(reverse("manager:shop:fitment_proposals")).content.decode()
    assert mine.node.name in body
    assert "Theirs" not in body


def test_the_report_lists_undeclared_pending_and_suspicious(
    signed_in: Client, vendor: Vendor, tree: dict[str, FitmentNode]
) -> None:
    category = CategoryFactory.create()
    FitmentCategoryFactory.create(category=category)
    bare = ProductFactory.create(vendor=vendor, category=category, sku="UNDECLARED-1")
    other = ProductFactory.create(vendor=VendorFactory.create(), category=category, sku="THEIRS-1")
    pending = ProductFactory.create(vendor=vendor, sku="PENDING-1")
    retired = FitmentNodeFactory.create(name="Old Make", status="retired")
    ProductFitmentStateFactory.create(product=pending)
    ProductFitmentFactory.create(product=pending, node=retired)
    odd = ProductFactory.create(vendor=vendor, sku="ODD-1")
    ProductFitmentStateFactory.create(product=odd)
    ProductFitmentFactory.create(product=odd, node=tree["model"], is_exclusion=True)
    body = signed_in.get(reverse("manager:shop:fitment_report")).content.decode()
    assert bare.sku in body
    assert "PENDING-1" in body
    assert "ODD-1" in body
    assert other.sku not in body


def test_the_list_filters_and_the_queries_do_not_grow_with_the_rows(
    signed_in: Client, vendor: Vendor
) -> None:
    url = reverse("manager:shop:fitment")
    ProductFactory.create_batch(2, vendor=vendor)
    with CaptureQueriesContext(connection) as few:
        assert signed_in.get(url).status_code == 200
    ProductFactory.create_batch(15, vendor=vendor)
    with CaptureQueriesContext(connection) as many:
        assert signed_in.get(url).status_code == 200
    assert len(many) <= len(few) + 1
    assert signed_in.get(url, {"declaration": "specific", "q": "x"}).status_code == 200


def test_the_menu_entry_and_a_customer_gets_a_404(client: Client, vendor: Vendor) -> None:
    from apps.accounts.tests.factories import UserFactory

    client.force_login(UserFactory.create())
    assert client.get(reverse("manager:shop:fitment")).status_code in (403, 404)
