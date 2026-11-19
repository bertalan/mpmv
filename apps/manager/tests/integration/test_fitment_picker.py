"""FIT.3 follow-up 2/2: the JSON endpoint of the cascading node picker, and the page around it."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.catalog.tests.factories import ProductFactory
from apps.fitment.models import FitmentNode
from apps.fitment.tests.factories import FitmentNodeFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

URL = "manager:shop:fitment_nodes"


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
def make() -> FitmentNode:
    return FitmentNodeFactory.create(name="Make One")


def _get(client: Client, **params: str) -> object:
    return client.get(reverse(URL), params)


def test_the_roots_are_the_approved_top_nodes(signed_in: Client, make: FitmentNode) -> None:
    FitmentNodeFactory.create(name="Make Proposed", status=FitmentNode.Status.PROPOSED)
    FitmentNodeFactory.create(name="Make Retired", status=FitmentNode.Status.RETIRED)
    FitmentNodeFactory.create(name="Model A", parent=make)
    response = _get(signed_in)
    data = response.json()  # type: ignore[attr-defined]
    assert response.status_code == 200  # type: ignore[attr-defined]
    assert [r["name"] for r in data["results"]] == ["Make One"]
    assert data["results"][0]["has_children"] is True
    assert data["more"] is False
    assert response["Cache-Control"] == "private, no-store"  # type: ignore[index]


def test_the_children_are_the_active_ones_of_that_parent_only(
    signed_in: Client, make: FitmentNode
) -> None:
    other = FitmentNodeFactory.create(name="Make Two")
    FitmentNodeFactory.create(name="Model A", parent=make)
    FitmentNodeFactory.create(name="Model P", parent=make, status=FitmentNode.Status.PROPOSED)
    FitmentNodeFactory.create(name="Model R", parent=make, status=FitmentNode.Status.RETIRED)
    FitmentNodeFactory.create(name="Model Z", parent=other)
    variant_parent = FitmentNode.objects.get(name="Model A")
    FitmentNodeFactory.create(name="Variant 1", parent=variant_parent)
    data = _get(signed_in, parent=str(make.public_id)).json()  # type: ignore[attr-defined]
    assert [r["name"] for r in data["results"]] == ["Model A"]
    assert data["results"][0]["has_children"] is True
    deeper = _get(signed_in, parent=str(variant_parent.public_id)).json()  # type: ignore[attr-defined]
    assert [r["name"] for r in deeper["results"]] == ["Variant 1"]
    assert deeper["results"][0]["has_children"] is False


@pytest.mark.parametrize("parent", ["not-a-uuid", "00000000-0000-0000-0000-000000000000"])
def test_an_unknown_parent_is_a_404(signed_in: Client, parent: str) -> None:
    assert _get(signed_in, parent=parent).status_code == 404  # type: ignore[attr-defined]


def test_a_proposed_parent_is_a_404(signed_in: Client) -> None:
    proposed = FitmentNodeFactory.create(status=FitmentNode.Status.PROPOSED)
    assert _get(signed_in, parent=str(proposed.public_id)).status_code == 404  # type: ignore[attr-defined]


def test_the_results_are_bounded(
    signed_in: Client, monkeypatch: pytest.MonkeyPatch, make: FitmentNode
) -> None:
    monkeypatch.setattr("apps.manager.fitment_picker.LIMIT", 2)
    for index in range(3):
        FitmentNodeFactory.create(name=f"Model {index}", parent=make)
    data = _get(signed_in, parent=str(make.public_id)).json()  # type: ignore[attr-defined]
    assert len(data["results"]) == 2
    assert data["more"] is True


def test_a_visitor_is_sent_to_sign_in_and_a_non_member_is_refused(
    client: Client, make: FitmentNode
) -> None:
    visitor = _get(client)
    assert visitor.status_code == 302  # type: ignore[attr-defined]
    assert "/login/" in visitor["Location"]  # type: ignore[index]
    outsider = VendorMemberFactory.create(role="owner").user
    outsider.vendor_memberships.all().delete()
    verified_login(client, outsider)
    assert _get(client).status_code in {403, 404}  # type: ignore[attr-defined]


def test_a_post_is_refused(signed_in: Client) -> None:
    assert signed_in.post(reverse(URL)).status_code == 405


def test_the_page_keeps_its_native_select_and_loads_the_script(
    signed_in: Client, vendor: Vendor, make: FitmentNode
) -> None:
    product = ProductFactory.create(vendor=vendor)
    html = signed_in.get(
        reverse("manager:shop:fitment_product", args=[product.public_id])
    ).content.decode()
    assert 'data-fitment-picker=""' in html
    assert 'name="rows-0-node"' in html
    assert "Make One" in html
    assert reverse(URL) in html
    assert "manager/fitment-picker.js" in html
    assert "<script>" not in html
