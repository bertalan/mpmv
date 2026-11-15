"""SEC.5 attack scenarios, category "Accounts", part 2: the passkey window and shop-scoped
confirmations (CORE.25, D-32 amendment of 2026-11-14), IDOR by `public_id`.

A vendor owner confirms a shop-space action of their own shop with a passkey ("Do it once" or a
window scoped to that shop). Each test plays a vendor member who holds a confirmation for shop A
and tries to turn it into power over shop B, over a platform action, or past its time. The real
WebAuthn endpoints run with a software authenticator; only Stripe is faked. Needs human review:
security code.
"""

import json
import time
import uuid
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.fake_authenticator import FakeAuthenticator
from apps.accounts.tests.mfa import password_login, verified_login
from apps.core.models import AuditLog
from apps.manager.tests.integration.conftest import PICKER, SHOP
from apps.orders.models import VendorOrder
from apps.orders.tests.factories import paid_vendor_order
from apps.payments.models import ConnectedAccount
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.vendors.models import SalesTermsVersion, Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

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


@pytest.fixture
def shop_a(db: None) -> Vendor:
    return VendorFactory.create(display_name="Shop A")


@pytest.fixture
def shop_b(db: None) -> Vendor:
    return VendorFactory.create(display_name="Shop B")


@pytest.fixture
def attacker(shop_a: Vendor, shop_b: Vendor) -> User:
    """Owner of shop A and a plain staff member of shop B: may pick both, owns only A."""
    user = VendorMemberFactory.create(vendor=shop_a, role="owner").user
    VendorMemberFactory.create(vendor=shop_b, user=user, role="staff")
    return user


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


def _choose(client: Client, vendor: Vendor) -> None:
    assert client.post(PICKER, {"vendor": str(vendor.public_id)}).status_code == 302


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


def _sign_in(client: Client, user: User, shop: Vendor) -> None:
    """A real login (second factor verified, no window open), working in `shop`."""
    verified_login(client, user, level=levels.NO_WINDOW)
    _choose(client, shop)


# --- "Do it once" for shop A, replayed on shop B


def test_a_do_it_once_for_shop_a_is_worthless_on_shop_b(
    client: Client, attacker: User, shop_a: Vendor, shop_b: Vendor, stripe: FakeStripeClient
) -> None:
    authenticator = _enrol_passkey(attacker)
    _sign_in(client, attacker, shop_a)
    refused = client.post(PAYMENTS)
    assert _do_it_once(client, authenticator, refused).status_code == 200

    _choose(client, shop_b)  # the same request, the same session, the other shop
    assert client.post(PAYMENTS).status_code in {403, 404}
    assert stripe.calls == []
    assert not ConnectedAccount.objects.filter(vendor=shop_b).exists()

    _choose(client, shop_a)  # the grant was not burnt by the failed attempt on B
    assert client.post(PAYMENTS).status_code == 302
    assert ConnectedAccount.objects.filter(vendor=shop_a).exists()
    assert not ConnectedAccount.objects.filter(vendor=shop_b).exists()


def test_a_non_owner_cannot_be_offered_or_confirm_an_action_on_the_other_shop(
    client: Client, attacker: User, shop_b: Vendor
) -> None:
    """The prompt never offers a confirmation on shop B, so the passkey endpoints refuse a
    forged one: the attacker is only a staff member there."""
    _enrol_passkey(attacker)
    _sign_in(client, attacker, shop_b)
    assert client.post(PAYMENTS).status_code in {403, 404}
    for action in sorted(levels.SHOP_OWNER_ACTIONS):
        forged = {"action": action, "digest": "a" * 64}
        assert _post(client, "levels:once_options", forged).status_code == 403
    assert _post(client, "levels:once", {"credential": {}}).status_code == 403
    assert not AuditLog.objects.filter(action=levels.SINGLE_GRANTED).exists()


def test_a_grant_is_bound_to_the_session_that_asked_for_it(
    client: Client, attacker: User, shop_a: Vendor, stripe: FakeStripeClient
) -> None:
    """A confirmation granted in one browser session cannot be spent from another session of the
    same user (a second device, or a session copied before the passkey was used)."""
    authenticator = _enrol_passkey(attacker)
    _sign_in(client, attacker, shop_a)
    refused = client.post(PAYMENTS)
    assert _do_it_once(client, authenticator, refused).status_code == 200

    elsewhere = Client()
    _sign_in(elsewhere, attacker, shop_a)
    assert elsewhere.post(PAYMENTS).status_code == 403
    assert stripe.calls == []
    assert client.post(PAYMENTS).status_code == 302  # the session that confirmed still can


def test_a_do_it_once_confirms_exactly_the_parameters_that_were_shown(
    client: Client, attacker: User, shop_a: Vendor
) -> None:
    """The grant binds the request digest: the same action with another body is refused."""
    authenticator = _enrol_passkey(attacker)
    _sign_in(client, attacker, shop_a)
    shown = client.post(TERMS, TERMS_DATA)
    assert shown.status_code == 403
    assert _do_it_once(client, authenticator, shown).status_code == 200

    tampered = client.post(TERMS, TERMS_DATA | {"body": "Terms swapped after the passkey"})
    assert tampered.status_code == 403
    assert not SalesTermsVersion.objects.filter(vendor=shop_a).exists()
    assert client.post(TERMS, TERMS_DATA).status_code == 302
    assert SalesTermsVersion.objects.filter(vendor=shop_a).count() == 1


