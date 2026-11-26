"""CORE.28 slice 2: the breached-password check at each password sign-in.

A sign-in whose password is in a known breach still succeeds (no automatic block), but the account
is flagged: every page then sends the user to the change-password page until a new password,
which passes the validators (the breach one included), is set. A vendor member must also verify
the second factor again after the change; the event is audited and the platform staff get a
notice through the outbox. The service down lets the sign-in through. Passwords are generated
and the breach status comes from the fake checker (no network, no credential literals).
"""

import datetime as dt
import json
import secrets
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import User
from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.fakes import FakeBreachChecker
from apps.accounts.tests.mfa import totp_token
from apps.core.models import AuditLog, OutboxEmail
from apps.core.tests.factories import PlatformOperatorFactory
from apps.vendors.models import VendorMember
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse

pytestmark = pytest.mark.django_db

LOGIN = "/account/login/"
SHOP = "/manager/shop/"
SESSIONS = "/account/sessions/"
BREACHED = "accounts.user.password_breached"
SUPPORT = "support@platform.example"


def change_url(next_url: str) -> str:
    return f"{reverse('password:change')}?{urlencode({'next': next_url})}"


@pytest.fixture
def password() -> str:
    return secrets.token_urlsafe(16)


@pytest.fixture
def operator(db: None) -> None:
    PlatformOperatorFactory.create(
        open_ended=True, valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC), support_email=SUPPORT
    )


@pytest.fixture
def customer(password: str) -> User:
    return UserFactory.create(password=password)


@pytest.fixture
def member(password: str) -> User:
    user = UserFactory.create(password=password)
    VendorMemberFactory.create(vendor=VendorFactory.create(), user=user, role="owner")
    return user


@pytest.fixture
def device(member: User) -> TOTPDevice:
    return TOTPDeviceFactory.create(user=member)


def sign_in(client: Client, user: User, password: str, next_url: str = "") -> None:
    url = f"{LOGIN}?{urlencode({'next': next_url})}" if next_url else LOGIN
    response = client.post(url, {"username": user.email, "password": password})
    assert response.status_code == 302, response.content
    assert "_auth_user_id" in client.session


def verify(client: Client, device: TOTPDevice, *, offset_steps: int = 0) -> None:
    response = client.post(
        reverse("mfa:verify"),
        {"method": "totp", "token": totp_token(device, offset_steps=offset_steps), "next": "/"},
    )
    assert response.status_code == 302, response.content


def change(
    client: Client, old: str, new: str, next_url: str = SESSIONS
) -> "_MonkeyPatchedWSGIResponse":
    return client.post(
        change_url(next_url),
        {"old_password": old, "new_password1": new, "new_password2": new},
    )


# --- Customers


def test_a_customer_with_a_breached_password_signs_in_and_is_flagged(
    client: Client, customer: User, password: str
) -> None:
    FakeBreachChecker.breached.add(password)
    sign_in(client, customer, password)
    customer.refresh_from_db()
    assert customer.password_breached_at is not None
    assert password in FakeBreachChecker.calls
    entry = AuditLog.objects.get(action=BREACHED)
    assert entry.target_key == str(customer.public_id)
    assert password not in json.dumps([entry.before, entry.after])
    assert not OutboxEmail.objects.exists()  # customers: no staff notice


def test_every_other_page_sends_a_flagged_customer_to_the_change_page(
    client: Client, customer: User, password: str
) -> None:
    FakeBreachChecker.breached.add(password)
    sign_in(client, customer, password)
    response = client.get(SESSIONS)
    assert response.status_code == 302
    assert response["Location"] == change_url(SESSIONS)
    page = client.get(change_url(SESSIONS))
    assert page.status_code == 200
    assert 'name="new_password1"' in page.text


def test_a_json_client_gets_a_403_while_flagged(
    client: Client, customer: User, password: str
) -> None:
    FakeBreachChecker.breached.add(password)
    sign_in(client, customer, password)
    response = client.get(SESSIONS, headers={"accept": "application/json"})
    assert response.status_code == 403
    assert response.json()["error"] == "password_change_required"


def test_the_new_password_goes_through_the_breach_validator(
    client: Client, customer: User, password: str
) -> None:
    other_breached = secrets.token_urlsafe(16)
    FakeBreachChecker.breached.update({password, other_breached})
    sign_in(client, customer, password)
    response = change(client, password, other_breached)
    assert response.status_code == 200  # the form again, with the error
    customer.refresh_from_db()
    assert customer.password_breached_at is not None
    assert customer.check_password(password)


