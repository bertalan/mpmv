"""ARC.7: staff admin for PlatformOperator; history rows are viewable, never edited or deleted."""

from collections.abc import Callable
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.core.models import AuditLog, PlatformOperator
from apps.core.tests.factories import PlatformOperatorFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(
    client: Client, superuser: Any, mfa_login: Callable[[Client, Any], Client]
) -> Client:
    # `superuser` and `mfa_login` come from the root conftest: core must not import accounts
    # (spec 01 §1.3). Staff need a verified second factor (CORE.4).
    return mfa_login(client, superuser)


def _form(**overrides: str) -> dict[str, str]:
    data = {
        "legal_name": "Example Marketplace Ltd",
        "country": "at",
        "vat_number": "ATU00000000",
        "tax_regime": "small_business",
        "commission_vat_mode": "none",
        "dac7_authority_country": "AT",
        "valid_from_0": "2027-01-01",
        "valid_from_1": "00:00:00",
    }
    data.update(overrides)
    return data


def test_changelist_lists_the_configuration_history(staff_client: Client) -> None:
    PlatformOperatorFactory.create(legal_name="Example Marketplace Ltd")
    response = staff_client.get(reverse("admin:core_platformoperator_changelist"))
    assert response.status_code == 200
    assert b"Example Marketplace Ltd" in response.content


def test_adding_through_the_admin_goes_through_the_audited_service(
    staff_client: Client,
) -> None:
    response = staff_client.post(reverse("admin:core_platformoperator_add"), _form())
    assert response.status_code == 302, response.content
    stored = PlatformOperator.objects.get()
    assert stored.country == "AT"
    entry = AuditLog.objects.get(action="core.platform_operator.configured")
    assert entry.source == "admin"
    assert entry.actor_label == "staff:staff@example.com"


def test_adding_a_new_row_closes_the_current_one(staff_client: Client) -> None:
    staff_client.post(reverse("admin:core_platformoperator_add"), _form())
    staff_client.post(
        reverse("admin:core_platformoperator_add"),
        _form(country="DE", dac7_authority_country="DE", valid_from_0="2029-01-01"),
    )
    old, new = PlatformOperator.objects.order_by("valid_from")
    assert old.valid_to == new.valid_from
    assert new.valid_to is None


def test_add_form_reports_a_start_that_is_not_after_the_current_one(
    staff_client: Client,
) -> None:
    staff_client.post(reverse("admin:core_platformoperator_add"), _form())
    response = staff_client.post(reverse("admin:core_platformoperator_add"), _form())
    assert response.status_code == 200
    assert "valid_from" in response.context["adminform"].form.errors
    assert PlatformOperator.objects.count() == 1


def test_existing_rows_cannot_be_changed_from_the_admin(staff_client: Client) -> None:
    op = PlatformOperatorFactory.create()
    url = reverse("admin:core_platformoperator_change", args=[op.pk])
    assert staff_client.get(url).status_code == 200  # view-only
    response = staff_client.post(url, _form(vat_number="ATU99999999"))
    assert response.status_code == 403
    assert PlatformOperator.objects.get(pk=op.pk).vat_number == op.vat_number


def test_rows_cannot_be_deleted_from_the_admin(staff_client: Client) -> None:
    op = PlatformOperatorFactory.create()
    url = reverse("admin:core_platformoperator_delete", args=[op.pk])
    assert staff_client.post(url, {"post": "yes"}).status_code == 403
    assert PlatformOperator.objects.filter(pk=op.pk).exists()
