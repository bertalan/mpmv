"""CORE.25 / VEN.6 (D-32 amendment, owner 2026-11-14): a shop owner confirms its own shop's
sensitive and critical actions with its own passkey.

A vendor owner holds no `core.elevate`. For the actions of `levels.SHOP_OWNER_ACTIONS` on a shop
they own they may "Do it once" and open a passkey window scoped to that shop: never another
shop, never a platform action. A non-owner member is refused, and platform staff are unchanged.
The real WebAuthn endpoints run with a software authenticator; only Stripe is faked. Needs human
review: security code.
"""

import json
from typing import Any

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.fake_authenticator import FakeAuthenticator
from apps.accounts.tests.mfa import password_login, verified_login
from apps.core.models import AuditLog
from apps.payments.models import ConnectedAccount
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.vendors.models import SalesTermsVersion, Vendor
from apps.vendors.tests.factories import VendorMemberFactory

from .conftest import PICKER, SHOP

pytestmark = pytest.mark.django_db

ORIGIN = "http://testserver"
PAYMENTS = f"{SHOP}payments/"
TERMS = f"{SHOP}sales-terms/"
TERMS_DATA = {
    "body": "Terms from the form",
    "clauses": "7 | Limitation of liability",
    "governing_law": "IT",
    "b2b_court": "",
    "delivery_days": "3",
    "return_cost_bearer": "",
    "return_cost_info": "",
}


