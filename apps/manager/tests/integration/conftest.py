"""Shared actors for the Manager tests (CORE.17)."""

import re

import pytest

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.manager.checks import manager_routes
from apps.manager.spaces import Space
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

SHOP = "/manager/shop/"
PICKER = "/manager/shop/vendor/"
PLATFORM = "/manager/platform/"
LOGIN = "/account/login/"


def sample_paths(space: Space) -> list[str]:
    """One path per route registered in `space`: "0" for parameters, a nil UUID for `public_id`."""
    nil = "00000000-0000-0000-0000-000000000000"
    return [
        "/" + re.sub(r"<[^>]+>", "0", route.replace("<uuid:public_id>", nil))
        for route, found in manager_routes()
        if found is space
    ]


@pytest.fixture
def vendor(db: None) -> Vendor:
    return VendorFactory.create(display_name="Shop One")


@pytest.fixture
def other_vendor(db: None) -> Vendor:
    return VendorFactory.create(display_name="Shop Two")


@pytest.fixture
def owner(vendor: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor, role="owner").user


@pytest.fixture
def platform_staff(db: None) -> User:
    """Platform staff until CORE.18 adds the roles: the platform owner permission (spec 02 Q-34).

    Never the `is_staff` flag: Wagtail or Django admin access does not make platform staff.
    """
    return UserFactory.create(permissions=["core.platform_owner"])


@pytest.fixture
def customer(db: None) -> User:
    return UserFactory.create()
