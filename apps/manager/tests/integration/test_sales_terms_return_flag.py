"""VEN.36: the sales terms page flags a bulky shipping method without a return-cost estimate."""

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.shipping.tests.factories import ShippingMethodFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import SalesTermsVersionFactory

from .conftest import PICKER

pytestmark = pytest.mark.django_db

FLAG = "has no maximum return-cost estimate"


def _page(client: Client, owner: User, vendor: Vendor) -> str:
    verified_login(client, owner, level=levels.NO_WINDOW)
    assert client.post(PICKER, {"vendor": str(vendor.public_id)}).status_code == 302
    response = client.get(reverse("manager:shop:sales_terms"))
    assert response.status_code == 200
    return str(response.content.decode())


def _version(vendor: Vendor, **over: Any) -> None:
    SalesTermsVersionFactory.create(vendor=vendor, **over)


def test_a_bulky_method_without_an_estimate_is_flagged(
    client: Client, owner: User, vendor: Vendor
) -> None:
    ShippingMethodFactory.create(vendor=vendor, key="pallet", not_returnable_by_post=True)
    _version(vendor, return_cost_info="")
    assert FLAG in _page(client, owner, vendor)


def test_a_bulky_method_with_an_estimate_is_not_flagged(
    client: Client, owner: User, vendor: Vendor
) -> None:
    ShippingMethodFactory.create(vendor=vendor, key="pallet", not_returnable_by_post=True)
    _version(vendor, return_cost_info="Up to EUR 120")
    assert FLAG not in _page(client, owner, vendor)


def test_every_version_offers_its_pdf(client: Client, owner: User, vendor: Vendor) -> None:
    _version(vendor)
    assert "/pdf/" in _page(client, owner, vendor)
