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
    """One concrete path per route registered in `space`, parameters filled with "0"."""
    return [
        "/" + re.sub(r"<[^>]+>", "0", route) for route, found in manager_routes() if found is space
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
    """Platform staff until CORE.18 adds the roles: the `is_staff` flag (spec 02 §2.2)."""
    return UserFactory.create(staff=True)


@pytest.fixture
def customer(db: None) -> User:
    return UserFactory.create()
