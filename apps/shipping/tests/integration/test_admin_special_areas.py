"""SHP.14: staff edit special areas and the treatment each method gives them in the admin;
every change goes through the audited services."""

from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.shipping import engine
from apps.shipping.models import SpecialArea, SpecialAreaTreatment
from apps.shipping.tests.factories import ShippingMethodFactory, SpecialAreaFactory
from apps.shipping.tests.integration.test_admin import MfaLogin, inline_management, method_form

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def area_form(**fields: str) -> dict[str, str]:
    return {
        "code": "carrier-remote-gr",
        "name": "Carrier remote areas (GR)",
        "kind": engine.REMOTE,
        "countries": "el",
        "postcode_patterns": "190 10,18010..18020",
        "states": "",
        "territory_code": "",
        **fields,
    }


def test_the_special_area_changelist_loads(staff_client: Client) -> None:
    response = staff_client.get(reverse("admin:shipping_specialarea_changelist"))
    assert response.status_code == 200
    assert "it-sardinia" in response.content.decode()


def test_add_special_area_is_normalized_and_audited(staff_client: Client) -> None:
    response = staff_client.post(reverse("admin:shipping_specialarea_add"), area_form())
    assert response.status_code == 302, response.content.decode()
    area = SpecialArea.objects.get(code="carrier-remote-gr")
    assert (area.countries, area.postcode_patterns) == (["GR"], ["190 10", "18010..18020"])
    entry = AuditLog.objects.get(action="shipping.specialarea.created")
    assert entry.source == "admin"


def test_an_extra_vat_area_without_a_territory_is_a_form_error(staff_client: Client) -> None:
    data = area_form(kind=engine.EXTRA_VAT)
    response = staff_client.post(reverse("admin:shipping_specialarea_add"), data)
    assert response.status_code == 200
    assert not SpecialArea.objects.filter(code="carrier-remote-gr").exists()


def test_a_method_sets_a_fixed_surcharge_for_an_area(staff_client: Client) -> None:
    method = ShippingMethodFactory.create(default=True, key="standard")
    area = SpecialAreaFactory.create()
    data = method_form(method.vendor, method.zone, method.carrier)
    data.update(
        {
            **inline_management("area_treatments", 1),
            "area_treatments-0-area": str(area.pk),
            "area_treatments-0-mode": engine.SURCHARGE,
            "area_treatments-0-surcharge_type": engine.FIXED,
            "area_treatments-0-surcharge": "15.00",
            "area_treatments-0-extra_days": "2",
        }
    )

    url = reverse("admin:shipping_shippingmethod_change", args=[method.pk])
    response = staff_client.post(url, data)

    assert response.status_code == 302, response.content.decode()
    treatment = SpecialAreaTreatment.objects.get(method=method)
    assert (treatment.mode, treatment.surcharge) == (engine.SURCHARGE, Decimal("15.00"))
    entry = AuditLog.objects.get(action="shipping.specialareatreatment.created")
    assert entry.vendor_key == str(method.vendor.public_id)