def test_the_flag_clears_after_the_change_and_the_customer_goes_on(
    client: Client, customer: User, password: str
) -> None:
    FakeBreachChecker.breached.add(password)
    sign_in(client, customer, password)
    new = secrets.token_urlsafe(16)
    response = change(client, password, new)
    assert response.status_code == 302
    assert response["Location"] == SESSIONS
    customer.refresh_from_db()
    assert customer.password_breached_at is None
    assert customer.check_password(new)
    assert AuditLog.objects.filter(action="accounts.user.password_changed").exists()
    assert client.get(SESSIONS).status_code == 200  # still signed in, nothing redirects


def test_the_service_down_lets_the_sign_in_through(
    client: Client, customer: User, password: str
) -> None:
    FakeBreachChecker.breached.add(password)
    FakeBreachChecker.down = True
    sign_in(client, customer, password)
    customer.refresh_from_db()
    assert customer.password_breached_at is None
    assert not AuditLog.objects.filter(action=BREACHED).exists()
    assert client.get(SESSIONS).status_code == 200


def test_a_clean_password_signs_in_without_a_flag_and_is_checked_each_time(
    client: Client, customer: User, password: str
) -> None:
    sign_in(client, customer, password)
    client.post(reverse("logout"))
    sign_in(client, customer, password)
    assert FakeBreachChecker.calls == [password, password]
    customer.refresh_from_db()
    assert customer.password_breached_at is None
    assert client.get(SESSIONS).status_code == 200


def test_a_wrong_password_is_never_sent_to_the_checker(
    client: Client, customer: User, password: str
) -> None:
    client.post(LOGIN, {"username": customer.email, "password": secrets.token_urlsafe(16)})
    assert "_auth_user_id" not in client.session
    assert FakeBreachChecker.calls == []


def test_an_already_flagged_account_is_not_checked_or_audited_again(
    client: Client, customer: User, password: str
) -> None:
    User.objects.filter(pk=customer.pk).update(password_breached_at=dt.datetime.now(dt.UTC))
    sign_in(client, customer, password)
    assert FakeBreachChecker.calls == []
    assert not AuditLog.objects.filter(action=BREACHED).exists()
    assert client.get(SESSIONS)["Location"] == change_url(SESSIONS)


# --- Vendor members


def test_a_vendor_member_is_flagged_audited_and_staff_are_told(
    client: Client, member: User, device: TOTPDevice, password: str, operator: None
) -> None:
    FakeBreachChecker.breached.add(password)
    sign_in(client, member, password, next_url=SHOP)
    member.refresh_from_db()
    assert member.password_breached_at is not None
    entry = AuditLog.objects.get(action=BREACHED)
    assert entry.after == {"privileged": True}
    notice = OutboxEmail.objects.get()
    assert notice.recipient == SUPPORT
    assert notice.template_key == "accounts.breached_password"
    assert password not in notice.context + notice.text_body + notice.subject
    # No automatic blocking of the account's activity.
    assert member.is_active
    assert VendorMember.objects.filter(user=member).exists()


def test_a_vendor_member_changes_the_password_then_verifies_again(
    client: Client, member: User, device: TOTPDevice, password: str, operator: None
) -> None:
    FakeBreachChecker.breached.add(password)
    sign_in(client, member, password, next_url=SHOP)
    # The second factor first (CORE.4), then the change page before any other page.
    assert client.get(SHOP)["Location"].startswith(reverse("mfa:verify"))
    verify(client, device)
    assert client.get(SHOP)["Location"] == change_url(SHOP)
    assert client.get(change_url(SHOP)).status_code == 200

    new = secrets.token_urlsafe(16)
    response = change(client, password, new, next_url=SHOP)
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("mfa:verify"))
    member.refresh_from_db()
    assert member.password_breached_at is None
    # A fresh authentication before any other action.
    assert client.get(SHOP)["Location"].startswith(reverse("mfa:verify"))
    verify(client, device, offset_steps=1)
    assert client.get(SHOP).status_code in (200, 302)
    assert not client.get(SHOP).get("Location", "").startswith(reverse("password:change"))


def test_the_staff_notice_is_skipped_without_a_support_address(
    client: Client, member: User, device: TOTPDevice, password: str
) -> None:
    FakeBreachChecker.breached.add(password)
    sign_in(client, member, password)
    member.refresh_from_db()
    assert member.password_breached_at is not None
    assert AuditLog.objects.filter(action=BREACHED).exists()
    assert not OutboxEmail.objects.exists()


def test_a_password_change_by_any_audited_path_clears_the_flag(customer: User) -> None:
    from apps.accounts.services import record_password_change
    from apps.core.audit import user_context

    User.objects.filter(pk=customer.pk).update(password_breached_at=dt.datetime.now(dt.UTC))
    customer.refresh_from_db()
    customer.set_password(secrets.token_urlsafe(16))
    customer.save()
    record_password_change(customer, had_usable_password=True, ctx=user_context(customer, None))
    customer.refresh_from_db()
    assert customer.password_breached_at is None
