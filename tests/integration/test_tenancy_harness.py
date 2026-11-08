"""CORE.3: the cross-tenant harness passes isolated views and catches every kind of leak.

The views live in `tests.integration.urls_tenancy`. A harness that cannot fail is worthless, so
each classic mistake (no tenant filter, 403 instead of 404, a change applied before the check,
a URL that never matches, a staff page without the staff check, a page listing every vendor, a
list, export or search without tenant filter, a list that lists nothing) has a test proving the
harness reports it.
"""

import pytest
from django.db import models
from django.test import Client

from apps.api.models import ApiClient
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.tests.factories import VendorMemberFactory, VendorTranslationFactory
from tests.tenancy import (
    ApiOperation,
    CrossTenant,
    Endpoint,
    NoVendorData,
    StaffOnly,
    VendorScoped,
    VendorScopedList,
    api_operations,
    check_api_operation,
    check_endpoint,
    endpoints,
    public_id_of,
    vendor_api_key,
)
from tests.tenancy_registry import OWNED_OBJECTS

URLCONF = "tests.integration.urls_tenancy"
pytestmark = [pytest.mark.django_db, pytest.mark.urls(URLCONF)]


def endpoint(key: str) -> Endpoint:
    return {e.key: e for e in endpoints(URLCONF)}[key]


def staff_member_of(vendor: object) -> VendorMember:
    return VendorMemberFactory.create(vendor=vendor, role=VendorMember.Role.STAFF)


def legal_name(vendor: models.Model) -> str:
    assert isinstance(vendor, Vendor)
    return vendor.legal_name


MEMBER = VendorScoped(make=staff_member_of)
VENDORS = VendorScopedList(make=lambda vendor: vendor, identify=legal_name)
TRANSLATION = VendorScoped(
    make=lambda vendor: VendorTranslationFactory.create(vendor=vendor, language="it"),
    methods=("GET", "POST"),
)


def test_the_cross_tenant_fixture_gives_two_vendors_with_an_owner_each(
    cross_tenant: CrossTenant,
) -> None:
    a, b = cross_tenant.a, cross_tenant.b
    assert a.vendor != b.vendor
    assert a.vendor.members.get().user == a.owner
    assert b.vendor.members.get().user == b.owner
    assert a.vendor.members.get().role == VendorMember.Role.OWNER
    assert str(b.vendor.public_id) in cross_tenant.markers()
    assert str(a.vendor.public_id) not in cross_tenant.markers()


# --- Vendor-scoped endpoints


def test_isolated_views_pass(client: Client, cross_tenant: CrossTenant) -> None:
    check_endpoint(client, endpoint("member"), MEMBER, cross_tenant)
    check_endpoint(client, endpoint("translation"), TRANSLATION, cross_tenant)


def test_a_view_without_tenant_filter_is_caught(client: Client, cross_tenant: CrossTenant) -> None:
    with pytest.raises(AssertionError, match="expected 404"):
        check_endpoint(client, endpoint("leaky-member"), MEMBER, cross_tenant)


def test_a_403_for_another_vendors_object_is_caught(
    client: Client, cross_tenant: CrossTenant
) -> None:
    with pytest.raises(AssertionError, match="expected 404, got 403"):
        check_endpoint(client, endpoint("forbidden-member"), MEMBER, cross_tenant)


def test_a_change_to_another_vendors_object_is_caught_even_behind_a_404(
    client: Client, cross_tenant: CrossTenant
) -> None:
    with pytest.raises(AssertionError, match="changed another vendor's"):
        check_endpoint(client, endpoint("leaky-translation"), TRANSLATION, cross_tenant)


def test_a_url_that_never_matches_fails_the_positive_control(
    client: Client, cross_tenant: CrossTenant
) -> None:
    with pytest.raises(AssertionError, match="own object"):
        check_endpoint(client, endpoint("wrong-member"), MEMBER, cross_tenant)


# --- Staff-only endpoints


def test_a_staff_only_view_passes(client: Client, cross_tenant: CrossTenant) -> None:
    access = StaffOnly("Staff page", make=lambda vendor: vendor)
    check_endpoint(client, endpoint("staff-vendor"), access, cross_tenant)


def test_a_staff_view_without_the_staff_check_is_caught(
    client: Client, cross_tenant: CrossTenant
) -> None:
    access = StaffOnly("Staff page", make=lambda vendor: vendor)
    with pytest.raises(AssertionError, match="vendor member got 200"):
        check_endpoint(client, endpoint("leaky-staff"), access, cross_tenant)


# --- Endpoints without vendor data


def test_a_page_without_vendor_data_passes(client: Client, cross_tenant: CrossTenant) -> None:
    check_endpoint(client, endpoint("empty-member-list"), NoVendorData("Nothing"), cross_tenant)


def test_a_page_showing_another_vendors_data_is_caught(
    client: Client, cross_tenant: CrossTenant
) -> None:
    with pytest.raises(AssertionError, match="Vendor B"):
        check_endpoint(client, endpoint("every-vendor"), NoVendorData("Listing"), cross_tenant)


def test_vendor_b_is_seeded_with_every_vendor_owned_model(cross_tenant: CrossTenant) -> None:
    seeded = cross_tenant.seed_b()
    assert {type(obj) for obj in seeded} == set(OWNED_OBJECTS)
    assert all(obj.pk is not None for obj in seeded)
    markers = cross_tenant.markers(*seeded)
    assert all(public_id_of(obj) in markers for obj in seeded)


