"""CORE.3: the cross-tenant harness finds every endpoint and builds its URLs (no database)."""

import uuid

import pytest

from apps.vendors.models import Vendor, VendorMember, VendorTranslation
from tests.tenancy import Endpoint, NoVendorData, StaffOnly, endpoints, openapi_path
from tests.tenancy_registry import classify

URLCONF = "tests.unit.tenancy_urls"


def by_key(urlconf: str | None = URLCONF) -> dict[str, Endpoint]:
    return {endpoint.key: endpoint for endpoint in endpoints(urlconf)}


def test_endpoints_are_keyed_by_namespaced_name_or_by_route() -> None:
    assert sorted(by_key()) == [
        "order-line",
        "root",
        "shop:item",
        "shop:page",
        "unnamed/<path:rest>",
    ]


def test_endpoints_know_their_parameters() -> None:
    found = by_key()
    assert found["root"].params == ()
    assert found["order-line"].params == ("pk", "line")
    assert found["shop:item"].params == ("public_id",)
    assert found["shop:page"].params == ("slug",)
    assert found["shop:item"].route == "shop/items/<uuid:public_id>/"


def test_paths_are_built_from_routes_and_regexes() -> None:
    found = by_key()
    item = uuid.UUID(int=7)
    assert found["root"].path() == "/"
    assert found["order-line"].path(pk="12", line="a b") == "/orders/12/lines/a%20b/"
    assert found["shop:item"].path(public_id=str(item)) == f"/shop/items/{item}/"
    assert found["shop:page"].path(slug="about-us") == "/shop/pages/about-us/"
    assert found["unnamed/<path:rest>"].path(rest="x/y") == "/unnamed/x/y"


def test_a_missing_parameter_is_an_error() -> None:
    with pytest.raises(KeyError, match="pk"):
        by_key()["order-line"].path(line="1")


def test_openapi_paths_come_from_routes() -> None:
    assert openapi_path("api/v1/me") == "/api/v1/me"
    assert openapi_path("api/v1/products/<str:sku>/variants") == "/api/v1/products/{sku}/variants"


# --- The project registry


def test_the_project_urlconf_is_enumerated_with_the_admin_and_the_api() -> None:
    found = by_key(None)
    assert "healthz" in found
    assert "admin:vendors_vendor_change" in found
    assert found["api-v1:me"].is_api_operation
    assert not found["healthz"].is_api_operation


def test_admin_object_pages_of_vendor_owned_models_carry_an_object_maker() -> None:
    found = by_key(None)
    change = classify(found["admin:vendors_vendor_change"])
    assert isinstance(change, StaffOnly)
    assert change.make is not None
    users = classify(found["admin:accounts_user_change"])
    assert isinstance(users, StaffOnly)
    assert users.make is None  # users are not vendor data


def test_the_admin_login_page_is_public() -> None:
    assert isinstance(classify(by_key(None)["admin:login"]), NoVendorData)


def test_unknown_endpoints_are_not_classified() -> None:
    assert classify(by_key()["shop:item"]) is None


@pytest.mark.parametrize("model", [Vendor, VendorMember, VendorTranslation])
def test_vendor_owned_models_have_object_makers(model: type[Vendor]) -> None:
    from tests.tenancy_registry import OWNED_OBJECTS

    assert model in OWNED_OBJECTS
