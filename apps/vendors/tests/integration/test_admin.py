"""VEN.1: staff CRUD for vendors and their translations in the Django admin."""

from collections.abc import Callable
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.vendors.models import Vendor, VendorTranslation
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

MfaLogin = Callable[[Client, User], Client]


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def staff_with(client: Client, *codenames: str) -> Client:
    """A logged-in staff user (not superuser) holding only the given vendors permissions."""
    user = UserFactory.create(
        email="limited@example.com",
        staff=True,
        permissions=[f"vendors.{codename}" for codename in codenames],
    )
    verified_login(client, user)  # staff need a verified second factor (CORE.4)
    return client


def management_form(total: int, initial: int = 0) -> dict[str, Any]:
    return {
        "translations-TOTAL_FORMS": str(total),
        "translations-INITIAL_FORMS": str(initial),
        "translations-MIN_NUM_FORMS": "0",
        "translations-MAX_NUM_FORMS": "1000",
    }


def vendor_form(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "legal_name": "Vendor B S.p.A.",
        "display_name": "Vendor B Mountain Shop",
        "slug": "",
        "country": "it",
        "vat_number": "IT01234567897",
        "tax_code": "",
        "registered_street": "Via Example",
        "registered_street_number": "1",
        "registered_city": "Rome",
        "registered_country": "IT",
        "contact_email": "vendor-b@example.com",
        "phone": "",
        "sales_mode_default": "buy",
        "legacy_wp_user_id": "",
    }
    data.update(overrides)
    return data


