"""CORE.4: passkey registration and authentication through the MFA endpoints.

A software authenticator plays the browser's part, so the full server side runs: options,
challenge kept in the session, py_webauthn verification, device storage and session upgrade.
"""

import json
import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts import engine
from apps.accounts.models import PasskeyDevice, RecoveryCode, User
from apps.accounts.tests.factories import (
    PasskeyDeviceFactory,
    RecoveryCodeDeviceFactory,
    RecoveryCodeFactory,
    TOTPDeviceFactory,
    UserFactory,
)
from apps.accounts.tests.fake_authenticator import FakeAuthenticator
from apps.accounts.tests.mfa import password_login, verified_login
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

ORIGIN = "http://testserver"  # the Django test client's host


def _post_json(client: Client, name: str, payload: dict[str, Any] | None = None) -> Any:
    return client.post(
        reverse(name), data=json.dumps(payload or {}), content_type="application/json"
    )


def _register(client: Client, authenticator: FakeAuthenticator) -> Any:
    options = _post_json(client, "mfa:passkey_register_options")
    assert options.status_code == 200
    credential = authenticator.create(options.json()["publicKey"])
    return _post_json(client, "mfa:passkey_register", {"credential": credential, "name": "Laptop"})


def _enrolled(client: Client, user: User) -> FakeAuthenticator:
    """Register a passkey for `user` in another session, then return its authenticator."""
    setup = Client()
    password_login(setup, user)
    authenticator = FakeAuthenticator(ORIGIN)
    assert _register(setup, authenticator).status_code == 200
    return authenticator


def test_registration_options_use_the_request_host_as_relying_party(client: Client) -> None:
    user = UserFactory.create(staff=True)
    password_login(client, user)
    public_key = _post_json(client, "mfa:passkey_register_options").json()["publicKey"]
    assert public_key["rp"]["id"] == "testserver"
    assert public_key["user"]["name"] == user.email
    assert engine.from_b64url(public_key["user"]["id"]) == user.public_id.bytes


def test_first_passkey_enrols_verifies_and_returns_recovery_codes(client: Client) -> None:
    user = UserFactory.create(staff=True)
    password_login(client, user)
    authenticator = FakeAuthenticator(ORIGIN)
    response = _register(client, authenticator)
    assert response.status_code == 200
    body = response.json()
    assert len(body["recovery_codes"]) == engine.RECOVERY_CODE_COUNT
    assert body["redirect"] == "/"

    device = PasskeyDevice.objects.get(user=user)
    assert device.confirmed
    assert device.name == "Laptop"
    assert device.credential_id == authenticator.credential_id_b64
    assert RecoveryCode.objects.filter(device__user=user).count() == engine.RECOVERY_CODE_COUNT
    assert client.get("/django-admin/").status_code == 200

    audit = AuditLog.objects.get(action="accounts.mfa.enrolled")
    assert audit.target_model == "accounts.passkeydevice"
    assert audit.after is not None
    assert audit.after["kind"] == "passkey"


def test_the_challenge_is_single_use(client: Client) -> None:
    password_login(client, UserFactory.create(staff=True))
    authenticator = FakeAuthenticator(ORIGIN)
    options = _post_json(client, "mfa:passkey_register_options").json()["publicKey"]
    credential = authenticator.create(options)
    assert _post_json(client, "mfa:passkey_register", {"credential": credential}).status_code == 200
    replay = _post_json(client, "mfa:passkey_register", {"credential": credential})
    assert replay.status_code == 400
    assert PasskeyDevice.objects.count() == 1


def test_a_response_to_another_challenge_is_rejected(client: Client) -> None:
    password_login(client, UserFactory.create(staff=True))
    _post_json(client, "mfa:passkey_register_options")
    stale = {
        "challenge": engine.b64url(b"\x00" * 32),
        "rp": {"id": "testserver"},
        "user": {"id": "AA"},
    }
    credential = FakeAuthenticator(ORIGIN).create(stale)
    response = _post_json(client, "mfa:passkey_register", {"credential": credential})
    assert response.status_code == 400
    assert not PasskeyDevice.objects.exists()
    assert client.get("/django-admin/").status_code == 302


def test_registration_needs_a_recent_verification_once_enrolled(client: Client) -> None:
    user = UserFactory.create(staff=True)
    TOTPDeviceFactory.create(user=user)
    password_login(client, user)
    assert _post_json(client, "mfa:passkey_register_options").status_code == 403


