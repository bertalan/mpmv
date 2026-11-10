"""CMP.3: staff edit GPSR data and safety cases in the admin, through the audited services."""

import datetime as dt
from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.tests.factories import ProductFactory
from apps.compliance.models import GpsrInfo, SafetyCase, VendorGpsrDefaults
from apps.compliance.tests.factories import SafetyCaseFactory
from apps.core.models import AuditLog
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PARTY = {
    "manufacturer_name": "Example Maker S.r.l.",
    "manufacturer_address": "Via Esempio 1, 00100 Roma",
    "manufacturer_contact": "safety@maker.example",
    "manufacturer_country": "IT",
    "eu_responsible_name": "",
    "eu_responsible_address": "",
    "eu_responsible_contact": "",
}


@pytest.fixture
def staff_client(
    client: Client, superuser: User, mfa_login: Callable[[Client, User], Client]
) -> Client:
    return mfa_login(client, superuser)


def test_vendor_defaults_are_added_through_the_service(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    response = staff_client.post(
        reverse("admin:compliance_vendorgpsrdefaults_add"), {"vendor": vendor.pk, **PARTY}
    )
    assert response.status_code == 302
    assert (
        VendorGpsrDefaults.objects.get(vendor=vendor).manufacturer_name
        == PARTY["manufacturer_name"]
    )
    entry = AuditLog.objects.get(action="compliance.gpsr_defaults.created")
    assert entry.actor_label == "staff:staff@example.com"


def test_an_invalid_contact_is_shown_as_a_form_error(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    response = staff_client.post(
        reverse("admin:compliance_vendorgpsrdefaults_add"),
        {"vendor": vendor.pk, **PARTY, "manufacturer_contact": "call us"},
    )
    assert response.status_code == 200
    assert not VendorGpsrDefaults.objects.exists()


def test_product_gpsr_is_edited_through_the_service(staff_client: Client) -> None:
    product = ProductFactory.create()
    staff_client.post(
        reverse("admin:compliance_gpsrinfo_add"),
        {"product": product.pk, **PARTY, "model_number": "M-1", "batch": ""},
    )
    info = GpsrInfo.objects.get(product=product)
    url = reverse("admin:compliance_gpsrinfo_change", args=[info.pk])
    staff_client.post(url, {"product": product.pk, **PARTY, "model_number": "M-2", "batch": ""})
    info.refresh_from_db()
    assert info.model_number == "M-2"
    assert AuditLog.objects.filter(action="compliance.gpsr.updated").count() == 1


def test_gpsr_data_cannot_be_deleted_from_the_admin(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    staff_client.post(
        reverse("admin:compliance_vendorgpsrdefaults_add"), {"vendor": vendor.pk, **PARTY}
    )
    defaults = VendorGpsrDefaults.objects.get()
    url = reverse("admin:compliance_vendorgpsrdefaults_delete", args=[defaults.pk])
    assert staff_client.get(url).status_code == 403


def test_a_safety_case_is_opened_and_shows_its_deadline(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    received = timezone.localtime().replace(microsecond=0)
    response = staff_client.post(
        reverse("admin:compliance_safetycase_add"),
        {
            "vendor": vendor.pk,
            "kind": SafetyCase.Kind.AUTHORITY_ORDER,
            "received_at_0": received.date().isoformat(),
            "received_at_1": received.time().isoformat(),
            "reference": "ORDER-7",
            "authority": "Example authority",
            "summary": "Withdraw the product.",
        },
    )
    assert response.status_code == 302, response.content.decode()[:2000]
    case = SafetyCase.objects.get()
    assert AuditLog.objects.filter(action="compliance.safety_case.opened").count() == 1
    body = staff_client.get(reverse("admin:compliance_safetycase_changelist")).content.decode()
    assert case.due_on.isoformat() in body or str(case.due_on.day) in body


def test_an_overdue_case_is_marked_in_the_list(staff_client: Client) -> None:
    SafetyCaseFactory.create(due_on=timezone.localdate() - dt.timedelta(days=5))
    body = staff_client.get(reverse("admin:compliance_safetycase_changelist")).content.decode()
    assert "Overdue" in body


def test_a_case_is_closed_through_the_service(staff_client: Client) -> None:
    case = SafetyCaseFactory.create()
    url = reverse("admin:compliance_safetycase_change", args=[case.pk])
    response = staff_client.post(url, {"resolution": "Product withdrawn.", "close": "on"})
    assert response.status_code == 302, response.content.decode()[:2000]
    case.refresh_from_db()
    assert case.status == SafetyCase.Status.CLOSED
    assert AuditLog.objects.filter(action="compliance.safety_case.closed").count() == 1
