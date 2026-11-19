"""VEN.36: a bulky shipping method without a maximum return-cost estimate is flagged."""

import pytest

from apps.shipping import services
from apps.shipping.tests.factories import ShippingMethodFactory
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db


def test_a_bulky_method_without_an_estimate_is_flagged() -> None:
    vendor = VendorFactory.create()
    bulky = ShippingMethodFactory.create(vendor=vendor, key="pallet", not_returnable_by_post=True)
    ShippingMethodFactory.create(vendor=vendor, key="parcel")
    assert services.methods_missing_return_estimate(vendor, estimate="") == [bulky]
    assert services.methods_missing_return_estimate(vendor, estimate="  ") == [bulky]


def test_an_estimate_clears_the_flag() -> None:
    vendor = VendorFactory.create()
    ShippingMethodFactory.create(vendor=vendor, not_returnable_by_post=True)
    assert services.methods_missing_return_estimate(vendor, estimate="Up to EUR 120") == []


def test_another_vendors_bulky_method_is_not_listed() -> None:
    ShippingMethodFactory.create(not_returnable_by_post=True)
    assert services.methods_missing_return_estimate(VendorFactory.create(), estimate="") == []
