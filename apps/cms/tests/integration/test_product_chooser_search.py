"""The product chooser of the page bodies: search, filters and vendor scoping (CMS.10, CMS.11).

An editor finds a product by its name (in any language), SKU, brand or vendor, and can narrow the
list by vendor, brand and category. Only listed products (published, of an active vendor) are
offered, in the list, in the "chosen" step and in the multiple-choice step alike. A vendor member
who reaches the chooser (the middleware keeps vendors out of the admin; this is the second line)
sees only the products of their own vendors, and a crafted request for another vendor's product
lists nothing and returns 404.
"""

import json
import secrets
from typing import Any

import pytest
from django.contrib.auth.models import Group
from django.http import Http404, HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductTranslationFactory,
)
from apps.cms import roles
from apps.cms.choosers import product_chooser
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


def _product(sku: str, title: str, vendor: Vendor, **kwargs: Any) -> Product:
    kwargs.setdefault("status", Product.Status.PUBLISHED)
    product: Product = ProductFactory.create(sku=sku, vendor=vendor, **kwargs)
    ProductTranslationFactory.create(
        product=product, title=title, slug=sku.lower(), status=PUBLISHED
    )
    ProductTranslationFactory.create(
        product=product, language="it", title=f"{title} italiano", slug=f"{sku.lower()}-it",
        status=PUBLISHED,
    )  # fmt: skip
    return product


@pytest.fixture
def editor(client: Client) -> User:
    user: User = UserFactory.create(superuser=True)
    verified_login(client, user)
    return user


@pytest.fixture
def pottery() -> Vendor:
    vendor: Vendor = VendorFactory.create(
        display_name="Pottery Studio", status=Vendor.Status.ACTIVE
    )
    return vendor


@pytest.fixture
def smithy() -> Vendor:
    vendor: Vendor = VendorFactory.create(display_name="Iron Smithy", status=Vendor.Status.ACTIVE)
    return vendor


def _results(client: Client, **params: str) -> str:
    response = client.get(reverse("cms_product_chooser:choose_results"), params)
    assert response.status_code == 200
    return response.content.decode()


def _ids_in(html: str, *products: Product) -> list[bool]:
    return [product.sku in html for product in products]


# --- Search


def test_search_finds_a_product_by_name_in_any_language(
    client: Client, editor: User, pottery: Vendor
) -> None:
    bowl = _product("BWL-1", "Blue bowl", pottery)
    jug = _product("JUG-1", "Red jug", pottery)
    assert _ids_in(_results(client, q="blue"), bowl, jug) == [True, False]
    assert _ids_in(_results(client, q="italiano"), bowl, jug) == [True, True]
    assert _ids_in(_results(client, q="rosso"), bowl, jug) == [False, False]


def test_search_finds_a_product_by_sku(client: Client, editor: User, pottery: Vendor) -> None:
    bowl = _product("BWL-1", "Blue bowl", pottery)
    jug = _product("JUG-1", "Red jug", pottery)
    assert _ids_in(_results(client, q="jug-"), bowl, jug) == [False, True]


def test_search_finds_a_product_by_brand(client: Client, editor: User, pottery: Vendor) -> None:
    bowl = _product("BWL-1", "Blue bowl", pottery, brand=BrandFactory.create(name="Kiln & Co"))
    jug = _product("JUG-1", "Red jug", pottery, brand=BrandFactory.create(name="Wheelhouse"))
    assert _ids_in(_results(client, q="kiln"), bowl, jug) == [True, False]


def test_search_finds_a_product_by_vendor(
    client: Client, editor: User, pottery: Vendor, smithy: Vendor
) -> None:
    bowl = _product("BWL-1", "Blue bowl", pottery)
    nail = _product("NAIL-1", "Iron nail", smithy)
    assert _ids_in(_results(client, q="smithy"), bowl, nail) == [False, True]


def test_search_never_offers_an_unlisted_product(
    client: Client, editor: User, pottery: Vendor
) -> None:
    draft = _product("DRAFT-1", "Blue draft", pottery, status=Product.Status.DRAFT)
    closed = _product("CLOSED-1", "Blue closed", VendorFactory.create(status=Vendor.Status.CLOSED))
    shown = _product("SHOWN-1", "Blue shown", pottery)
    assert _ids_in(_results(client, q="blue"), draft, closed, shown) == [False, False, True]


def test_a_product_with_two_matching_texts_is_listed_once(
    client: Client, editor: User, pottery: Vendor
) -> None:
    _product("BWL-1", "Blue bowl", pottery)
    html = _results(client, q="bowl")
    assert html.count("BWL-1") == 1


def test_the_chooser_modal_has_a_search_field(client: Client, editor: User) -> None:
    response = client.get(reverse("cms_product_chooser:choose"))
    html = str(json.loads(response.content)["html"])
    assert 'name="q"' in html


# --- Filters


