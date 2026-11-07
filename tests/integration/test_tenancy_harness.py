"""CORE.3: the cross-tenant harness passes isolated views and catches every kind of leak.

The views live in `tests.integration.urls_tenancy`. A harness that cannot fail is worthless, so
each classic mistake (no tenant filter, 403 instead of 404, a change applied before the check,
a URL that never matches, a staff page without the staff check, a page listing every vendor)
has a test proving the harness reports it.
"""

import pytest
from django.test import Client

from apps.vendors.models import VendorMember
from apps.vendors.tests.factories import VendorMemberFactory, VendorTranslationFactory
from tests.tenancy import (
    CrossTenant,
    Endpoint,
    NoVendorData,
    StaffOnly,
    VendorScoped,
    check_endpoint,
    endpoints,
)

URLCONF = "tests.integration.urls_tenancy"
pytestmark = [pytest.mark.django_db, pytest.mark.urls(URLCONF)]


def endpoint(key: str) -> Endpoint:
    return {e.key: e for e in endpoints(URLCONF)}[key]


def staff_member_of(vendor: object) -> VendorMember:
    return VendorMemberFactory.create(vendor=vendor, role=VendorMember.Role.STAFF)


MEMBER = VendorScoped(make=staff_member_of)
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


def test_a_page_showing_only_the_members_own_vendor_passes(
    client: Client, cross_tenant: CrossTenant
) -> None:
    check_endpoint(client, endpoint("own-profile"), NoVendorData("Own profile"), cross_tenant)


def test_a_page_showing_another_vendors_data_is_caught(
    client: Client, cross_tenant: CrossTenant
) -> None:
    with pytest.raises(AssertionError, match="Vendor B"):
        check_endpoint(client, endpoint("every-vendor"), NoVendorData("Listing"), cross_tenant)
