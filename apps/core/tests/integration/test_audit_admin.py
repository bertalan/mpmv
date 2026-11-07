"""SEC.4: the staff view of the audit log: read-only, filterable, before/after on every entry."""

import datetime as dt
from collections.abc import Callable
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.tests.factories import AuditLogFactory, PlatformOperatorFactory, utc

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:core_auditlog_changelist"
VENDOR_ID = "6f1c1f2e-0000-4000-8000-000000000001"


@pytest.fixture
def staff_client(
    client: Client, superuser: Any, mfa_login: Callable[[Client, Any], Client]
) -> Client:
    # `superuser` and `mfa_login` come from the root conftest: core must not import accounts.
    return mfa_login(client, superuser)


def listed(client: Client, **params: str) -> list[AuditLog]:
    response = client.get(reverse(CHANGELIST), params)
    assert response.status_code == 200, response.content
    return list(response.context["cl"].result_list)


def test_changelist_shows_who_did_what_to_which_object(staff_client: Client) -> None:
    AuditLogFactory.create(
        actor_label="staff:alice@example.com", action="vendor.updated", target_key="v-1"
    )
    response = staff_client.get(reverse(CHANGELIST))
    content = response.content.decode()
    assert "staff:alice@example.com" in content
    assert "vendor.updated" in content
    assert "v-1" in content


def test_filter_by_actor(staff_client: Client) -> None:
    alice = AuditLogFactory.create(actor_label="staff:alice@example.com")
    AuditLogFactory.create(actor_label="staff:bob@example.com")
    assert listed(staff_client, actor="alice") == [alice]


def test_filter_by_action(staff_client: Client) -> None:
    status = AuditLogFactory.create(action="vendor.status_changed")
    AuditLogFactory.create(action="vendor.updated")
    assert listed(staff_client, action="vendor.status_changed") == [status]


def test_filter_by_object_type(staff_client: Client) -> None:
    operator = AuditLogFactory.create(target_model="core.platformoperator")
    AuditLogFactory.create(target_model="vendors.vendor")
    assert listed(staff_client, target_model="core.platformoperator") == [operator]


def test_filter_by_date_range(staff_client: Client) -> None:
    entry = AuditLogFactory.create()
    today = timezone.localdate()
    yesterday = (today - dt.timedelta(days=1)).isoformat()
    tomorrow = (today + dt.timedelta(days=1)).isoformat()
    assert listed(staff_client, created_from=today.isoformat()) == [entry]
    assert listed(staff_client, created_from=yesterday, created_to=today.isoformat()) == [entry]
    assert listed(staff_client, created_to=yesterday) == []
    assert listed(staff_client, created_from=tomorrow) == []


def test_an_invalid_date_is_refused_with_the_admin_error_page(staff_client: Client) -> None:
    response = staff_client.get(reverse(CHANGELIST), {"created_from": "yesterday"})
    assert response.status_code == 302
    assert "e=1" in response["Location"]


def test_filter_by_vendor(staff_client: Client) -> None:
    mine = AuditLogFactory.create(vendor_key=VENDOR_ID)
    AuditLogFactory.create(vendor_key="")
    assert listed(staff_client, vendor=VENDOR_ID) == [mine]


def test_the_detail_page_shows_before_and_after(staff_client: Client) -> None:
    entry = AuditLogFactory.create(before={"rate": "22.00"}, after={"rate": "23.00"})
    response = staff_client.get(reverse("admin:core_auditlog_change", args=[entry.pk]))
    assert response.status_code == 200
    content = response.content.decode()
    assert "22.00" in content
    assert "23.00" in content


def test_staff_cannot_add_change_or_delete_entries(staff_client: Client) -> None:
    entry = AuditLogFactory.create()
    assert staff_client.get(reverse("admin:core_auditlog_add")).status_code == 403
    change = reverse("admin:core_auditlog_change", args=[entry.pk])
    staff_client.post(change, {"action": "tampered"})
    assert AuditLog.objects.get(pk=entry.pk).action == entry.action
    delete = reverse("admin:core_auditlog_delete", args=[entry.pk])
    assert staff_client.post(delete, {"post": "yes"}).status_code == 403
    assert AuditLog.objects.filter(pk=entry.pk).exists()


def test_the_bulk_delete_action_is_not_offered(staff_client: Client) -> None:
    AuditLogFactory.create()
    response = staff_client.get(reverse(CHANGELIST))
    assert "delete_selected" not in response.content.decode()


def test_anonymous_visitors_cannot_read_the_audit_log(client: Client) -> None:
    AuditLogFactory.create()
    response = client.get(reverse(CHANGELIST))
    assert response.status_code == 302
    assert reverse("admin:login") in response["Location"]


# --- Done when: a rate-like configuration change is visible with before/after.
# Rate models (TAX, SHP) do not exist yet; the platform operator is the versioned configuration
# that already does, and rates follow the same pattern (see docs/dev/audit-trail.md).


def _operator_form(**overrides: str) -> dict[str, str]:
    data = {
        "legal_name": "Example Marketplace Ltd",
        "country": "at",
        "vat_number": "ATU00000000",
        "tax_regime": "small_business",
        "commission_vat_mode": "none",
        "einvoice_channel": "none",
        "dac7_authority_country": "AT",
        "valid_from_0": "2027-01-01",
        "valid_from_1": "00:00:00",
    }
    data.update(overrides)
    return data


def test_a_configuration_change_is_visible_in_the_audit_view_with_before_and_after(
    staff_client: Client,
) -> None:
    staff_client.post(reverse("admin:core_platformoperator_add"), _operator_form())
    staff_client.post(
        reverse("admin:core_platformoperator_add"),
        _operator_form(
            country="de",
            vat_number="DE000000000",
            commission_vat_mode="standard",
            dac7_authority_country="DE",
            valid_from_0="2029-01-01",
        ),
    )

    entries = listed(
        staff_client,
        target_model="core.platformoperator",
        action="core.platform_operator.configured",
    )
    newest = entries[0]
    assert newest.before is not None
    assert newest.before["country"] == "AT"
    assert newest.after is not None
    assert newest.after["country"] == "DE"
    assert newest.actor_label == "staff:staff@example.com"

    detail = staff_client.get(reverse("admin:core_auditlog_change", args=[newest.pk]))
    assert detail.status_code == 200
    content = detail.content.decode()
    for value in ("ATU00000000", "DE000000000", "commission_vat_mode", "standard"):
        assert value in content


def test_the_change_table_lists_only_the_fields_that_changed(staff_client: Client) -> None:
    entry = AuditLogFactory.create(
        before={"country": "AT", "legal_name": "Same"},
        after={"country": "DE", "legal_name": "Same"},
    )
    response = staff_client.get(reverse("admin:core_auditlog_change", args=[entry.pk]))
    assert response.context["audit_changes"] == [("country", "AT", "DE")]


def test_a_superseding_configuration_shows_what_changed_from_the_previous_one(
    staff_client: Client,
) -> None:
    PlatformOperatorFactory.create(valid_from=utc(2027), valid_to=utc(2028), country="AT")
    staff_client.post(
        reverse("admin:core_platformoperator_add"),
        _operator_form(country="de", dac7_authority_country="DE", valid_from_0="2029-01-01"),
    )
    entry = AuditLog.objects.get(action="core.platform_operator.configured")
    response = staff_client.get(reverse("admin:core_auditlog_change", args=[entry.pk]))
    changed = {name for name, _, _ in response.context["audit_changes"]}
    assert {"country", "dac7_authority_country", "valid_from", "valid_to"} <= changed
