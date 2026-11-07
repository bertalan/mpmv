"""CORE.3: negative cross-tenant tests, generated for every endpoint and vendor-owned model.

Spec 02 §2.2: "Vendor A requests Vendor B's object → 404" is mandatory for every endpoint. The
tests below are generated from the root URLconf, the API v1 OpenAPI description and the model
registry, and the guards fail when something new is not declared in `tests.tenancy_registry`:

- a vendor-owned model (a ForeignKey chain to Vendor) without the scoped manager or an object
  maker;
- a URL pattern or an API operation without an access class.
"""

import pytest
from apps.vendors.scoping import get_vendor_object_or_404, is_vendor_scoped, scope_target
from django.http import Http404
from django.test import Client

from apps.vendors.models import Vendor
from tests.tenancy import (
    ApiOperation,
    CrossTenant,
    Endpoint,
    api_operations,
    check_endpoint,
    endpoints,
    vendor_owned_models,
)
from tests.tenancy_registry import (
    API_OPERATIONS,
    ENDPOINTS,
    NOT_VENDOR_OWNED,
    OWNED_OBJECTS,
    classify,
)

pytestmark = pytest.mark.django_db

WEB_ENDPOINTS = [e for e in endpoints() if not e.is_api_operation]
API_ENDPOINTS = [e for e in endpoints() if e.is_api_operation]
OPERATIONS = api_operations()
OWNED_MODELS = vendor_owned_models(exclude=NOT_VENDOR_OWNED)


# --- Guards: nothing vendor-owned or reachable is left undeclared


def test_vendor_owned_models_are_found_through_foreign_keys() -> None:
    labels = {model._meta.label for model in OWNED_MODELS}
    assert {"vendors.Vendor", "vendors.VendorMember", "vendors.VendorTranslation"} <= labels


def test_every_vendor_owned_model_uses_the_scoped_manager() -> None:
    unscoped = sorted(m._meta.label for m in OWNED_MODELS if not is_vendor_scoped(m))
    assert not unscoped, (
        "Give these vendor-owned models `objects = VendorScopedManager()` and a `vendor_scope` "
        "path (apps.vendors.scoping), or list them in NOT_VENDOR_OWNED with a reason: "
        + ", ".join(unscoped)
    )


def test_every_scoped_model_reaches_vendor_through_its_scope_path() -> None:
    wrong = sorted(m._meta.label for m in OWNED_MODELS if scope_target(m) is not Vendor)
    assert not wrong, "`vendor_scope` does not lead to Vendor for: " + ", ".join(wrong)


def test_every_vendor_owned_model_has_an_object_maker() -> None:
    missing = sorted(m._meta.label for m in OWNED_MODELS if m not in OWNED_OBJECTS)
    stale = sorted(m._meta.label for m in OWNED_OBJECTS if m not in OWNED_MODELS)
    assert not missing, "Add to tests.tenancy_registry.OWNED_OBJECTS: " + ", ".join(missing)
    assert not stale, "Not vendor-owned, remove from OWNED_OBJECTS: " + ", ".join(stale)


def test_every_endpoint_is_classified() -> None:
    missing = sorted(e.key for e in WEB_ENDPOINTS if classify(e) is None)
    assert not missing, (
        "Declare the tenant access of these endpoints in tests.tenancy_registry.ENDPOINTS: "
        + ", ".join(missing)
    )


def test_the_endpoint_registry_has_no_stale_entries() -> None:
    keys = {e.key for e in WEB_ENDPOINTS}
    stale = sorted(set(ENDPOINTS) - keys)
    assert not stale, "No such endpoint any more, remove from ENDPOINTS: " + ", ".join(stale)


def test_every_api_operation_is_classified() -> None:
    keys = {op.key for op in OPERATIONS}
    missing = sorted(keys - set(API_OPERATIONS))
    stale = sorted(set(API_OPERATIONS) - keys)
    assert not missing, "Declare in tests.tenancy_registry.API_OPERATIONS: " + ", ".join(missing)
    assert not stale, "No such operation any more, remove from API_OPERATIONS: " + ", ".join(stale)


def test_every_api_url_belongs_to_a_described_operation() -> None:
    described = {op.endpoint.key for op in OPERATIONS}
    hidden = sorted(e.key for e in API_ENDPOINTS if e.key not in described)
    assert not hidden, "API URLs missing from the OpenAPI description: " + ", ".join(hidden)


# --- Generated negative tests


@pytest.mark.parametrize("model", OWNED_MODELS, ids=lambda m: m._meta.label)
def test_scoped_queries_never_return_another_vendors_rows(
    model: type[Vendor], cross_tenant: CrossTenant
) -> None:
    a, b = cross_tenant.a, cross_tenant.b
    make = OWNED_OBJECTS[model]
    mine, theirs = make(a.vendor), make(b.vendor)
    rows = model._default_manager

    assert mine in rows.for_member(a.owner)
    assert theirs not in rows.for_member(a.owner)
    assert theirs not in rows.for_vendor(a.vendor)
    assert get_vendor_object_or_404(model, a.owner, pk=mine.pk) == mine
    with pytest.raises(Http404):
        get_vendor_object_or_404(model, a.owner, pk=theirs.pk)


@pytest.mark.parametrize("endpoint", WEB_ENDPOINTS, ids=lambda e: e.key)
def test_a_vendor_member_cannot_reach_another_vendors_data(
    endpoint: Endpoint, client: Client, cross_tenant: CrossTenant
) -> None:
    access = classify(endpoint)
    assert access is not None, f"{endpoint.key} is not declared in tests.tenancy_registry"
    check_endpoint(client, endpoint, access, cross_tenant)


@pytest.mark.parametrize("operation", OPERATIONS, ids=lambda op: op.key)
def test_an_api_caller_cannot_reach_another_vendors_data(
    operation: ApiOperation, client: Client, cross_tenant: CrossTenant
) -> None:
    access = API_OPERATIONS.get(operation.key)
    assert access is not None, f"{operation.key} is not declared in tests.tenancy_registry"
    check_endpoint(client, operation.endpoint, access, cross_tenant, method=operation.method)
