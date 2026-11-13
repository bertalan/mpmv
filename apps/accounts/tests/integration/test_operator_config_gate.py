"""D-32 sensitive group: the PlatformOperator admin needs a passkey window (`operator_config`)."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts import levels
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.models import PlatformOperator
from apps.core.tests.factories import PlatformOperatorFactory

pytestmark = pytest.mark.django_db


def _form() -> dict[str, str]:
    return {
        "legal_name": "Example Marketplace Ltd",
        "country": "at",
        "vat_number": "ATU00000000",
        "tax_regime": "small_business",
        "commission_vat_mode": "none",
        "dac7_authority_country": "AT",
        "valid_from_0": "2027-01-01",
        "valid_from_1": "00:00:00",
    }


def _client(level: str) -> Client:
    client = Client()
    verified_login(client, UserFactory.create(superuser=True), level=level, passkey=True)
    return client


def test_no_window_session_cannot_add_a_row() -> None:
    client = _client(levels.NO_WINDOW)
    assert client.get(reverse("admin:core_platformoperator_add")).status_code != 200
    response = client.post(reverse("admin:core_platformoperator_add"), _form())
    assert response.status_code != 302
    assert PlatformOperator.objects.count() == 0


def test_no_window_session_cannot_post_the_changelist_or_delete() -> None:
    row = PlatformOperatorFactory.create()
    client = _client(levels.NO_WINDOW)
    changelist = reverse("admin:core_platformoperator_changelist")
    assert client.post(changelist, {"action": "delete_selected"}).status_code != 302
    delete_url = reverse("admin:core_platformoperator_delete", args=[row.pk])
    assert client.get(delete_url).status_code != 200
    assert client.get(changelist).status_code == 200  # viewing stays free


def test_open_window_session_can_add_a_row() -> None:
    client = _client(levels.PASSKEY_WINDOW)
    response = client.post(reverse("admin:core_platformoperator_add"), _form())
    assert response.status_code == 302
    assert PlatformOperator.objects.count() == 1
