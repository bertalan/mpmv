"""CORE.25 (D-32): bank details are a critical action (`payout_details`) in the vendors admin."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts import levels
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db


def _post(client: Client, vendor_pk: int, **fields: str) -> object:
    url = reverse("admin:vendors_vendor_change", args=[vendor_pk])
    return client.post(url, {"iban": "XX00 CHANGED", "_save": "Save", **fields})


def test_changing_bank_details_needs_a_per_action_passkey(client: Client) -> None:
    vendor = VendorFactory.create()
    verified_login(client, UserFactory.create(superuser=True))  # operational, TOTP only
    response = _post(client, vendor.pk)
    assert response.status_code == 302  # type: ignore[attr-defined]
    assert "factor=passkey" in response["Location"]  # type: ignore[index]


def test_changing_bank_details_is_refused_in_restricted_mode(client: Client) -> None:
    vendor = VendorFactory.create()
    verified_login(client, UserFactory.create(superuser=True), level=levels.NO_WINDOW)
    assert _post(client, vendor.pk).status_code == 403  # type: ignore[attr-defined]


def test_other_vendor_edits_are_not_gated(client: Client) -> None:
    vendor = VendorFactory.create()
    verified_login(client, UserFactory.create(superuser=True), level=levels.NO_WINDOW)
    response = client.get(reverse("admin:vendors_vendor_change", args=[vendor.pk]))
    assert response.status_code == 200
