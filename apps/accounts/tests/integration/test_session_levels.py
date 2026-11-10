"""CORE.25 (D-32): restricted and operational session levels, "do it once", group 3 prompts.

A software authenticator plays the browser through the real WebAuthn endpoints; only the
authenticator is fake. Needs human review: security code.
"""

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.fake_authenticator import FakeAuthenticator
from apps.accounts.tests.mfa import password_login, verified_login
from apps.core.models import AuditLog
from apps.core.tests.factories import PlatformOperatorFactory

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.accounts.tests.integration.urls")]

ORIGIN = "http://testserver"


def _post(client: Client, name: str, payload: dict[str, Any] | None = None) -> Any:
    return client.post(
        reverse(name), data=json.dumps(payload or {}), content_type="application/json"
    )


def _staff_with_passkey(*, elevate: bool = True) -> tuple[User, FakeAuthenticator]:
    """A staff user who may elevate and has a passkey (enrolled in another session)."""
    user = UserFactory.create(staff=True)
    if elevate:
        user.user_permissions.add(Permission.objects.get(codename=levels.ELEVATE_CODENAME))
    setup = Client()
    password_login(setup, user)
    authenticator = FakeAuthenticator(ORIGIN)
    options = _post(setup, "mfa:passkey_register_options").json()["publicKey"]
    credential = authenticator.create(options)
    assert _post(setup, "mfa:passkey_register", {"credential": credential}).status_code == 200
    return user, authenticator


def _restricted_session(client: Client, user: User) -> None:
    """What a login gives: second factor verified, no operational mode."""
    verified_login(client, user, level=levels.RESTRICTED)


def _elevate(client: Client, authenticator: FakeAuthenticator, **assertion: Any) -> Any:
    options = _post(client, "levels:options")
    assert options.status_code == 200
    credential = authenticator.get(options.json()["publicKey"], **assertion)
    return _post(client, "levels:operational", {"credential": credential})


def _do_it_once(client: Client, authenticator: FakeAuthenticator, refusal: Any) -> Any:
    options = _post(
        client,
        "levels:once_options",
        {"action": refusal.context["action"], "digest": refusal.context["digest"]},
    )
    assert options.status_code == 200
    credential = authenticator.get(options.json()["publicKey"])
    return _post(client, "levels:once", {"credential": credential})


def _actions(name: str) -> list[AuditLog]:
    return list(AuditLog.objects.filter(action=name).order_by("id"))


def _after(row: AuditLog) -> dict[str, Any]:
    assert isinstance(row.after, dict)
    return row.after


def _age(client: Client, key: str, seconds: float) -> None:
    session = client.session
    session[key] -= seconds
    session.save()


# --- Restricted is the default


def test_after_login_group_2_is_refused_and_everyday_work_is_free(client: Client) -> None:
    user, _ = _staff_with_passkey()
    _restricted_session(client, user)
    assert client.get(reverse("test-everyday")).status_code == 200
    refused = client.get(reverse("test-export"))
    assert refused.status_code == 403
    assert b"export-csv" not in refused.content
    assert refused.context["action"] == "export"


def test_the_refusal_offers_do_it_once_and_switching(client: Client) -> None:
    user, _ = _staff_with_passkey()
    _restricted_session(client, user)
    text = client.get(reverse("test-export")).content.decode()
    assert "Do it once" in text
    assert "Switch to operational mode" in text


def test_json_clients_get_a_machine_readable_refusal(client: Client) -> None:
    user, _ = _staff_with_passkey()
    _restricted_session(client, user)
    response = client.get(reverse("test-export"), headers={"accept": "application/json"})
    assert response.status_code == 403
    assert response.json()["error"] == "operational_mode_required"


def test_every_group_2_default_is_refused_in_restricted_mode(client: Client) -> None:
    user, _ = _staff_with_passkey()
    _restricted_session(client, user)
    for name in ("test-export", "test-backup"):
        assert client.get(reverse(name)).status_code == 403


# --- Elevation


