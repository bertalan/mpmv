"""VEN.18 (review M2): the vendor admin cannot change a fiscal regime the tax profile relies on.

Once a vendor has a tax profile (current or scheduled), its declared fiscal regime changes only
through a TAX.2 regime-change request: vendor request, staff approval with the review
permission and a fresh second factor, requester other than approver. In the vendor admin the
field is read-only, and a forged POST that sends it anyway is ignored, for superusers too.
"""

from typing import Any

import pytest
from django.contrib import admin
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.tests.factories import utc
from apps.tax.tests.factories import VendorTaxProfileFactory
from apps.vendors import engine as vendors_engine
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from apps.vendors.tests.fakes import seed_legal_requirements

pytestmark = pytest.mark.django_db

RF20 = Vendor.FiscalRegime.RF20
EMPTY_INLINE = {
    "translations-TOTAL_FORMS": "0",
    "translations-INITIAL_FORMS": "0",
    "translations-MIN_NUM_FORMS": "0",
    "translations-MAX_NUM_FORMS": "1000",
}


@pytest.fixture
def superuser_client(client: Client, superuser: User, mfa_login: Any) -> Client:
    seed_legal_requirements()
    logged_in: Client = mfa_login(client, superuser)
    return logged_in


@pytest.fixture
def vendor_editor_client(client: Client, mfa_login: Any) -> Client:
    """Staff who may edit vendors but not review regime changes."""
    seed_legal_requirements()
    editor = UserFactory.create(
        staff=True, permissions=["vendors.view_vendor", "vendors.change_vendor"]
    )
    logged_in: Client = mfa_login(client, editor)
    return logged_in


def change_form(vendor: Vendor, **overrides: Any) -> dict[str, Any]:
    """The POST data of the vendor change form, as the browser would send it unchanged."""
    data: dict[str, Any] = {
        name: "" if getattr(vendor, name) is None else getattr(vendor, name)
        for name in (
            "display_name",
            "slug",
            "legal_name",
            "country",
            "contact_email",
            "phone",
            "sales_mode_default",
            *vendors_engine.LEGAL_FIELDS,
        )
    }
    data["share_capital"] = "" if vendor.share_capital is None else str(vendor.share_capital)
    data["legacy_wp_user_id"] = ""
    data.update(EMPTY_INLINE)
    data.update(overrides)
    return data


def change_url(vendor: Vendor) -> str:
    return reverse("admin:vendors_vendor_change", args=[vendor.pk])


def vendor_with_profile(*, valid_from: Any = None) -> Vendor:
    vendor = VendorFactory.create(italian_company=True)
    VendorTaxProfileFactory.create(vendor=vendor, valid_from=valid_from or utc(2026, 1, 1))
    return vendor


def test_the_regime_is_read_only_once_the_vendor_has_a_tax_profile(
    superuser_client: Client,
) -> None:
    vendor = vendor_with_profile()
    response = superuser_client.get(change_url(vendor))
    assert response.status_code == 200
    content = response.content.decode()
    assert '<select name="fiscal_regime"' not in content
    assert "RF01 - Ordinario" in content


def test_a_forged_post_cannot_change_the_regime_of_a_vendor_with_a_profile(
    vendor_editor_client: Client,
) -> None:
    vendor = vendor_with_profile()
    response = vendor_editor_client.post(
        change_url(vendor), change_form(vendor, fiscal_regime=RF20, registered_city="Rome")
    )
    assert response.status_code == 302, response.content.decode()
    vendor.refresh_from_db()
    assert vendor.registered_city == "Rome"
    assert vendor.fiscal_regime == Vendor.FiscalRegime.RF01


def test_a_superuser_cannot_change_the_regime_there_either(superuser_client: Client) -> None:
    vendor = vendor_with_profile()
    response = superuser_client.post(change_url(vendor), change_form(vendor, fiscal_regime=RF20))
    assert response.status_code == 302, response.content.decode()
    vendor.refresh_from_db()
    assert vendor.fiscal_regime == Vendor.FiscalRegime.RF01


def test_a_scheduled_profile_locks_the_regime_too(superuser_client: Client) -> None:
    vendor = vendor_with_profile(valid_from=utc(2030, 1, 1))
    superuser_client.post(change_url(vendor), change_form(vendor, fiscal_regime=RF20))
    vendor.refresh_from_db()
    assert vendor.fiscal_regime == Vendor.FiscalRegime.RF01


def test_without_a_tax_profile_staff_still_set_the_regime(superuser_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    assert (
        '<select name="fiscal_regime"' in superuser_client.get(change_url(vendor)).content.decode()
    )
    response = superuser_client.post(change_url(vendor), change_form(vendor, fiscal_regime=RF20))
    assert response.status_code == 302, response.content.decode()
    vendor.refresh_from_db()
    assert vendor.fiscal_regime == RF20


def test_the_add_form_keeps_the_regime_editable() -> None:
    model_admin = admin.site._registry[Vendor]
    assert "fiscal_regime" not in model_admin.get_readonly_fields(request=None, obj=None)  # type: ignore[arg-type]