def test_changelist_lists_vendors_with_their_status(staff_client: Client) -> None:
    VendorFactory.create(display_name="Vendor A")
    response = staff_client.get(reverse("admin:vendors_vendor_changelist"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Vendor A" in content
    assert "Applicant" in content


def test_changelist_filters_by_status(staff_client: Client) -> None:
    VendorFactory.create()
    active = VendorFactory.create(status="active")
    response = staff_client.get(reverse("admin:vendors_vendor_changelist"), {"status": "active"})
    assert response.status_code == 200
    assert list(response.context["cl"].result_list) == [active]


def test_add_vendor_with_translations(staff_client: Client) -> None:
    data = vendor_form()
    data.update(management_form(2))
    data.update(
        {
            "translations-0-language": "en",
            "translations-0-status": "published",
            "translations-0-tagline": "Outdoor gear",
            "translations-0-description": "Family business.",
            "translations-0-shipping_policy": "Ships in 2 days.",
            "translations-0-refund_policy": "Refunds within 14 days.",
            "translations-0-cancellation_policy": "Cancel before shipping.",
            "translations-1-language": "it",
            "translations-1-status": "machine",
            "translations-1-tagline": "Attrezzatura outdoor",
        }
    )
    response = staff_client.post(reverse("admin:vendors_vendor_add"), data)
    assert response.status_code == 302, response.content.decode()
    # I18N.2: staff set the workflow status; the IT draft falls back to the published EN texts.
    resolved = Vendor.objects.get().translation("it")
    assert resolved.language == "en"
    assert resolved.is_fallback is True

    vendor = Vendor.objects.get()
    assert vendor.slug == "vendor-b-mountain-shop"
    assert vendor.status == Vendor.Status.APPLICANT
    assert vendor.country == "IT"
    assert vendor.registered_city == "Rome"
    assert vendor.translations.get(language="en").refund_policy == "Refunds within 14 days."
    assert vendor.translations.get(language="it").tagline == "Attrezzatura outdoor"


def test_add_vendor_rejects_duplicate_translation_languages(staff_client: Client) -> None:
    data = vendor_form()
    data.update(management_form(2))
    data.update({"translations-0-language": "en", "translations-1-language": "en"})
    response = staff_client.post(reverse("admin:vendors_vendor_add"), data)
    assert response.status_code == 200  # form redisplayed with errors
    assert Vendor.objects.count() == 0
    assert VendorTranslation.objects.count() == 0


def test_add_vendor_rejects_an_invalid_italian_address(staff_client: Client) -> None:
    data = vendor_form(registered_postal_code="0010", registered_province="Lazio")
    data.update(management_form(0))
    response = staff_client.post(reverse("admin:vendors_vendor_add"), data)
    assert response.status_code == 200
    content = response.content.decode()
    assert "An Italian postal code (CAP) has 5 digits." in content
    assert "is not an Italian province code" in content
    assert Vendor.objects.count() == 0


def test_add_vendor_with_no_address_saves_empty_fields(staff_client: Client) -> None:
    data = vendor_form(
        registered_street="", registered_street_number="", registered_city="", registered_country=""
    )
    data.update(management_form(0))
    response = staff_client.post(reverse("admin:vendors_vendor_add"), data)
    assert response.status_code == 302, response.content.decode()
    vendor = Vendor.objects.get()
    assert (vendor.registered_street, vendor.registered_city) == ("", "")


def test_change_vendor_does_not_let_staff_edit_the_status_directly(staff_client: Client) -> None:
    vendor = VendorFactory.create(display_name="Vendor A", contact_email="vendor-a@example.com")
    data = vendor_form(
        legal_name="Vendor A Holding S.r.l.",
        display_name="Vendor A",
        slug="vendor-a",
        contact_email="vendor-a@example.com",
        status="active",  # not an editable field: must be ignored
    )
    data.update(management_form(0))
    response = staff_client.post(reverse("admin:vendors_vendor_change", args=[vendor.pk]), data)
    assert response.status_code == 302, response.content.decode()
    vendor.refresh_from_db()
    assert vendor.legal_name == "Vendor A Holding S.r.l."
    assert vendor.status == Vendor.Status.APPLICANT


def test_delete_vendor(staff_client: Client) -> None:
    vendor = VendorFactory.create(languages=["en"])
    response = staff_client.post(
        reverse("admin:vendors_vendor_delete", args=[vendor.pk]), {"post": "yes"}
    )
    assert response.status_code == 302
    assert Vendor.objects.count() == 0
    assert VendorTranslation.objects.count() == 0


def test_status_action_moves_vendors_through_the_transition_service(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    response = staff_client.post(
        reverse("admin:vendors_vendor_changelist"),
        {"action": "move_to_onboarding", "_selected_action": [vendor.pk]},
        follow=True,
    )
    assert response.status_code == 200
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ONBOARDING
    entry = AuditLog.objects.get(target_model="vendors.vendor")  # the login is audited too
    assert entry.action == "vendor.status_changed"
    assert entry.source == "admin"
    assert entry.actor_label == "staff:staff@example.com"


def test_status_action_reports_invalid_transitions_and_changes_nothing(
    staff_client: Client,
) -> None:
    vendor = VendorFactory.create()
    response = staff_client.post(
        reverse("admin:vendors_vendor_changelist"),
        {"action": "move_to_active", "_selected_action": [vendor.pk]},
        follow=True,
    )
    assert response.status_code == 200
    messages = [str(m) for m in response.context["messages"]]
    assert any("applicant → active" in m for m in messages)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.APPLICANT
    assert not AuditLog.objects.filter(target_model="vendors.vendor").exists()


def test_view_only_staff_get_no_status_actions(client: Client) -> None:
    VendorFactory.create()
    viewer = staff_with(client, "view_vendor")
    response = viewer.get(reverse("admin:vendors_vendor_changelist"))
    assert response.status_code == 200
    assert "move_to_" not in response.content.decode()


@pytest.mark.parametrize("action", ["move_to_onboarding", "move_to_closed"])
def test_view_only_staff_cannot_run_status_actions(client: Client, action: str) -> None:
    vendor = VendorFactory.create()
    viewer = staff_with(client, "view_vendor")
    viewer.post(
        reverse("admin:vendors_vendor_changelist"),
        {"action": action, "_selected_action": [vendor.pk]},
    )
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.APPLICANT
    assert not AuditLog.objects.filter(target_model="vendors.vendor").exists()


def test_staff_with_change_permission_can_run_status_actions(client: Client) -> None:
    vendor = VendorFactory.create()
    editor = staff_with(client, "view_vendor", "change_vendor")
    response = editor.post(
        reverse("admin:vendors_vendor_changelist"),
        {"action": "move_to_onboarding", "_selected_action": [vendor.pk]},
        follow=True,
    )
    assert response.status_code == 200
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ONBOARDING
    entry = AuditLog.objects.get(target_model="vendors.vendor")
    assert entry.actor_label == "staff:limited@example.com"


# --- SEC.4: staff edits are audited, and visible with before/after in the audit view


def test_adding_a_vendor_in_the_admin_is_audited(staff_client: Client) -> None:
    data = vendor_form()
    data.update(management_form(0))
    response = staff_client.post(reverse("admin:vendors_vendor_add"), data)
    assert response.status_code == 302, response.content.decode()
    vendor = Vendor.objects.get()
    entry = AuditLog.objects.get(action="vendor.created")
    assert entry.target_key == str(vendor.public_id)
    assert entry.source == "admin"
    assert entry.actor_label == "staff:staff@example.com"


def test_a_vendor_field_change_is_visible_in_the_audit_view_with_before_and_after(
    staff_client: Client,
) -> None:
    vendor = VendorFactory.create(
        display_name="Vendor A", contact_email="vendor-a@example.com", vat_number="IT00000010025"
    )
    data = vendor_form(
        display_name="Vendor A",
        slug=vendor.slug,
        legal_name=vendor.legal_name,
        contact_email="vendor-a@example.com",
        vat_number="IT00000020032",
    )
    data.update(management_form(0))
    response = staff_client.post(reverse("admin:vendors_vendor_change", args=[vendor.pk]), data)
    assert response.status_code == 302, response.content.decode()

    listing = staff_client.get(
        reverse("admin:core_auditlog_changelist"),
        # The VAT number is part of the DAC7 dataset: a staff change is a DAC7 correction.
        {"vendor": str(vendor.public_id), "action": "vendor.dac7_corrected"},
    )
    (entry,) = listing.context["cl"].result_list
    detail = staff_client.get(reverse("admin:core_auditlog_change", args=[entry.pk]))
    assert ("vat_number", "IT00000010025", "IT00000020032") in detail.context["audit_changes"]
    assert entry.actor_label == "staff:staff@example.com"


def test_deleting_a_vendor_in_the_admin_is_audited(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    staff_client.post(reverse("admin:vendors_vendor_delete", args=[vendor.pk]), {"post": "yes"})
    entry = AuditLog.objects.get(action="vendor.deleted")
    assert entry.target_key == str(vendor.public_id)
    assert entry.after is None


def test_bulk_deleting_vendors_in_the_admin_is_audited(staff_client: Client) -> None:
    vendors = VendorFactory.create_batch(2)
    staff_client.post(
        reverse("admin:vendors_vendor_changelist"),
        {"action": "delete_selected", "_selected_action": [v.pk for v in vendors], "post": "yes"},
    )
    assert not Vendor.objects.exists()
    assert AuditLog.objects.filter(action="vendor.deleted").count() == 2


def test_the_vendor_page_links_to_its_audit_trail(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    response = staff_client.get(reverse("admin:vendors_vendor_change", args=[vendor.pk]))
    trail = f"{reverse('admin:core_auditlog_changelist')}?vendor={vendor.public_id}"
    assert trail in response.content.decode()


def test_anonymous_users_cannot_reach_the_vendor_admin(client: Client) -> None:
    response = client.get(reverse("admin:vendors_vendor_changelist"))
    assert response.status_code == 302
    assert "/login/" in response["Location"]