def test_a_passkey_verifies_a_password_session(client: Client) -> None:
    user = UserFactory.create(staff=True)
    authenticator = _enrolled(client, user)
    password_login(client, user)

    options = _post_json(client, "mfa:passkey_auth_options")
    assert options.status_code == 200
    public_key = options.json()["publicKey"]
    assert [c["id"] for c in public_key["allowCredentials"]] == [authenticator.credential_id_b64]

    assertion = authenticator.get(public_key)
    response = client.post(
        reverse("mfa:passkey_auth") + "?next=/django-admin/",
        data=json.dumps({"credential": assertion}),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json() == {"redirect": "/django-admin/"}
    assert client.get("/django-admin/").status_code == 200
    device = PasskeyDevice.objects.get()
    assert device.sign_count == 1
    assert device.last_used_at is not None


def test_another_users_passkey_does_not_verify(client: Client) -> None:
    owner = UserFactory.create(staff=True)
    authenticator = _enrolled(client, owner)
    intruder = UserFactory.create(staff=True)
    PasskeyDeviceFactory.create(user=intruder)
    password_login(client, intruder)
    public_key = _post_json(client, "mfa:passkey_auth_options").json()["publicKey"]
    response = _post_json(client, "mfa:passkey_auth", {"credential": authenticator.get(public_key)})
    assert response.status_code == 400
    assert client.get("/django-admin/").status_code == 302


def test_authentication_without_a_pending_challenge_is_rejected(client: Client) -> None:
    user = UserFactory.create(staff=True)
    authenticator = _enrolled(client, user)
    password_login(client, user)
    fake_options = {"challenge": engine.b64url(b"\x01" * 32), "rpId": "testserver"}
    response = _post_json(
        client, "mfa:passkey_auth", {"credential": authenticator.get(fake_options)}
    )
    assert response.status_code == 400


def test_a_verified_user_can_add_a_second_passkey_without_new_recovery_codes(
    client: Client,
) -> None:
    user = UserFactory.create(staff=True)
    RecoveryCodeFactory.create(device=RecoveryCodeDeviceFactory.create(user=user))
    verified_login(client, user)
    response = _register(client, FakeAuthenticator(ORIGIN))
    assert response.status_code == 200
    assert response.json()["recovery_codes"] == []


def test_passkey_endpoints_only_accept_post(client: Client) -> None:
    password_login(client, UserFactory.create(staff=True))
    for name in (
        "mfa:passkey_register_options",
        "mfa:passkey_register",
        "mfa:passkey_auth_options",
        "mfa:passkey_auth",
    ):
        assert client.get(reverse(name)).status_code == 405


PASSKEY_FORM_RE = re.compile(r"<form\b[^>]*\bdata-passkey=[^>]*>(.*?)</form>", re.DOTALL)


@pytest.mark.parametrize(
    ("name", "enrolled", "verified"),
    [("mfa:setup", False, False), ("mfa:overview", True, True), ("mfa:verify", True, False)],
)
def test_passkey_forms_never_post_natively(
    client: Client, name: str, enrolled: bool, verified: bool
) -> None:
    """The ceremony runs from passkey.js. A click before the script is ready (or without
    JavaScript) must not POST to the page itself, which only accepts GET."""
    user = UserFactory.create(staff=True)
    if enrolled:
        PasskeyDeviceFactory.create(user=user)
    if verified:
        verified_login(client, user)
    else:
        password_login(client, user)
    content = client.get(reverse(name)).content.decode()
    forms = [(match.group(0), match.group(1)) for match in PASSKEY_FORM_RE.finditer(content)]
    assert forms
    for form, body in forms:
        opening = form.split(">", 1)[0]
        assert 'method="post"' not in opening
        assert "csrfmiddlewaretoken" not in body  # a native GET would put it in the URL
        assert "data-csrf-token=" in opening
        assert "<button" in body
        assert all('type="button"' in button for button in re.findall(r"<button\b[^>]*>", body))


# --- The account security page (CORE.25 / CORE.4)

REQUIRES = "Your role requires a second factor before you can continue"


def test_the_security_page_shows_the_passkey_state_and_no_requires_text(client: Client) -> None:
    user = UserFactory.create(staff=True)
    PasskeyDeviceFactory.create(user=user, name="Work laptop")
    verified_login(client, user)
    content = client.get(reverse("mfa:setup") + "?from=security").content.decode()
    assert "Your account is protected with a passkey" in content
    assert "Work laptop" in content
    assert "never used" in content
    assert "Add another passkey" in content
    assert "Use an authenticator app" in content
    assert REQUIRES not in content


def test_the_security_page_says_a_role_needs_a_factor_when_there_is_none(client: Client) -> None:
    user = UserFactory.create(staff=True)
    password_login(client, user)
    content = client.get(reverse("mfa:setup")).content.decode()
    assert REQUIRES in content
    assert "Your account is protected with a passkey" not in content


def test_the_security_page_keeps_the_requires_text_for_the_mfa_gate(client: Client) -> None:
    user = UserFactory.create(staff=True)
    password_login(client, user)
    content = client.get(reverse("mfa:setup") + "?next=/manager/platform/").content.decode()
    assert REQUIRES in content