def test_a_list_of_vendor_data_wrongly_declared_without_vendor_data_is_caught(
    client: Client, cross_tenant: CrossTenant
) -> None:
    """Vendor B owns one object of every kind, so an unfiltered list always has one to leak."""
    access = NoVendorData("Wrongly declared")
    with pytest.raises(AssertionError, match="another vendor's data"):
        check_endpoint(client, endpoint("leaky-member-list"), access, cross_tenant)


# --- Lists, exports and searches of vendor-owned objects

MEMBERS = VendorScopedList(make=staff_member_of)


def test_an_isolated_list_passes(client: Client, cross_tenant: CrossTenant) -> None:
    check_endpoint(client, endpoint("member-list"), MEMBERS, cross_tenant)


def test_a_page_listing_only_the_members_own_vendor_passes(
    client: Client, cross_tenant: CrossTenant
) -> None:
    access = VENDORS
    check_endpoint(client, endpoint("own-profile"), access, cross_tenant)


@pytest.mark.parametrize("key", ["leaky-member-list", "leaky-member-export"])
def test_a_list_or_export_without_tenant_filter_is_caught(
    key: str, client: Client, cross_tenant: CrossTenant
) -> None:
    with pytest.raises(AssertionError, match="another vendor's"):
        check_endpoint(client, endpoint(key), MEMBERS, cross_tenant)


def test_a_search_redirecting_to_another_vendors_object_is_caught(
    client: Client, cross_tenant: CrossTenant
) -> None:
    access = VendorScopedList(make=staff_member_of, kwargs={"term": "staff"})
    with pytest.raises(AssertionError, match="another vendor's"):
        check_endpoint(client, endpoint("leaky-member-search"), access, cross_tenant)


def member_email(member: models.Model) -> str:
    assert isinstance(member, VendorMember)
    return member.user.email


def test_a_list_leaking_through_a_custom_identifier_is_caught(
    client: Client, cross_tenant: CrossTenant
) -> None:
    access = VendorScopedList(make=staff_member_of, identify=member_email)
    with pytest.raises(AssertionError, match="lists another vendor's object"):
        check_endpoint(client, endpoint("leaky-member-emails"), access, cross_tenant)


def test_a_list_of_every_vendor_is_caught(client: Client, cross_tenant: CrossTenant) -> None:
    access = VENDORS
    with pytest.raises(AssertionError, match="Vendor B"):
        check_endpoint(client, endpoint("every-vendor"), access, cross_tenant)


def test_a_list_that_lists_nothing_fails_the_positive_control(
    client: Client, cross_tenant: CrossTenant
) -> None:
    with pytest.raises(AssertionError, match="own object"):
        check_endpoint(client, endpoint("empty-member-list"), MEMBERS, cross_tenant)


# --- API v1 operations, called with Vendor A's API key (CORE.13)

API_URLCONF_SCOPE = "catalog:read"  # vendor-grantable (CORE.14)


def operation(key: str) -> ApiOperation:
    from tests.integration.api_tenancy import tenancy_api

    return {op.key: op for op in api_operations(URLCONF, api=tenancy_api)}[key]


def test_api_operations_carry_the_scope_they_require() -> None:
    assert operation("GET /api/members/{public_id}").scopes == (API_URLCONF_SCOPE,)


def test_the_vendor_api_key_belongs_to_vendor_a_and_has_the_operations_scope(
    client: Client, cross_tenant: CrossTenant
) -> None:
    headers = vendor_api_key((API_URLCONF_SCOPE,))(client, cross_tenant)
    api_client = ApiClient.objects.get()
    assert api_client.vendor == cross_tenant.a.vendor
    assert api_client.scopes == [API_URLCONF_SCOPE]
    assert headers["Authorization"].startswith(f"Bearer mpmv_{api_client.key_prefix}_")


def test_an_isolated_api_operation_passes_with_a_vendor_key(
    client: Client, cross_tenant: CrossTenant
) -> None:
    check_api_operation(client, operation("GET /api/members/{public_id}"), MEMBER, cross_tenant)


def test_an_api_operation_without_tenant_filter_is_caught(
    client: Client, cross_tenant: CrossTenant
) -> None:
    with pytest.raises(AssertionError, match="expected 404, got 200"):
        check_api_operation(
            client, operation("GET /api/leaky/members/{public_id}"), MEMBER, cross_tenant
        )


def test_a_403_from_an_api_operation_is_caught(client: Client, cross_tenant: CrossTenant) -> None:
    with pytest.raises(AssertionError, match="expected 404, got 403"):
        check_api_operation(
            client, operation("GET /api/forbidden/members/{public_id}"), MEMBER, cross_tenant
        )


def test_an_isolated_api_list_passes_with_a_vendor_key(
    client: Client, cross_tenant: CrossTenant
) -> None:
    check_api_operation(client, operation("GET /api/members"), MEMBERS, cross_tenant)


def test_an_api_list_without_tenant_filter_is_caught(
    client: Client, cross_tenant: CrossTenant
) -> None:
    with pytest.raises(AssertionError, match="another vendor's"):
        check_api_operation(client, operation("GET /api/leaky/members"), MEMBERS, cross_tenant)


def test_an_api_list_wrongly_declared_without_vendor_data_is_caught(
    client: Client, cross_tenant: CrossTenant
) -> None:
    access = NoVendorData("Wrongly declared")
    with pytest.raises(AssertionError, match="another vendor's data"):
        check_api_operation(client, operation("GET /api/leaky/members"), access, cross_tenant)


def test_staff_only_is_not_an_api_access_class(client: Client, cross_tenant: CrossTenant) -> None:
    with pytest.raises(TypeError, match="StaffOnly"):
        check_api_operation(
            client, operation("GET /api/members"), StaffOnly("Platform only"), cross_tenant
        )
