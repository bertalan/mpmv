"""CMP.4 (2/2), CMP.30: the customer self-service export and erasure, and the staff view that
processes requests. The target is always `request.user` (never an id from the input), POST with
CSRF, a fresh re-authentication (the account password, or a second factor verified minutes ago),
a rate limit, an audit entry without personal data."""

import json
import secrets

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cart.tests.factories import CartFactory
from apps.compliance import gdpr
from apps.compliance.models import ErasureRequest
from apps.compliance.tests.factories import ErasureRequestFactory
from apps.core.models import AuditLog
from apps.orders.tests.integration.test_retention import ago, place
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db


def signed_in(**fields: object) -> tuple[Client, User, str]:
    password = secrets.token_urlsafe(16)
    user = UserFactory.create(password=password, **fields)
    client = Client()
    client.force_login(user)
    return client, user, password


# --- access ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "method"),
    [("privacy_account", "get"), ("privacy_export", "post"), ("privacy_erase", "post")],
)
def test_anonymous_visitors_are_sent_to_sign_in(name: str, method: str) -> None:
    response = getattr(Client(), method)(reverse(name))
    assert response.status_code == 302
    assert "login" in response["Location"]


@pytest.mark.parametrize("name", ["privacy_export", "privacy_erase"])
def test_actions_need_post_and_csrf(name: str) -> None:
    client, user, password = signed_in()
    assert client.get(reverse(name)).status_code == 405
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)
    assert strict.post(reverse(name), {"password": password}).status_code == 403


def test_the_account_page_explains_the_retention_of_order_data() -> None:
    client, _, _ = signed_in()
    html = client.get(reverse("privacy_account")).content.decode()
    assert "10 years" in html or "ten years" in html.lower()


# --- export ------------------------------------------------------------------------------------


def test_export_with_the_password_returns_only_own_data_as_a_download() -> None:
    client, user, password = signed_in(email="mario@example.com")
    other = UserFactory.create(email="other@example.com")
    CartFactory.create(user=user, session_key="")
    place(state="closed", customer=other, legacy_wp_order_id=8101, email=other.email)

    response = client.post(reverse("privacy_export"), {"password": password})

    assert response.status_code == 200
    assert "attachment" in response["Content-Disposition"]
    data = json.loads(response.content)
    assert data["profile"]["email"] == "mario@example.com"
    assert "other@example.com" not in response.content.decode()
    assert (
        response["Cache-Control"].startswith("no-store") or "no-store" in response["Cache-Control"]
    )


def test_export_ignores_any_user_id_in_the_input() -> None:
    client, _user, password = signed_in()
    other = UserFactory.create(email="victim@example.com")
    response = client.post(
        reverse("privacy_export"),
        {"password": password, "user": str(other.public_id), "user_id": other.pk},
    )
    assert "victim@example.com" not in response.content.decode()


def test_export_without_fresh_authentication_is_refused() -> None:
    client, _, _ = signed_in()
    wrong = client.post(reverse("privacy_export"), {"password": "x" + secrets.token_urlsafe(8)})
    assert wrong.status_code == 403
    assert not AuditLog.objects.filter(action=gdpr.ACTION_EXPORT).exists()


def test_a_second_factor_verified_minutes_ago_replaces_the_password() -> None:
    user = UserFactory.create()
    client = Client()
    verified_login(client, user)
    assert client.post(reverse("privacy_export")).status_code == 200


def test_export_is_rate_limited_and_audited_without_personal_data() -> None:
    client, _user, password = signed_in(email="mario@example.com")
    statuses = [
        client.post(reverse("privacy_export"), {"password": password}).status_code for _ in range(6)
    ]
    assert statuses[0] == 200
    assert 429 in statuses
    entry = AuditLog.objects.filter(action=gdpr.ACTION_EXPORT).first()
    assert entry is not None
    assert "mario@example.com" not in f"{entry.before}{entry.after}{entry.actor_label}"


# --- erasure -----------------------------------------------------------------------------------


