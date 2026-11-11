"""CORE.25 (D-32): restricted and operational session levels, "do it once", group 3 prompts.

A software authenticator plays the browser through the real WebAuthn endpoints; only the
authenticator is fake. Needs human review: security code.
"""

import datetime as dt
import json
import re
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

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
    from apps.accounts.models import ElevationGrant

    ElevationGrant.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))
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


def _group3_passkey(client: Client, authenticator: FakeAuthenticator, **assertion: Any) -> Any:
    """The passkey step the group 3 redirect leads to (`factor=passkey`: UV required)."""
    suffix = "?factor=passkey"
    options = client.post(reverse("mfa:passkey_auth_options") + suffix)
    assert options.status_code == 200
    credential = authenticator.get(options.json()["publicKey"], **assertion)
    return client.post(
        reverse("mfa:passkey_auth") + suffix,
        data=json.dumps({"credential": credential}),
        content_type="application/json",
    )


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
    assert _group3_passkey(client, authenticator).status_code == 200
    assert client.get(reverse("test-payout")).status_code == 200


def test_group_3_is_refused_in_restricted_mode_and_offers_do_it_once(client: Client) -> None:
    user, _ = _staff_with_passkey()
    _restricted_session(client, user)
    refused = client.get(reverse("test-payout"))
    assert refused.status_code == 403
    assert "Do it once" in refused.content.decode()


def _group3_options(client: Client, refusal: Any) -> Any:
    return _post(
        client,
        "levels:once_options",
        {"action": refusal.context["action"], "digest": refusal.context["digest"]},
    )