@pytest.fixture(autouse=True)
def stripe(monkeypatch: pytest.MonkeyPatch) -> FakeStripeClient:
    fake = FakeStripeClient()
    monkeypatch.setattr("apps.manager.views.client_from_settings", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _terms_url() -> None:
    assert reverse("manager:shop:sales_terms") == TERMS


def _post(client: Client, name: str, payload: dict[str, Any] | None = None) -> Any:
    return client.post(
        reverse(name), data=json.dumps(payload or {}), content_type="application/json"
    )


def _enrol_passkey(user: User) -> FakeAuthenticator:
    setup = Client()
    password_login(setup, user)
    authenticator = FakeAuthenticator(ORIGIN)
    options = _post(setup, "mfa:passkey_register_options").json()["publicKey"]
    credential = authenticator.create(options)
    assert _post(setup, "mfa:passkey_register", {"credential": credential}).status_code == 200
    return authenticator


def _login(client: Client, user: User) -> None:
    """A real login: second factor verified, no passkey window open."""
    verified_login(client, user, level=levels.NO_WINDOW)


def _do_it_once(client: Client, authenticator: FakeAuthenticator, refusal: Any) -> Any:
    options = _post(
        client,
        "levels:once_options",
        {"action": refusal.context["action"], "digest": refusal.context["digest"]},
    )
    assert options.status_code == 200
    credential = authenticator.get(options.json()["publicKey"])
    return _post(client, "levels:once", {"credential": credential})


def _open_window(client: Client, authenticator: FakeAuthenticator) -> Any:
    options = _post(client, "levels:options")
    assert options.status_code == 200
    credential = authenticator.get(options.json()["publicKey"])
    return _post(client, "levels:window", {"credential": credential})


def _choose(client: Client, vendor: Vendor) -> None:
    assert client.post(PICKER, {"vendor": str(vendor.public_id)}).status_code == 302


def _actions(name: str) -> list[AuditLog]:
    return list(AuditLog.objects.filter(action=name).order_by("id"))


# --- "Do it once" on the payout account (critical)


def test_the_owner_is_offered_do_it_once_and_a_window_for_the_payout_account(
    client: Client, owner: User
) -> None:
    _enrol_passkey(owner)
    _login(client, owner)
    refused = client.post(PAYMENTS)
    assert refused.status_code == 403
    text = refused.content.decode()
    assert "Do it once" in text
    assert "Open passkey window" in text
    assert "never covers another shop" in text
    assert "Your role may not open a passkey window" not in text


def test_the_owner_connects_the_payout_account_once_with_a_passkey(
    client: Client, owner: User, vendor: Vendor, stripe: FakeStripeClient
) -> None:
    authenticator = _enrol_passkey(owner)
    _login(client, owner)
    refused = client.post(PAYMENTS)
    assert _do_it_once(client, authenticator, refused).status_code == 200
    done = client.post(PAYMENTS)
    assert done.status_code == 302
    assert done["Location"] == stripe.links[0]
    assert ConnectedAccount.objects.filter(vendor=vendor).exists()
    assert client.post(PAYMENTS).status_code == 403  # once means once
    (granted,) = _actions(levels.SINGLE_GRANTED)
    assert isinstance(granted.after, dict)
    assert granted.after["action"] == "payout_details"
    assert granted.after["shop"] == levels.shop_key(vendor)
    assert _actions(levels.SINGLE_USED)


def test_a_confirmation_for_one_shop_is_never_spent_on_another(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor, stripe: FakeStripeClient
) -> None:
    VendorMemberFactory.create(vendor=other_vendor, user=owner, role="owner")
    authenticator = _enrol_passkey(owner)
    _login(client, owner)
    _choose(client, vendor)
    refused = client.post(PAYMENTS)
    assert _do_it_once(client, authenticator, refused).status_code == 200
    _choose(client, other_vendor)
    assert client.post(PAYMENTS).status_code == 403
    assert stripe.calls == []
    _choose(client, vendor)
    assert client.post(PAYMENTS).status_code == 302  # the grant is still there, for its shop


def _never_owner(user: object, shop: object) -> bool:
    return False


def test_ownership_is_checked_again_when_the_confirmation_is_spent(
    client: Client, owner: User, settings: Any, stripe: FakeStripeClient
) -> None:
    authenticator = _enrol_passkey(owner)
    _login(client, owner)
    refused = client.post(PAYMENTS)
    assert _do_it_once(client, authenticator, refused).status_code == 200
    settings.SHOP_OWNER_CHECK = f"{__name__}._never_owner"  # ownership lost meanwhile
    assert client.post(PAYMENTS).status_code == 403
    assert stripe.calls == []


def test_the_owner_cannot_confirm_an_action_the_server_did_not_offer(
    client: Client, owner: User
) -> None:
    _enrol_passkey(owner)
    _login(client, owner)
    for action in ("payout_details", "owner_role_grant", "export"):
        forged = {"action": action, "digest": "a" * 64}
        assert _post(client, "levels:once_options", forged).status_code == 403
    assert _post(client, "levels:once", {"credential": {}}).status_code == 403
    assert _post(client, "levels:window", {"credential": {}}).status_code == 403


def test_a_member_who_is_not_the_owner_is_refused(client: Client, vendor: Vendor) -> None:
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    _enrol_passkey(staff)
    _login(client, staff)
    assert client.post(PAYMENTS).status_code == 404
    assert client.post(TERMS, TERMS_DATA).status_code == 404
    forged = {"action": "payout_details", "digest": "a" * 64}
    assert _post(client, "levels:once_options", forged).status_code == 403
    assert _post(client, "levels:window", {"credential": {}}).status_code == 403
    assert not levels.may_confirm(staff, "payout_details", vendor)


def test_an_owner_without_a_passkey_is_told_to_add_one(client: Client, owner: User) -> None:
    _login(client, owner)
    text = client.post(PAYMENTS).content.decode()
    assert "Your account has no passkey yet" in text
    assert "Do it once" not in text


# --- A passkey window scoped to the owner's shop


def test_the_owner_opens_a_window_that_lets_the_own_shop_sensitive_action_through(
    client: Client, owner: User, vendor: Vendor
) -> None:
    authenticator = _enrol_passkey(owner)
    _login(client, owner)
    assert client.post(TERMS, TERMS_DATA).status_code == 403
    assert _open_window(client, authenticator).status_code == 200
    assert client.post(TERMS, TERMS_DATA).status_code == 302
    assert client.post(TERMS, TERMS_DATA | {"body": "Again"}).status_code == 302  # repeated
    assert SalesTermsVersion.objects.filter(vendor=vendor).count() == 2
    (started,) = _actions(levels.SESSION_STARTED)
    assert isinstance(started.after, dict)
    assert started.after["shop"] == levels.shop_key(vendor)


def test_the_owner_window_still_asks_a_passkey_for_the_critical_payout_action(
    client: Client, owner: User, stripe: FakeStripeClient
) -> None:
    authenticator = _enrol_passkey(owner)
    _login(client, owner)
    client.post(TERMS, TERMS_DATA)
    assert _open_window(client, authenticator).status_code == 200
    response = client.post(PAYMENTS)  # the login was a code: no per-action passkey yet
    assert response.status_code == 302
    assert "factor=passkey" in response["Location"]
    assert stripe.calls == []


def test_the_owner_window_does_not_cover_another_shop(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor
) -> None:
    VendorMemberFactory.create(vendor=other_vendor, user=owner, role="owner")
    authenticator = _enrol_passkey(owner)
    _login(client, owner)
    _choose(client, vendor)
    client.post(TERMS, TERMS_DATA)
    assert _open_window(client, authenticator).status_code == 200
    _choose(client, other_vendor)
    assert client.post(TERMS, TERMS_DATA).status_code == 403
    assert not SalesTermsVersion.objects.filter(vendor=other_vendor).exists()


def test_the_owner_window_never_covers_a_platform_action(
    client: Client, owner: User, vendor: Vendor
) -> None:
    authenticator = _enrol_passkey(owner)
    _login(client, owner)
    client.post(TERMS, TERMS_DATA)
    assert _open_window(client, authenticator).status_code == 200
    request = RequestFactory().post("/")
    request.session = client.session
    request.user = owner
    assert levels.window_covers(request, "sales_terms_publish", vendor)
    for action in ("owner_role_grant", "operator_config", "export", "bulk_edit"):
        assert not levels.window_covers(request, action, vendor)
        assert not levels.window_covers(request, action, None)


def test_platform_finance_staff_keep_the_full_window(client: Client) -> None:
    from django.contrib.auth.models import Group

    from apps.accounts.tests.factories import UserFactory

    finance = UserFactory.create()
    finance.groups.add(Group.objects.get(name="Platform: finance and payments"))
    authenticator = _enrol_passkey(finance)
    assert levels.may_elevate(finance)
    _login(client, finance)
    assert _open_window(client, authenticator).status_code == 200
    request = RequestFactory().post("/")
    request.session = client.session
    request.user = finance
    assert levels.window_covers(request, "export", None)
    (started,) = _actions(levels.SESSION_STARTED)
    assert isinstance(started.after, dict)
    assert "shop" not in started.after