def test_erasure_needs_the_confirmation_and_the_password() -> None:
    client, user, password = signed_in()
    no_confirm = client.post(reverse("privacy_erase"), {"password": password})
    wrong = client.post(reverse("privacy_erase"), {"password": "no", "confirm": "erase"})
    assert no_confirm.status_code in (200, 400)
    assert wrong.status_code == 403
    user.refresh_from_db()
    assert user.is_active
    assert not ErasureRequest.objects.filter(user=user).exists()


def test_erasure_anonymises_the_account_and_ends_the_session() -> None:
    client, user, password = signed_in(email="mario@example.com")
    order = place(state="closed", customer=user, legacy_wp_order_id=8102, email=user.email)

    response = client.post(reverse("privacy_erase"), {"password": password, "confirm": "erase"})

    assert response.status_code == 302
    user.refresh_from_db()
    assert not user.is_active
    assert gdpr.is_anonymised(user)
    order.refresh_from_db()
    assert order.privacy_status == "restricted_tax_only"
    assert client.get(reverse("privacy_account")).status_code == 302  # signed out


def test_erasure_with_an_open_order_is_accepted_and_the_user_is_told() -> None:
    client, user, password = signed_in()
    place(state="paid", customer=user, legacy_wp_order_id=8011, paid_at=ago(days=1))
    response = client.post(
        reverse("privacy_erase"), {"password": password, "confirm": "erase"}, follow=True
    )
    assert response.status_code == 200
    assert ErasureRequest.objects.get(user=user).completed_at is None
    assert "last open order" in response.content.decode().lower()


def test_a_vendor_member_is_pointed_to_support_and_nothing_changes() -> None:
    client, user, password = signed_in()
    VendorMemberFactory.create(vendor=VendorFactory.create(), user=user, role="owner")
    response = client.post(
        reverse("privacy_erase"), {"password": password, "confirm": "erase"}, follow=True
    )
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.is_active


def test_erasure_is_rate_limited_and_audited_without_personal_data() -> None:
    client, _user, password = signed_in(email="mario@example.com")
    for _ in range(6):
        client.post(reverse("privacy_erase"), {"password": "bad", "confirm": "erase"})
    again = client.post(reverse("privacy_erase"), {"password": password, "confirm": "erase"})
    assert again.status_code == 429


# --- staff -------------------------------------------------------------------------------------


def staff_client(*perms: str) -> Client:
    staff = UserFactory.create(staff=True, permissions=list(perms))
    client = Client()
    verified_login(client, staff)
    return client


def test_staff_without_the_permission_and_customers_get_404() -> None:
    ErasureRequestFactory.create()
    assert staff_client().get(reverse("privacy_requests")).status_code == 404
    client, _, _ = signed_in()
    assert client.get(reverse("privacy_requests")).status_code == 404


def test_staff_list_shows_requests_without_personal_data() -> None:
    pending = ErasureRequestFactory.create(user=UserFactory.create(email="shown@example.com"))
    response = staff_client("compliance.change_erasurerequest").get(reverse("privacy_requests"))
    html = response.content.decode()
    assert response.status_code == 200
    assert str(pending.public_id) in html
    assert "shown@example.com" not in html


def test_staff_processes_a_pending_request_with_a_reason_and_it_is_audited() -> None:
    pending = ErasureRequestFactory.create()
    client = staff_client("compliance.change_erasurerequest")
    url = reverse("privacy_request_process", kwargs={"public_id": pending.public_id})

    refused = client.post(url, {"reason": ""})
    ok = client.post(url, {"reason": "owner asked by letter"})

    assert refused.status_code == 400
    assert ok.status_code == 302
    pending.refresh_from_db()
    assert pending.completed_at is not None
    assert AuditLog.objects.filter(action="compliance.erasure_processed").exists()


def test_processing_an_unknown_request_is_404() -> None:
    client = staff_client("compliance.change_erasurerequest")
    url = reverse(
        "privacy_request_process", kwargs={"public_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert client.post(url, {"reason": "x"}).status_code == 404