def test_a_group_3_one_off_needs_a_fresh_user_verified_assertion(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    refused = client.get(reverse("test-payout"))
    options = _group3_options(client, refused)
    assert options.status_code == 200
    credential = authenticator.get(options.json()["publicKey"])
    assert _post(client, "levels:once", {"credential": credential}).status_code == 200
    assert client.get(reverse("test-payout")).status_code == 200
    assert client.get(reverse("test-payout")).status_code == 403  # single use
    assert not _actions(levels.SESSION_STARTED)  # still restricted
    granted = [_after(r) for r in _actions(levels.SINGLE_GRANTED)]
    assert [(g["kind"], g["action"]) for g in granted] == [("single", "payout_details")]
    assert [_after(r)["action"] for r in _actions(levels.SINGLE_USED)] == ["payout_details"]


def test_a_group_3_one_off_refuses_an_assertion_without_user_verification(
    client: Client,
) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    refused = client.get(reverse("test-payout"))
    options = _group3_options(client, refused).json()["publicKey"]
    credential = authenticator.get(options, user_verified=False)
    assert _post(client, "levels:once", {"credential": credential}).status_code == 400
    assert client.get(reverse("test-payout")).status_code == 403


def test_an_earlier_login_or_elevation_does_not_authorise_a_group_3_one_off(
    client: Client,
) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    assert _elevate(client, authenticator).status_code == 200  # fresh UV passkey, operational
    assert client.post(reverse("levels:end")).status_code == 302  # back to restricted
    assert client.get(reverse("test-payout")).status_code == 403  # nothing carried over


def test_a_group_3_one_off_assertion_cannot_be_replayed(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    refused = client.get(reverse("test-payout"))
    options = _group3_options(client, refused).json()["publicKey"]
    credential = authenticator.get(options)
    assert _post(client, "levels:once", {"credential": credential}).status_code == 200
    assert _post(client, "levels:once", {"credential": credential}).status_code == 400
    assert client.get(reverse("test-payout")).status_code == 200
    assert client.get(reverse("test-payout")).status_code == 403


def test_a_group_2_grant_does_not_open_a_group_3_action(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    _do_it_once(client, authenticator, client.get(reverse("test-export")))
    assert client.get(reverse("test-payout")).status_code == 403


def test_a_group_3_one_off_expires_after_about_two_minutes(client: Client) -> None:
    from apps.accounts.models import ElevationGrant

    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    _do_it_once(client, authenticator, client.get(reverse("test-payout")))
    ElevationGrant.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))
    assert client.get(reverse("test-payout")).status_code == 403


def test_a_group_3_one_off_needs_the_elevate_permission(client: Client) -> None:
    user, _ = _staff_with_passkey(elevate=False)
    _restricted_session(client, user)
    refused = client.get(reverse("test-payout"))
    assert "Do it once" not in refused.content.decode()
    assert _group3_options(client, refused).status_code == 403


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


WIRED = {
    "role_grant",
    "vat_regime",
    "audit_in_clear",
    "mfa_disable",
    "owner_role_grant",
    "payout_details",
    "api_key",
    "operator_config",
}
# Actions whose web view does not exist yet: nothing to decorate (follow-ups in the PR).
NOT_BUILT = {"tax_in_clear", "export", "backup", "bulk_edit", "impersonation"}
# Actions that are not web views: `manage.py bootstrap_platform_owner` runs with shell access on
# the server, outside any browser session, so session levels cannot apply to it.
NO_SESSION = {"bootstrap_platform_owner"}


def _source_files() -> list[Path]:
    root = Path(__file__).resolve().parents[4]
    return [p for p in root.glob("apps/*/*.py")]


def _gated_actions() -> set[str]:
    """Action keys named at `requires_level`, `level_gated_action` or `level=` in app code."""
    pattern = re.compile(r"""(?:requires_level\(|level_gated_action\(|level=)\s*["'](\w+)["']""")
    found: set[str] = set()
    for path in _source_files():
        found |= set(pattern.findall(path.read_text()))
    return found


def test_every_configured_action_is_wired_not_built_or_outside_sessions() -> None:
    from apps.core.session_policy import parse_policy

    policy = parse_policy({})
    configured = policy.operational | policy.passkey
    assert configured == WIRED | NOT_BUILT | NO_SESSION, "classify every configured action"
    missing = WIRED - _gated_actions()
    assert not missing, f"existing views without their requires_level: {sorted(missing)}"


def test_no_unclassified_view_looks_like_a_sensitive_action() -> None:
    """A view or admin action named like a group 2 action must be gated: fails on a new one."""
    pattern = re.compile(r"def \w*(export|impersonat|bulk_edit|backup|dump)\w*", re.IGNORECASE)
    hits = []
    for path in _source_files():
        if path.name in {"admin.py", "views.py"} or path.name.endswith("_views.py"):
            text = path.read_text()
            if pattern.search(text) and "requires_level(" not in text and "level=" not in text:
                hits.append(path.name)
    assert not hits, f"sensitive-looking views with no level gate: {hits}"


# --- Security review fixes (PR 234)

from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.test import RequestFactory  # noqa: E402


def test_a_login_passkey_without_user_verification_does_not_satisfy_group_3(
    client: Client,
) -> None:
    user, authenticator = _staff_with_passkey()
    verified_login(client, user, verified_at=time.time() - 3600)
    options = _post(client, "mfa:passkey_auth_options").json()["publicKey"]
    credential = authenticator.get(options, user_verified=False)
    assert _post(client, "mfa:passkey_auth", {"credential": credential}).status_code == 200
    assert client.get(reverse("test-payout")).status_code == 302  # login passkey: not enough


def test_the_group_3_passkey_step_refuses_an_assertion_without_uv(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    verified_login(client, user, verified_at=time.time() - 3600)
    assert _group3_passkey(client, authenticator, user_verified=False).status_code == 400
    assert client.get(reverse("test-payout")).status_code == 302


def test_a_later_totp_verification_clears_the_group_3_passkey(client: Client) -> None:
    from apps.accounts.tests.mfa import totp_token

    user, authenticator = _staff_with_passkey()
    device = verified_login(client, user, verified_at=time.time() - 3600)
    assert _group3_passkey(client, authenticator).status_code == 200
    assert client.get(reverse("test-payout")).status_code == 200
    client.post(reverse("mfa:verify"), {"method": "totp", "token": totp_token(device)})
    assert client.get(reverse("test-payout")).status_code == 302


# Digest of what a request does


def _digest(request: Any) -> str:
    return levels.params_digest(request)


def test_the_digest_covers_method_query_body_and_files() -> None:
    rf = RequestFactory()
    base = _digest(rf.get("/x/", {"a": "1", "b": "2"}))
    assert base == _digest(rf.get("/x/", {"b": "2", "a": "1"}))  # canonical order
    assert base != _digest(rf.get("/x/", {"a": "1", "b": "2", "c": "3"}))
    assert base != _digest(rf.post("/x/", {"a": "1", "b": "2"}))  # method
    small = _digest(rf.post("/x/", json.dumps({"rows": 10}), content_type="application/json"))
    big = _digest(rf.post("/x/", json.dumps({"rows": 10000}), content_type="application/json"))
    assert small != big
    same = _digest(
        rf.post("/x/", json.dumps({"b": 1, "a": 2}, indent=3), content_type="application/json")
    )
    assert same == _digest(rf.post("/x/", '{"a":2,"b":1}', content_type="application/json"))
    one = _digest(rf.post("/x/", {"f": SimpleUploadedFile("a.csv", b"1")}))
    other = _digest(rf.post("/x/", {"f": SimpleUploadedFile("a.csv", b"2")}))
    renamed = _digest(rf.post("/x/", {"f": SimpleUploadedFile("b.csv", b"1")}))
    assert len({one, other, renamed}) == 3


def test_the_digest_encoding_is_unambiguous() -> None:
    rf = RequestFactory()
    assert _digest(rf.get("/x/", {"a": "b\x1fc"})) != _digest(rf.get("/x/", {"a": "b", "c": ""}))
    assert _digest(rf.get("/x/", {"a": "1\x1e"})) != _digest(rf.get("/x/", {"a": "1"}))


def test_a_grant_for_a_small_export_cannot_run_a_bigger_one(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    url = reverse("test-export-json")

    def body(rows: int) -> dict[str, Any]:
        return {"data": json.dumps({"rows": rows}), "content_type": "application/json"}

    refused = client.post(url, **body(10))
    assert refused.status_code == 403
    _do_it_once(client, authenticator, refused)
    assert client.post(url, **body(10000)).status_code == 403  # differs only in the body
    assert client.post(url, **body(10)).status_code == 200


def test_a_grant_survives_reordered_but_not_extra_parameters(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    refused = client.get(reverse("test-export"), {"a": "1", "b": "2"})
    _do_it_once(client, authenticator, refused)
    assert client.get(reverse("test-export"), {"a": "1", "b": "2", "limit": "9"}).status_code == 403
    assert client.get(reverse("test-export"), {"b": "2", "a": "1"}).status_code == 200


# Single use under concurrency


@pytest.mark.django_db(transaction=True)
def test_a_grant_is_spent_once_even_with_parallel_requests() -> None:
    import threading

    from django.db import connection

    user = UserFactory.create(staff=True)
    levels.create_grant("session-key", user, "export", "digest-1", ttl=120)
    results: list[bool] = []
    barrier = threading.Barrier(8)

    def spend() -> None:
        try:
            barrier.wait()
            results.append(levels.spend_grant("session-key", user, "export", "digest-1"))
        finally:
            connection.close()

    threads = [threading.Thread(target=spend) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 1
    assert results.count(False) == 7


def test_a_grant_is_bound_to_its_session_user_action_and_digest() -> None:
    user, other = UserFactory.create(staff=True), UserFactory.create(staff=True)
    levels.create_grant("s1", user, "export", "d", ttl=120)
    assert not levels.spend_grant("s2", user, "export", "d")
    assert not levels.spend_grant("s1", other, "export", "d")
    assert not levels.spend_grant("s1", user, "backup", "d")
    assert not levels.spend_grant("s1", user, "export", "e")
    assert levels.spend_grant("s1", user, "export", "d")
    assert not levels.spend_grant("s1", user, "export", "d")


def test_the_grant_row_stores_a_hash_of_the_session_key_not_the_key(client: Client) -> None:
    from apps.accounts.models import ElevationGrant

    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    _do_it_once(client, authenticator, client.get(reverse("test-export")))
    grant = ElevationGrant.objects.get()
    assert client.session.session_key not in (grant.session_hash, grant.digest)
    assert grant.used_at is None
    client.get(reverse("test-export"))
    grant.refresh_from_db()
    assert grant.used_at is not None


# Challenges, caps, re-login, polling, failing closed


def test_a_passkey_challenge_expires(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    options = _post(client, "levels:options").json()["publicKey"]
    credential = authenticator.get(options)
    session = client.session
    session[levels.CHALLENGE_KEY]["issued_at"] -= 121
    session.save()
    assert _post(client, "levels:operational", {"credential": credential}).status_code == 400


def test_a_challenge_for_one_action_cannot_start_operational_mode(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    refused = client.get(reverse("test-export"))
    options = _post(
        client,
        "levels:once_options",
        {"action": "export", "digest": refused.context["digest"]},
    ).json()["publicKey"]
    credential = authenticator.get(options)
    assert _post(client, "levels:operational", {"credential": credential}).status_code == 400
    assert client.get(reverse("test-backup")).status_code == 403


def test_once_is_refused_for_a_free_action(client: Client) -> None:
    _configure(actions={"restricted": ["export"]})
    user, _ = _staff_with_passkey()
    _restricted_session(client, user)
    response = _post(client, "levels:once_options", {"action": "export", "digest": "d" * 64})
    assert response.status_code == 400


def test_re_elevating_does_not_extend_the_two_hour_cap(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    _elevate(client, authenticator)
    session = client.session
    started = session[levels.STARTED_KEY] = session[levels.STARTED_KEY] - 7000
    session.save()
    assert _elevate(client, authenticator).status_code == 200
    assert client.session[levels.STARTED_KEY] == started


def test_logging_in_again_in_the_same_session_ends_operational_mode(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    _elevate(client, authenticator)
    client.force_login(user)  # the same user signs in again: Django keeps the session data
    assert levels.STARTED_KEY not in client.session
    assert _after(_actions(levels.SESSION_ENDED)[-1])["reason"] == "relogin"


def test_polling_requests_do_not_keep_operational_mode_alive(client: Client) -> None:
    user, authenticator = _staff_with_passkey()
    _restricted_session(client, user)
    _elevate(client, authenticator)
    _age(client, levels.ACTIVITY_KEY, 10 * 60)
    before = client.session[levels.ACTIVITY_KEY]
    client.get(reverse("test-everyday"), headers={"x-mpmv-poll": "1"})
    assert client.session[levels.ACTIVITY_KEY] == before
    client.get(reverse("test-everyday"), headers={"sec-purpose": "prefetch"})
    assert client.session[levels.ACTIVITY_KEY] == before
    client.get(reverse("test-everyday"))
    assert client.session[levels.ACTIVITY_KEY] > before


def test_an_invalid_stored_policy_fails_closed(client: Client, caplog: Any) -> None:
    user, _ = _staff_with_passkey()
    verified_login(client, user, verified_at=time.time() - 3600)  # operational, old passkey
    operator = PlatformOperatorFactory.create(
        open_ended=True, valid_from=datetime(2020, 1, 1, tzinfo=UTC)
    )
    type(operator).objects.filter(pk=operator.pk).update(session_policy={"idle_minutes": -3})
    assert client.get(reverse("test-everyday")).status_code == 302  # even everyday work: group 3
    assert "session policy" in caplog.text.lower()


def test_the_admin_warns_about_an_invalid_stored_policy(client: Client) -> None:
    root = UserFactory.create(superuser=True)
    verified_login(client, root)
    operator = PlatformOperatorFactory.create(
        open_ended=True, valid_from=datetime(2020, 1, 1, tzinfo=UTC)
    )
    type(operator).objects.filter(pk=operator.pk).update(session_policy={"idle_minutes": -3})
    response = client.get(reverse("admin:core_platformoperator_changelist"))
    assert "session policy is invalid" in response.content.decode()


# Real views (restricted mode refuses them)


def _superuser_restricted(client: Client) -> User:
    root = UserFactory.create(superuser=True)
    verified_login(client, root, level=levels.RESTRICTED)
    return root


@pytest.mark.parametrize(
    ("name", "action"),
    [
        ("admin:core_auditlog_changelist", "audit_in_clear"),
        ("admin:accounts_user_add", "role_grant"),
        ("admin:auth_group_add", "role_grant"),
    ],
)
def test_real_admin_views_refuse_in_restricted_mode(client: Client, name: str, action: str) -> None:
    _superuser_restricted(client)
    response = client.get(reverse(name))
    assert response.status_code == 403
    assert response.context["action"] == action


def test_real_group_admin_change_and_delete_refuse_in_restricted_mode(client: Client) -> None:
    from django.contrib.auth.models import Group

    _superuser_restricted(client)
    group = Group.objects.create(name="Support")
    assert client.get(reverse("admin:auth_group_change", args=[group.pk])).status_code == 403
    assert client.get(reverse("admin:auth_group_delete", args=[group.pk])).status_code == 403


def test_removing_a_factor_needs_operational_mode_and_a_fresh_passkey(client: Client) -> None:
    from apps.accounts.models import PasskeyDevice

    user, _ = _staff_with_passkey()
    device = PasskeyDevice.objects.get(user=user)
    url = reverse("mfa:remove_device", args=["passkey", str(device.public_id)])
    verified_login(client, user, level=levels.RESTRICTED)
    assert client.post(url).status_code == 403  # restricted
    verified_login(client, user)  # operational, but only a TOTP confirmation
    assert client.post(url).status_code == 302
    assert PasskeyDevice.objects.filter(pk=device.pk).exists()


def test_a_factory_grant_is_live_and_unspent() -> None:
    from apps.accounts.tests.factories import ElevationGrantFactory

    grant = ElevationGrantFactory.create()
    assert grant.used_at is None
    assert grant.expires_at > timezone.now()


# Security re-check findings (PR 234)


def _owner_group() -> Any:
    from django.contrib.auth.models import Group

    from apps.core.models import PLATFORM_OWNER_CODENAME

    group = Group.objects.create(name="Owners")
    group.permissions.add(
        Permission.objects.get(content_type__app_label="core", codename=PLATFORM_OWNER_CODENAME)
    )
    return group


def test_joining_a_group_that_holds_platform_owner_needs_the_group_3_passkey(
    client: Client,
) -> None:
    root = UserFactory.create(superuser=True)
    verified_login(client, root)  # operational, TOTP only
    target = UserFactory.create(staff=True)
    group = _owner_group()
    response = client.post(
        reverse("admin:accounts_user_change", args=[target.pk]), {"groups": [group.pk]}
    )
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("mfa:verify"))
    assert "factor=passkey" in response["Location"]
    assert not target.groups.exists()


def test_joining_an_ordinary_group_needs_no_group_3_passkey(client: Client) -> None:
    from django.contrib.auth.models import Group

    root = UserFactory.create(superuser=True)
    verified_login(client, root)
    target = UserFactory.create(staff=True)
    group = Group.objects.create(name="Support")
    response = client.post(
        reverse("admin:accounts_user_change", args=[target.pk]), {"groups": [group.pk]}
    )
    assert response.status_code != 403


def test_adding_a_user_straight_into_an_owner_group_needs_the_group_3_passkey(
    client: Client,
) -> None:
    root = UserFactory.create(superuser=True)
    verified_login(client, root)
    group = _owner_group()
    response = client.post(reverse("admin:accounts_user_add"), {"groups": [group.pk]})
    assert response.status_code == 302
    assert "factor=passkey" in response["Location"]


def test_a_malformed_client_digest_is_a_400_not_a_500(client: Client) -> None:
    user, _ = _staff_with_passkey()
    _restricted_session(client, user)
    for digest in ("x" * 65, "g" * 64, "A" * 64, "a" * 63):
        response = _post(client, "levels:once_options", {"action": "export", "digest": digest})
        assert response.status_code == 400, digest
    ok = _post(client, "levels:once_options", {"action": "export", "digest": "a" * 64})
    assert ok.status_code == 200


def test_bulk_user_delete_is_a_group_2_action(client: Client) -> None:
    root = UserFactory.create(superuser=True)
    verified_login(client, root, level=levels.RESTRICTED)
    victim = UserFactory.create()
    response = client.post(
        reverse("admin:accounts_user_changelist"),
        {"action": "delete_selected", "_selected_action": [victim.pk], "post": "yes"},
    )
    assert response.status_code == 403
    assert User.objects.filter(pk=victim.pk).exists()


def test_changing_the_password_ends_operational_mode(client: Client) -> None:
    old = secrets.token_urlsafe(16)
    user = UserFactory.create(staff=True, password=old)
    verified_login(client, user)
    assert client.session.get(levels.STARTED_KEY) is not None
    new = secrets.token_urlsafe(16)
    response = client.post(
        reverse("admin:password_change"),
        {"old_password": old, "new_password1": new, "new_password2": new},
    )
    assert response.status_code == 302
    assert client.session.get(levels.STARTED_KEY) is None
    assert _after(_actions(levels.SESSION_ENDED)[-1])["reason"] == "password"


@pytest.mark.django_db(transaction=True)
def test_minting_the_same_grant_in_parallel_leaves_one_usable_grant() -> None:
    import threading

    from django.db import connection

    from apps.accounts.models import ElevationGrant

    user = UserFactory.create(staff=True)
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def mint() -> None:
        try:
            barrier.wait()
            levels.create_grant("session-key", user, "export", "digest-1", ttl=120)
        except BaseException as exc:  # the test reports it
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=mint) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    assert ElevationGrant.objects.filter(used_at__isnull=True).count() == 1
    assert levels.spend_grant("session-key", user, "export", "digest-1")
    assert not levels.spend_grant("session-key", user, "export", "digest-1")


def test_an_expired_unspent_grant_does_not_block_a_new_one() -> None:
    user = UserFactory.create(staff=True)
    levels.create_grant("s", user, "export", "d", ttl=-5)
    levels.create_grant("s", user, "export", "d", ttl=120)
    assert levels.spend_grant("s", user, "export", "d")
