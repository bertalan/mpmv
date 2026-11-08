"""VEN.25 (owner decision MQ-5): RF20 only for vendors established in an EU member state, with
the `EX` number issued by that member state, at every vendor entry point: `save_vendor` (API,
MCP, shell, the TAX.2 approval) and the vendor admin.

Done when: RF20 is refused for a vendor established outside the EU and without an `EX` number; a
malformed number or one issued by another member state is refused; a valid EU vendor saves RF20.
"""

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors import engine
from apps.vendors.models import Vendor
from apps.vendors.services import save_vendor
from apps.vendors.tests.factories import VendorFactory
from apps.vendors.tests.fakes import seed_legal_requirements

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="127.0.0.1")
RF20 = engine.CROSS_BORDER_FRANCHISE_REGIME
NUMBER = "cross_border_franchise_number"
EMPTY_INLINE = {
    "translations-TOTAL_FORMS": "0",
    "translations-INITIAL_FORMS": "0",
    "translations-MIN_NUM_FORMS": "0",
    "translations-MAX_NUM_FORMS": "1000",
}


def german_number(vendor: Vendor) -> str:
    """A synthetic EX number issued by Germany (the `eu_company` VAT number + EX)."""
    return f"{vendor.vat_number}EX"


def refused(vendor: Vendor) -> dict[str, Any]:
    with pytest.raises(ValidationError) as exc:
        save_vendor(vendor, ctx=CTX)
    assert Vendor.objects.get(pk=vendor.pk).fiscal_regime != RF20
    assert not AuditLog.objects.filter(action="vendor.updated").exists()
    return exc.value.message_dict


# --- save_vendor


def test_rf20_is_refused_for_a_vendor_outside_the_eu_without_an_ex_number() -> None:
    vendor = VendorFactory.create(non_eu_without_vat=True)
    vendor.fiscal_regime = RF20
    errors = refused(vendor)
    assert "fiscal_regime" in errors
    assert NUMBER in errors


def test_rf20_is_refused_for_a_non_eu_vendor_with_an_ex_number() -> None:
    vendor = VendorFactory.create(non_eu_company=True)
    vendor.fiscal_regime = RF20
    vendor.cross_border_franchise_number = "DE123456789EX"
    errors = refused(vendor)
    assert "fiscal_regime" in errors


def test_rf20_without_the_number_is_refused_for_an_eu_vendor() -> None:
    vendor = VendorFactory.create(eu_company=True)
    vendor.fiscal_regime = RF20
    assert set(refused(vendor)) == {NUMBER}


def test_a_malformed_number_is_refused() -> None:
    vendor = VendorFactory.create(eu_company=True)
    vendor.fiscal_regime = RF20
    vendor.cross_border_franchise_number = "DE12EX"
    assert set(refused(vendor)) == {NUMBER}


def test_a_number_issued_by_another_member_state_is_refused() -> None:
    vendor = VendorFactory.create(eu_company=True)
    vendor.fiscal_regime = RF20
    vendor.cross_border_franchise_number = "IT01234567897EX"
    errors = refused(vendor)
    assert set(errors) == {NUMBER}
    assert "IT" in errors[NUMBER][0]


def test_a_valid_eu_vendor_saves_rf20_and_the_number_is_audited() -> None:
    vendor = VendorFactory.create(eu_company=True)
    vendor.fiscal_regime = RF20
    vendor.cross_border_franchise_number = german_number(vendor).lower()
    save_vendor(vendor, ctx=CTX)

    stored = Vendor.objects.get(pk=vendor.pk)
    assert stored.fiscal_regime == RF20
    assert stored.cross_border_franchise_number == german_number(vendor).upper()
    entry = AuditLog.objects.get(action="vendor.updated")
    assert entry.after is not None
    assert entry.after[NUMBER] == stored.cross_border_franchise_number


def test_an_rf20_vendor_cannot_drop_its_number() -> None:
    vendor = VendorFactory.create(eu_company=True)
    vendor.fiscal_regime = RF20
    vendor.cross_border_franchise_number = german_number(vendor)
    save_vendor(vendor, ctx=CTX)

    vendor.cross_border_franchise_number = ""
    with pytest.raises(ValidationError) as exc:
        save_vendor(vendor, ctx=CTX)
    assert NUMBER in exc.value.message_dict


def test_an_rf20_vendor_cannot_move_its_seat_outside_the_eu() -> None:
    vendor = VendorFactory.create(eu_company=True)
    vendor.fiscal_regime = RF20
    vendor.cross_border_franchise_number = german_number(vendor)
    save_vendor(vendor, ctx=CTX)

    vendor.registered_country = "CH"
    with pytest.raises(ValidationError):
        save_vendor(vendor, ctx=CTX, dry_run=True)


# --- Vendor admin


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: Any) -> Client:
    seed_legal_requirements()
    logged_in: Client = mfa_login(client, superuser)
    return logged_in


def change_form(vendor: Vendor, **overrides: Any) -> dict[str, Any]:
    """The POST data of the vendor change form, as the browser would send it unchanged."""
    names = ("display_name", "slug", "legal_name", "country", "contact_email", "phone")
    data: dict[str, Any] = {
        name: "" if getattr(vendor, name) is None else getattr(vendor, name)
        for name in (*names, "sales_mode_default", *engine.LEGAL_FIELDS)
    }
    data["share_capital"] = "" if vendor.share_capital is None else str(vendor.share_capital)
    data["legacy_wp_user_id"] = ""
    data.update(EMPTY_INLINE)
    data.update(overrides)
    return data


def change_url(vendor: Vendor) -> str:
    return reverse("admin:vendors_vendor_change", args=[vendor.pk])


def test_the_admin_shows_the_number_field(staff_client: Client) -> None:
    vendor = VendorFactory.create(eu_company=True)
    content = staff_client.get(change_url(vendor)).content.decode()
    assert f'name="{NUMBER}"' in content


def test_the_admin_refuses_rf20_without_the_number(staff_client: Client) -> None:
    vendor = VendorFactory.create(eu_company=True)
    response = staff_client.post(change_url(vendor), change_form(vendor, fiscal_regime=RF20))
    assert response.status_code == 200
    assert NUMBER in response.context["adminform"].form.errors
    vendor.refresh_from_db()
    assert vendor.fiscal_regime != RF20


def test_the_admin_refuses_rf20_for_a_non_eu_vendor(staff_client: Client) -> None:
    vendor = VendorFactory.create(non_eu_without_vat=True)
    form = change_form(vendor, fiscal_regime=RF20, **{NUMBER: "DE123456789EX"})
    response = staff_client.post(change_url(vendor), form)
    assert response.status_code == 200
    assert "fiscal_regime" in response.context["adminform"].form.errors
    vendor.refresh_from_db()
    assert vendor.fiscal_regime != RF20


def test_the_admin_saves_rf20_with_a_valid_number(staff_client: Client) -> None:
    vendor = VendorFactory.create(eu_company=True)
    form = change_form(vendor, fiscal_regime=RF20, **{NUMBER: german_number(vendor)})
    response = staff_client.post(change_url(vendor), form)
    assert response.status_code == 302, response.content.decode()
    vendor.refresh_from_db()
    assert vendor.fiscal_regime == RF20
    assert vendor.cross_border_franchise_number == german_number(vendor)