def test_the_list_can_be_narrowed_by_vendor_brand_and_category(
    client: Client, editor: User, pottery: Vendor, smithy: Vendor
) -> None:
    brand = BrandFactory.create(name="Kiln & Co")
    category = CategoryFactory.create()
    match = _product("BWL-1", "Blue bowl", pottery, brand=brand, category=category)
    other_vendor = _product("NAIL-1", "Iron nail", smithy, brand=brand, category=category)
    other_brand = _product("JUG-1", "Red jug", pottery, category=category)
    other_category = _product("CUP-1", "Tin cup", pottery, brand=brand)
    everything = (match, other_vendor, other_brand, other_category)
    assert _ids_in(_results(client, vendor=str(pottery.pk)), *everything) == [
        True, False, True, True,
    ]  # fmt: skip
    assert _ids_in(_results(client, brand=str(brand.pk)), *everything) == [
        True, True, False, True,
    ]  # fmt: skip
    assert _ids_in(_results(client, category=str(category.pk)), *everything) == [
        True, True, True, False,
    ]  # fmt: skip
    both = _results(client, vendor=str(pottery.pk), brand=str(brand.pk), q="bowl")
    assert _ids_in(both, *everything) == [True, False, False, False]


def test_a_filter_naming_nothing_real_is_ignored_safely(
    client: Client, editor: User, pottery: Vendor
) -> None:
    _product("BWL-1", "Blue bowl", pottery)
    response = client.get(reverse("cms_product_chooser:choose_results"), {"vendor": "999999"})
    assert response.status_code == 200


# --- Scope: the chosen steps


def test_the_chosen_steps_return_only_listed_products(
    client: Client, editor: User, pottery: Vendor
) -> None:
    shown = _product("SHOWN-1", "Blue shown", pottery)
    draft = _product("DRAFT-1", "Blue draft", pottery, status=Product.Status.DRAFT)
    assert client.get(reverse("cms_product_chooser:chosen", args=(shown.pk,))).status_code == 200
    assert client.get(reverse("cms_product_chooser:chosen", args=(draft.pk,))).status_code == 404
    multiple = client.get(
        reverse("cms_product_chooser:chosen_multiple"), {"id": [str(shown.pk), str(draft.pk)]}
    )
    body = multiple.content.decode()
    assert "SHOWN-1" in body
    assert "DRAFT-1" not in body


# --- Scope: a vendor member


def _member_view(url_name: str, user: User, *view_args: Any, **params: Any) -> HttpResponse:
    request = RequestFactory().get("/", params)
    request.user = user
    view = {
        "choose": product_chooser.choose_view,
        "choose_results": product_chooser.choose_results_view,
        "chosen": product_chooser.chosen_view,
        "chosen_multiple": product_chooser.chosen_multiple_view,
    }[url_name]
    response: HttpResponse = view(request, *view_args)
    if hasattr(response, "render"):
        response.render()
    return response


@pytest.fixture
def member() -> User:
    made = VendorMemberFactory.create(
        vendor=VendorFactory.create(display_name="Own Shop", status=Vendor.Status.ACTIVE),
        user=UserFactory.create(password=secrets.token_urlsafe(16)),
    )
    made.user.groups.add(Group.objects.get(name=roles.PAGE_EDITORS))
    user: User = made.user
    return user


def _member_vendor(user: User) -> Vendor:
    vendor: Vendor = user.vendor_memberships.get().vendor
    return vendor


def test_a_vendor_member_lists_and_searches_only_their_own_products(
    member: User, smithy: Vendor
) -> None:
    own = _product("OWN-1", "Blue own", _member_vendor(member))
    foreign = _product("FOREIGN-1", "Blue foreign", smithy)
    for name, params in (
        ("choose_results", {}),
        ("choose_results", {"q": "blue"}),
        ("choose_results", {"q": "FOREIGN"}),
        ("choose_results", {"vendor": str(smithy.pk)}),
    ):
        body = _member_view(name, member, **params).content.decode()
        assert foreign.sku not in body, params
    assert own.sku in _member_view("choose_results", member, q="blue").content.decode()


def test_a_crafted_request_for_another_vendors_product_is_refused(
    member: User, smithy: Vendor
) -> None:
    foreign = _product("FOREIGN-1", "Blue foreign", smithy)
    own = _product("OWN-1", "Blue own", _member_vendor(member))
    with pytest.raises(Http404):
        _member_view("chosen", member, str(foreign.pk))
    assert _member_view("chosen", member, str(own.pk)).status_code == 200
    multiple = _member_view("chosen_multiple", member, id=[str(own.pk), str(foreign.pk)])
    assert "FOREIGN-1" not in multiple.content.decode()
    assert "OWN-1" in multiple.content.decode()


def test_an_editor_without_a_vendor_sees_every_vendor(
    client: Client, pottery: Vendor, smithy: Vendor
) -> None:
    editor_user: User = UserFactory.create(password=secrets.token_urlsafe(16))
    editor_user.groups.add(Group.objects.get(name=roles.PAGE_EDITORS))
    verified_login(client, editor_user)
    bowl = _product("BWL-1", "Blue bowl", pottery)
    nail = _product("NAIL-1", "Iron nail", smithy)
    body = _results(client)
    assert _ids_in(body, bowl, nail) == [True, True]


def test_the_brand_filter_offers_approved_brands_only(
    client: Client, editor: User, pottery: Vendor
) -> None:
    BrandFactory.create(name="Approved Forge")
    BrandFactory.create(name="Pending Forge", status="pending")
    BrandFactory.create(name="Rejected Forge", status="rejected")
    response = client.get(reverse("cms_product_chooser:choose"))
    html = str(json.loads(response.content)["html"])
    assert "Approved Forge" in html
    assert "Pending Forge" not in html
    assert "Rejected Forge" not in html