# --- The shop-scoped window


def test_a_window_for_shop_a_does_not_let_a_non_owner_act_on_shop_b(
    client: Client, attacker: User, shop_a: Vendor, shop_b: Vendor
) -> None:
    authenticator = _enrol_passkey(attacker)
    _sign_in(client, attacker, shop_a)
    assert client.post(TERMS, TERMS_DATA).status_code == 403
    assert _open_window(client, authenticator).status_code == 200
    assert client.post(TERMS, TERMS_DATA).status_code == 302

    _choose(client, shop_b)
    assert client.post(TERMS, TERMS_DATA).status_code in {403, 404}
    assert not SalesTermsVersion.objects.filter(vendor=shop_b).exists()


def test_a_window_stops_working_when_the_ownership_is_taken_away(
    client: Client, attacker: User, shop_a: Vendor
) -> None:
    authenticator = _enrol_passkey(attacker)
    _sign_in(client, attacker, shop_a)
    client.post(TERMS, TERMS_DATA)
    assert _open_window(client, authenticator).status_code == 200
    assert client.post(TERMS, TERMS_DATA).status_code == 302

    shop_a.members.filter(user=attacker).update(role="staff")  # demoted while the window is open
    after = client.post(TERMS, TERMS_DATA | {"body": "After the demotion"})
    assert after.status_code in {403, 404}
    assert SalesTermsVersion.objects.filter(vendor=shop_a).count() == 1


@pytest.mark.parametrize("started_ago", [60 * 60 * 24, 60 * 60 * 4])
def test_a_window_past_its_cap_or_idle_limit_is_closed_by_the_next_request(
    client: Client, attacker: User, shop_a: Vendor, started_ago: int
) -> None:
    authenticator = _enrol_passkey(attacker)
    _sign_in(client, attacker, shop_a)
    client.post(TERMS, TERMS_DATA)
    assert _open_window(client, authenticator).status_code == 200
    session = client.session
    session[levels.STARTED_KEY] = time.time() - started_ago
    session[levels.ACTIVITY_KEY] = time.time() - started_ago
    session.save()

    assert client.post(TERMS, TERMS_DATA).status_code == 403
    assert client.session.get(levels.STARTED_KEY) is None
    assert not SalesTermsVersion.objects.filter(vendor=shop_a).exists()
    assert AuditLog.objects.filter(action=levels.SESSION_ENDED).exists()


def test_an_owner_window_never_confirms_the_payout_account_without_its_own_passkey(
    client: Client, attacker: User, shop_a: Vendor, stripe: FakeStripeClient
) -> None:
    """The critical payout action needs a per-action passkey even inside a window."""
    authenticator = _enrol_passkey(attacker)
    _sign_in(client, attacker, shop_a)
    client.post(TERMS, TERMS_DATA)
    assert _open_window(client, authenticator).status_code == 200
    response = client.post(PAYMENTS)
    assert response.status_code == 302
    assert "factor=passkey" in response["Location"]
    assert stripe.calls == []


def test_a_window_cannot_be_forged_from_the_client(
    client: Client, attacker: User, shop_a: Vendor
) -> None:
    """The window state is server-side: client-supplied fields or cookies never open one."""
    _enrol_passkey(attacker)
    _sign_in(client, attacker, shop_a)
    client.cookies[levels.STARTED_KEY] = str(time.time())
    client.cookies[levels.WINDOW_SHOP_KEY] = levels.shop_key(shop_a)
    response = client.post(
        TERMS, TERMS_DATA | {levels.STARTED_KEY: str(time.time()), levels.WINDOW_SHOP_KEY: "1"}
    )
    assert response.status_code == 403
    assert client.session.get(levels.STARTED_KEY) is None
    assert not SalesTermsVersion.objects.filter(vendor=shop_a).exists()


# --- IDOR: another shop's order by its public_id


def test_a_shop_owner_cannot_act_on_another_shops_order_by_its_public_id(
    client: Client, attacker: User, shop_a: Vendor, shop_b: Vendor
) -> None:
    foreign = paid_vendor_order(shop_b)
    _sign_in(client, attacker, shop_a)
    orders = f"{SHOP}orders/"
    state = foreign.state
    assert client.get(f"{orders}{foreign.public_id}/").status_code == 404
    for action in ("accept", "ship", "deliver"):
        assert client.post(f"{orders}{foreign.public_id}/{action}/").status_code == 404
    for tampered in (str(uuid.uuid4()), str(foreign.pk), "0", "../" + str(foreign.public_id)):
        assert client.get(f"{orders}{tampered}/").status_code == 404
        assert client.post(f"{orders}{tampered}/accept/").status_code == 404
    assert VendorOrder.objects.get(pk=foreign.pk).state == state
