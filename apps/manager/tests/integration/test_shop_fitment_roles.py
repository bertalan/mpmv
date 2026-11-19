"""FIT.3 follow-up: every shop role against the Fits pages (spec 17 §17.3, CORE.2 matrix).

The shop space has two roles, owner and staff; both hold the `products` action, so both reach
every Fits route and every write. Whoever is not a member of the shop (a member of another shop,
a customer, a visitor) gets no page and no write, and a member never sees another shop's product.
"""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory
from apps.fitment.models import FitmentNode, FitmentNodeProposal, ProductFitment
from apps.fitment.tests.factories import FitmentNodeFactory
from apps.vendors import engine
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

SHOP_ROLES = engine.ROLES


@pytest.fixture
def shop(db: None) -> Vendor:
    return VendorFactory.create(display_name="Shop A", status=Vendor.Status.ACTIVE)


@pytest.fixture
def other_shop(db: None) -> Vendor:
    return VendorFactory.create(display_name="Shop B", status=Vendor.Status.ACTIVE)


@pytest.fixture
def make() -> FitmentNode:
    return FitmentNodeFactory.create(name="Make One")


def _member(shop: Vendor, role: str) -> User:
    return VendorMemberFactory.create(vendor=shop, role=role).user


def _rows(node: FitmentNode) -> dict[str, str]:
    return {
        "rows-TOTAL_FORMS": "1",
        "rows-INITIAL_FORMS": "0",
        "rows-MIN_NUM_FORMS": "0",
        "rows-MAX_NUM_FORMS": "1000",
        "rows-0-node": str(node.public_id),
    }


def _product_url(product: Product) -> str:
    return reverse("manager:shop:fitment_product", args=[product.public_id])


def _write_requests(product: Product, node: FitmentNode) -> dict[str, tuple[str, dict[str, str]]]:
    """Every write of the Fits pages, confirmed: name -> (url, data)."""
    return {
        "save": (
            _product_url(product),
            {"action": "save", "mode": "specific", "confirm": "1", **_rows(node)},
        ),
        "bulk": (
            reverse("manager:shop:fitment"),
            {
                "action": "add_fitment",
                "selected": str(product.public_id),
                "confirm": "1",
                **_rows(node),
            },
        ),
        "propose": (reverse("manager:shop:fitment_proposals"), {"name": "New Make"}),
    }


def _nothing_written() -> bool:
    return not ProductFitment.objects.exists() and not FitmentNodeProposal.objects.exists()


def _read_urls(product: Product) -> list[str]:
    return [
        reverse("manager:shop:fitment"),
        _product_url(product),
        reverse("manager:shop:fitment_proposals"),
        reverse("manager:shop:fitment_report"),
    ]


@pytest.mark.parametrize("role", SHOP_ROLES)
def test_every_shop_role_reads_every_fitment_page(client: Client, shop: Vendor, role: str) -> None:
    product = ProductFactory.create(vendor=shop)
    verified_login(client, _member(shop, role))
    urls = _read_urls(product)
    assert [client.get(url).status_code for url in urls] == [200] * len(urls)


@pytest.mark.parametrize("role", SHOP_ROLES)
def test_every_shop_role_may_write_with_a_verified_session(
    client: Client, shop: Vendor, make: FitmentNode, role: str
) -> None:
    product = ProductFactory.create(vendor=shop)
    verified_login(client, _member(shop, role))
    codes = {
        name: client.post(url, data).status_code
        for name, (url, data) in _write_requests(product, make).items()
    }
    assert codes == {"save": 302, "bulk": 302, "propose": 302}
    assert ProductFitment.objects.filter(product=product).exists()
    assert FitmentNodeProposal.objects.filter(vendor=shop).count() == 1


@pytest.mark.parametrize("role", SHOP_ROLES)
def test_a_write_without_the_verified_session_changes_nothing(
    client: Client, shop: Vendor, make: FitmentNode, role: str
) -> None:
    product = ProductFactory.create(vendor=shop)
    client.force_login(_member(shop, role))  # signed in, no second factor
    for url, data in _write_requests(product, make).values():
        assert client.post(url, data).status_code in (302, 403, 404)
    assert _nothing_written()


@pytest.mark.parametrize("role", SHOP_ROLES)
def test_a_member_of_shop_a_never_sees_shop_b_products(
    client: Client, shop: Vendor, other_shop: Vendor, make: FitmentNode, role: str
) -> None:
    mine = ProductFactory.create(vendor=shop, sku="MINE-1")
    theirs = ProductFactory.create(vendor=other_shop, sku="THEIRS-1")
    verified_login(client, _member(shop, role))
    listing = client.get(reverse("manager:shop:fitment")).content.decode()
    assert "MINE-1" in listing
    assert "THEIRS-1" not in listing
    assert client.get(_product_url(mine)).status_code == 200
    assert client.get(_product_url(theirs)).status_code == 404
    report = client.get(reverse("manager:shop:fitment_report")).content.decode()
    assert "THEIRS-1" not in report
    for name, (url, data) in _write_requests(theirs, make).items():
        if name != "propose":
            assert client.post(url, data).status_code == 404, name
    assert not ProductFitment.objects.filter(product=theirs).exists()


@pytest.mark.parametrize("role", SHOP_ROLES)
def test_a_member_of_another_shop_cannot_reach_this_shops_product(
    client: Client, shop: Vendor, other_shop: Vendor, make: FitmentNode, role: str
) -> None:
    product = ProductFactory.create(vendor=shop)
    verified_login(client, _member(other_shop, role))
    assert client.get(reverse("manager:shop:fitment")).status_code == 200  # their own shop
    assert client.get(_product_url(product)).status_code == 404
    url, data = _write_requests(product, make)["save"]
    assert client.post(url, data).status_code == 404
    assert not ProductFitment.objects.exists()


def test_a_user_without_a_shop_and_a_visitor_get_no_fitment_page_and_no_write(
    client: Client, shop: Vendor, make: FitmentNode
) -> None:
    product = ProductFactory.create(vendor=shop)
    for actor in (None, UserFactory.create()):
        if actor is not None:
            verified_login(client, actor)
        assert all(client.get(url).status_code in (302, 403, 404) for url in _read_urls(product))
        for url, data in _write_requests(product, make).values():
            assert client.post(url, data).status_code in (302, 403, 404)
    assert _nothing_written()


def test_the_matrix_gives_both_shop_roles_the_products_action() -> None:
    """The section is gated by `products`: pin who holds it, so a change of the CORE.2 matrix
    that drops staff (or adds a role) shows up here and the tests above are revisited."""
    assert set(SHOP_ROLES) == {"owner", "staff"}
    assert all(engine.is_allowed(role, "products") for role in SHOP_ROLES)