def test_a_fresh_passkey_starts_operational_mode_and_it_is_audited(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    assert _elevate(client, authenticator).status_code == 200
    assert client.get(reverse("test-export")).status_code == 200
    assert client.get(reverse("test-export")).status_code == 200  # repeated
    (started,) = _actions(levels.SESSION_STARTED)
    assert started.actor_id == user.pk
    assert _after(started)["kind"] == "session"


def test_a_role_without_the_elevate_permission_cannot_elevate(client: Client) -> None:
    user, authenticator = _staff_with_passkey(elevate=False)
    _restricted_session(client, user)
    assert _elevate(client, authenticator).status_code == 403
    assert client.get(reverse("test-export")).status_code == 403
    assert not _actions(levels.SESSION_STARTED)


def test_a_user_without_a_passkey_cannot_elevate(client: Client) -> None:
    user = UserFactory.create(staff=True)
    user.user_permissions.add(Permission.objects.get(codename=levels.ELEVATE_CODENAME))
    _restricted_session(client, user)  # verified with TOTP only
    assert _post(client, "levels:options").status_code == 400
    assert _post(client, "levels:operational", {"credential": {}}).status_code == 403
    assert client.get(reverse("test-export")).status_code == 403


def test_a_stolen_session_without_the_passkey_cannot_elevate(client: Client) -> None:
    user, _ = _staff_with_passkey()
    _restricted_session(client, user)  # the thief has the cookie, not the authenticator
    thief = FakeAuthenticator(ORIGIN)
    thief.credential_id = b"x" * 32
    options = _post(client, "levels:options").json()["publicKey"]
    forged = thief.get(options)
    assert _post(client, "levels:operational", {"credential": forged}).status_code == 400
    assert _post(client, "levels:operational", {"totp": "123456"}).status_code == 400
    assert client.get(reverse("test-export")).status_code == 403


def test_an_assertion_without_user_verification_is_refused(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    assert _elevate(client, authenticator, user_verified=False).status_code == 400
    assert client.get(reverse("test-export")).status_code == 403


def test_the_challenge_is_single_use(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    options = _post(client, "levels:options").json()["publicKey"]
    credential = authenticator.get(options)
    assert _post(client, "levels:operational", {"credential": credential}).status_code == 200
    assert _post(client, "levels:operational", {"credential": credential}).status_code == 400


# --- The mode ends


def test_idle_for_16_minutes_ends_operational_mode(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    _elevate(client, authenticator)
    _age(client, levels.ACTIVITY_KEY, 16 * 60)
    assert client.get(reverse("test-export")).status_code == 403
    (ended,) = _actions(levels.SESSION_ENDED)
    assert _after(ended)["reason"] == "idle"


def test_activity_keeps_the_mode_alive_until_the_cap(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    _elevate(client, authenticator)
    _age(client, levels.ACTIVITY_KEY, 10 * 60)
    assert client.get(reverse("test-export")).status_code == 200  # 10 min idle: still on
    _age(client, levels.STARTED_KEY, 2 * 3600 + 1)
    assert client.get(reverse("test-export")).status_code == 403
    assert _after(_actions(levels.SESSION_ENDED)[-1])["reason"] == "cap"


def test_logout_ends_operational_mode_and_audits_it(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    _elevate(client, authenticator)
    client.logout()
    (ended,) = _actions(levels.SESSION_ENDED)
    assert _after(ended)["reason"] == "logout"
    assert ended.actor_id == user.pk


def test_end_now_returns_to_restricted(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    _elevate(client, authenticator)
    response = client.post(reverse("levels:end"))
    assert response.status_code == 302
    assert client.get(reverse("test-export")).status_code == 403
    assert _after(_actions(levels.SESSION_ENDED)[-1])["reason"] == "manual"


def test_the_banner_shows_the_mode_and_an_end_now_button(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    _elevate(client, authenticator)
    from django.template import Context, Template

    request = client.get(reverse("test-export")).wsgi_request
    banner = Template("{% load session_levels %}{% session_level_banner %}").render(
        Context({"request": request})
    )
    assert "Operational mode" in banner
    assert "End now" in banner


# --- Do it once


def test_acceptance_flow_of_the_four_steps(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    # (1) right after login a bulk export is refused
    refused = client.get(reverse("test-export"))
    assert refused.status_code == 403
    # (2) "do it once" with a fresh passkey succeeds once, a second export is refused
    assert _do_it_once(client, authenticator, refused).status_code == 200
    assert client.get(reverse("test-export")).status_code == 200
    assert client.get(reverse("test-export")).status_code == 403
    # (3) operational mode allows repeated exports
    assert _elevate(client, authenticator).status_code == 200
    assert client.get(reverse("test-export")).status_code == 200
    assert client.get(reverse("test-export")).status_code == 200
    # (4) after 16 minutes idle exports are refused again
    _age(client, levels.ACTIVITY_KEY, 16 * 60)
    assert client.get(reverse("test-export")).status_code == 403


def test_do_it_once_keeps_the_session_restricted_and_is_audited_as_single(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    _do_it_once(client, authenticator, client.get(reverse("test-export")))
    assert client.get(reverse("test-export")).status_code == 200
    assert client.get(reverse("test-backup")).status_code == 403  # another action
    assert not _actions(levels.SESSION_STARTED)
    granted = _actions(levels.SINGLE_GRANTED)
    assert [_after(row)["kind"] for row in granted] == ["single"]
    assert _after(granted[0])["action"] == "export"


def test_a_single_authorisation_is_bound_to_the_parameters(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    refused = client.get(reverse("test-export"), {"vendor": "a"})
    _do_it_once(client, authenticator, refused)
    assert client.get(reverse("test-export"), {"vendor": "b"}).status_code == 403
    assert client.get(reverse("test-export"), {"vendor": "a"}).status_code == 200


def test_a_single_authorisation_expires_after_about_two_minutes(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    _do_it_once(client, authenticator, client.get(reverse("test-export")))
    session = client.session
    for grant in session[levels.SINGLE_KEY]:
        grant["expires"] -= 121
    session.save()
    assert client.get(reverse("test-export")).status_code == 403


def test_do_it_once_needs_the_elevate_permission_and_a_passkey(client: Client) -> None:
    user, _ = _staff_with_passkey(elevate=False)
    _restricted_session(client, user)
    refused = client.get(reverse("test-export"))
    options = _post(
        client,
        "levels:once_options",
        {"action": refused.context["action"], "digest": refused.context["digest"]},
    )
    assert options.status_code == 403
    assert client.get(reverse("test-export")).status_code == 403


# --- Group 3


def test_group_3_prompts_for_a_passkey_even_inside_operational_mode(client: Client) -> None:
    user, _ = _staff_with_passkey()
    verified_login(client, user, verified_at=time.time() - 3600)  # operational, old second factor
    response = client.get(reverse("test-payout"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("mfa:verify"))
    assert "factor=passkey" in response["Location"]


def test_group_3_does_not_accept_a_totp_confirmation(client: Client) -> None:
    user, _ = _staff_with_passkey()
    verified_login(client, user)  # fresh TOTP verification, operational
    assert client.get(reverse("test-payout")).status_code == 302


def test_group_3_passes_after_a_fresh_passkey(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    verified_login(client, user, verified_at=time.time() - 3600)
    options = _post(client, "mfa:passkey_auth_options").json()["publicKey"]
    credential = authenticator.get(options)
    assert _post(client, "mfa:passkey_auth", {"credential": credential}).status_code == 200
    assert client.get(reverse("test-payout")).status_code == 200


def test_group_3_is_refused_in_restricted_mode_with_no_do_it_once(client: Client) -> None:
    user, _ = _staff_with_passkey()
    _restricted_session(client, user)
    refused = client.get(reverse("test-payout"))
    assert refused.status_code == 403
    assert "Do it once" not in refused.content.decode()


# --- Tunable without code, audit of prompts


def _configure(**policy: Any) -> None:
    PlatformOperatorFactory.create(
        open_ended=True, valid_from=datetime(2020, 1, 1, tzinfo=UTC), session_policy=policy
    )


def test_moving_an_action_to_the_free_list_frees_it_with_no_code_change(client: Client) -> None:
    user, _ = _staff_with_passkey()
    _restricted_session(client, user)
    assert client.get(reverse("test-export")).status_code == 403
    _configure(actions={"restricted": ["export"]})
    assert client.get(reverse("test-export")).status_code == 200


def test_moving_an_action_to_the_passkey_list_makes_it_group_3(client: Client) -> None:
    user, _ = _staff_with_passkey()
    verified_login(client, user, verified_at=time.time() - 3600)  # operational
    assert client.get(reverse("test-export")).status_code == 200
    _configure(actions={"passkey": ["export"]})
    assert client.get(reverse("test-export")).status_code == 302


def test_a_tuned_idle_timer_applies(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    _configure(idle_minutes=5)
    _elevate(client, authenticator)
    _age(client, levels.ACTIVITY_KEY, 6 * 60)  # 6 min > the tuned 5 min idle
    assert client.get(reverse("test-export")).status_code == 403


def test_every_prompt_is_audited(client: Client) -> None:
    user, _ = _staff_with_passkey()
    _restricted_session(client, user)
    client.get(reverse("test-export"))
    client.get(reverse("test-payout"))
    prompts = _actions(levels.PROMPT)
    assert [_after(row)["action"] for row in prompts] == ["export", "payout_details"]
    assert [_after(row)["group"] for row in prompts] == [2, 3]
    assert all(row.actor_id == user.pk for row in prompts)


def test_enrolling_a_passkey_needs_a_fresh_second_factor(client: Client) -> None:
    user = UserFactory.create(staff=True)
    verified_login(client, user, verified_at=time.time() - 3600, level=levels.RESTRICTED)
    assert _post(client, "mfa:passkey_register_options").status_code == 403


# --- Sweep


KNOWN_UNWIRED = {
    # Group 2 and 3 actions whose view does not exist yet (follow-ups in the PR).
    "bulk_edit",
    "tax_in_clear",
    "impersonation",
    "api_key",
    "mfa_disable",
    "owner_role_grant",
    "bootstrap_platform_owner",
    "backup",
    "export",
    "payout_details",
}


def _gated_actions() -> set[str]:
    """Action keys named at `requires_level("...")` or `step_up(..., level="...")` in app code."""
    pattern = re.compile(r"""(?:requires_level\(|level=)\s*["'](\w+)["']""")
    found: set[str] = set()
    for path in Path(__file__).resolve().parents[4].glob("apps/*/*.py"):
        found |= set(pattern.findall(path.read_text()))
    return found


def test_every_group_2_and_3_action_has_a_decorator_or_is_listed_as_not_built() -> None:
    from apps.core.session_policy import parse_policy

    policy = parse_policy({})
    sensitive = policy.operational | policy.passkey
    missing = sensitive - _gated_actions() - KNOWN_UNWIRED
    assert not missing, f"group 2 or 3 actions with no requires_level view: {sorted(missing)}"
    assert {"role_grant", "vat_regime", "audit_in_clear"} <= _gated_actions()
    wired = _gated_actions() & KNOWN_UNWIRED - {"export", "payout_details", "backup"}
    assert not wired, f"remove from KNOWN_UNWIRED, now gated: {sorted(wired)}"
