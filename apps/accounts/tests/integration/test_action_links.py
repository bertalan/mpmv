"""CORE.15: secure action links (spec 02, "Secure action links").

A link carries a signed token bound to an action object, its recipient and the object version.
A GET or HEAD only shows a "Continue" page; the Continue POST signs the recipient in once
(without a password) and leads to the action page, whose approve POST needs an MFA step-up.
"""

import json
import logging
import secrets
import threading
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError, connection, transaction
from django.db.models import F
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.accounts import services
from apps.accounts.models import ActionLinkIssue, ActionLinkUse, User
from apps.accounts.tests.factories import (
    ActionLinkIssueFactory,
    ActionLinkUseFactory,
    TOTPDeviceFactory,
    UserFactory,
)
from apps.accounts.tests.integration.urls import APPROVE_PURPOSE, APPROVED, DOCUMENTS, Document
from apps.accounts.tests.mfa import totp_token, verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.accounts.tests.integration.urls")]

LOGIN_PATH = "/account/login/"
SYSTEM = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)


@pytest.fixture(autouse=True)
def _documents() -> Iterator[None]:
    DOCUMENTS.clear()
    APPROVED.clear()
    yield
    DOCUMENTS.clear()
    APPROVED.clear()


def owned_document(user: User, key: str = "doc-1", version: str = "1") -> str:
    DOCUMENTS[key] = Document(owner_id=user.pk, version=version)
    return key


def token_for(user: User, key: str = "doc-1", **kwargs: object) -> str:
    return services.issue_action_token(
        purpose=APPROVE_PURPOSE,
        object_key=key,
        recipient=user,
        version=DOCUMENTS[key].version,
        **kwargs,  # type: ignore[arg-type]
    )


def open_link(client: Client, token: str, method: str = "get") -> Any:
    return getattr(client, method)(services.action_link_path(token))


def press_continue(client: Client, token: str) -> Any:
    return client.post(reverse("action_link:continue"), {"token": token})


def signed_in_user(client: Client) -> int | None:
    value = client.session.get("_auth_user_id")
    return int(value) if value is not None else None


def assert_sent_to_login(response: Any, next_path: str) -> None:
    assert response.status_code == 302
    parts = urlsplit(response["Location"])
    assert parts.path == LOGIN_PATH
    assert parse_qs(parts.query)["next"] == [next_path]


def action_page(key: str = "doc-1", version: str = "1") -> str:
    """Where a usable link leads: the action page, told the version the link was issued for."""
    return f"{reverse('test-approve', args=[key])}?version={version}"


def refusals() -> list[dict[str, Any]]:
    """The refused-link audit entries: their `after` snapshot plus the target key."""
    return [
        {**(entry.after or {}), "target_key": entry.target_key}
        for entry in AuditLog.objects.filter(action=services.ACTION_LINK_REFUSED)
    ]


# --- GET and HEAD never consume the token nor change state


@pytest.mark.parametrize("method", ["get", "head"])
def test_get_and_head_show_the_continue_page_without_changing_state(
    client: Client, method: str
) -> None:
    user = UserFactory.create()
    token = token_for(user, owned_document(user))

    response = open_link(client, token, method)

    assert response.status_code == 200
    assert response["Referrer-Policy"] == "strict-origin"
    assert "no-store" in response["Cache-Control"]
    assert signed_in_user(client) is None
    assert not ActionLinkUse.objects.exists()
    assert not AuditLog.objects.exists()


def test_the_continue_page_posts_the_token_to_a_url_without_it(client: Client) -> None:
    user = UserFactory.create()
    token = token_for(user, owned_document(user))
    html = open_link(client, token).content.decode()
    assert f'action="{reverse("action_link:continue")}"' in html
    assert 'method="post"' in html
    assert "csrfmiddlewaretoken" in html
    assert f'name="token" value="{token}"' in html


def test_an_invalid_token_on_get_changes_nothing_either(client: Client) -> None:
    response = client.get(reverse("action_link:open", args=["not-a-token"]))
    assert response.status_code == 200
    assert not AuditLog.objects.exists()
    assert not ActionLinkUse.objects.exists()


def test_a_prefetch_does_not_spend_the_link(client: Client) -> None:
    user = UserFactory.create()
    TOTPDeviceFactory.create(user=user)
    token = token_for(user, owned_document(user))
    Client().get(services.action_link_path(token))  # a mail scanner
    Client().head(services.action_link_path(token))
    response = press_continue(client, token)
    assert response["Location"] == action_page()
    assert signed_in_user(client) == user.pk


def test_a_signed_in_recipient_lands_directly_on_the_action_page(client: Client) -> None:
    user = UserFactory.create()
    verified_login(client, user)
    token = token_for(user, owned_document(user))
    response = open_link(client, token)
    assert response.status_code == 302
    assert response["Location"] == action_page()
    assert not ActionLinkUse.objects.exists()


# --- Continue POST: magic sign-in, then MFA step-up before the action


def test_a_valid_token_without_session_leads_to_the_step_up_and_then_to_the_page(
    client: Client,
) -> None:
    user = UserFactory.create()
    device = TOTPDeviceFactory.create(user=user)
    token = token_for(user, owned_document(user))
    action_url = action_page()

    response = press_continue(client, token)
    assert response.status_code == 302
    assert response["Location"] == action_url
    assert response["Referrer-Policy"] == "strict-origin"
    assert signed_in_user(client) == user.pk
    assert ActionLinkUse.objects.count() == 1
    entry = AuditLog.objects.get(action=services.ACTION_LINK_SIGNED_IN)
    assert entry.target_key == str(user.public_id)
    assert entry.after == {"purpose": APPROVE_PURPOSE, "object": "doc-1", "version": "1"}

    page = client.get(action_url)
    assert page.status_code == 302
    parts = urlsplit(page["Location"])
    assert parts.path == reverse("mfa:verify")
    assert parse_qs(parts.query)["next"] == [action_url]

    verify = client.post(page["Location"], {"method": "totp", "token": totp_token(device)})
    assert verify["Location"] == action_url
    assert client.get(action_url).status_code == 200
    assert client.post(action_url, {"version": "1"}).content == b"approved doc-1"
    assert [("doc-1", user.pk)] == APPROVED


def test_a_recipient_without_a_second_factor_gets_no_passwordless_sign_in(
    client: Client,
) -> None:
    # Enrolling a first factor needs no other proof (CORE.4), so a magic sign-in here would let
    # whoever holds only the mailbox or the link bind their own authenticator and approve.
    user = UserFactory.create()
    token = token_for(user, owned_document(user))

    response = press_continue(client, token)

    assert_sent_to_login(response, reverse("test-approve", args=["doc-1"]))
    assert signed_in_user(client) is None
    assert not ActionLinkUse.objects.exists()
    assert not AuditLog.objects.filter(action=services.ACTION_LINK_SIGNED_IN).exists()
    [entry] = refusals()
    assert entry["reason"] == "no_second_factor"
    assert entry["target_key"] == str(user.public_id)


def test_an_unconfirmed_factor_does_not_count_for_the_magic_sign_in(client: Client) -> None:
    user = UserFactory.create()
    TOTPDeviceFactory.create(user=user, confirmed=False)
    press_continue(client, token_for(user, owned_document(user)))
    assert signed_in_user(client) is None
    assert refusals()[0]["reason"] == "no_second_factor"


def test_a_signed_in_recipient_without_a_factor_still_goes_on_to_the_page(
    client: Client,
) -> None:
    # Signed in with a password already: the link adds no sign-in, the page asks for enrolment.
    user = UserFactory.create()
    client.force_login(user)
    response = press_continue(client, token_for(user, owned_document(user)))
    assert response["Location"] == action_page()
    assert not refusals()


def test_an_action_without_step_up_is_refused(client: Client) -> None:
    user = UserFactory.create()
    TOTPDeviceFactory.create(user=user)
    press_continue(client, token_for(user, owned_document(user)))

    response = client.post(reverse("test-approve", args=["doc-1"]), {"version": "1"})

    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == reverse("mfa:verify")
    assert APPROVED == []


def test_an_old_step_up_is_not_enough_for_the_action(client: Client) -> None:
    user = UserFactory.create()
    verified_login(client, user, verified_at=time.time() - 3600)
    owned_document(user)
    response = client.post(reverse("test-approve", args=["doc-1"]), {"version": "1"})
    assert urlsplit(response["Location"]).path == reverse("mfa:verify")
    assert APPROVED == []


def test_the_continue_post_needs_csrf() -> None:
    user = UserFactory.create()
    token = token_for(user, owned_document(user))
    client = Client(enforce_csrf_checks=True)
    response = press_continue(client, token)
    assert response.status_code == 403
    assert signed_in_user(client) is None
    assert not ActionLinkUse.objects.exists()


def test_the_continue_url_answers_only_post(client: Client) -> None:
    assert client.get(reverse("action_link:continue")).status_code == 405


@pytest.mark.parametrize("method", ["post", "put", "delete", "options"])
def test_the_token_never_reaches_the_request_log(
    client: Client, caplog: pytest.LogCaptureFixture, method: str
) -> None:
    user = UserFactory.create()
    token = token_for(user, owned_document(user))
    caplog.set_level(logging.DEBUG)

    response = open_link(client, token, method)

    assert response.status_code == 405
    request_records = [r for r in caplog.records if r.name == "django.request"]
    assert request_records, "the 405 is logged"
    assert all("/account/link/[redacted]/" in r.getMessage() for r in request_records)
    assert token not in caplog.text
    assert token.split(":")[-1] not in caplog.text


# --- Single use and misuse: normal login, then the same action page, with audit


def test_the_sign_in_use_is_single(client: Client) -> None:
    user = UserFactory.create()
    TOTPDeviceFactory.create(user=user)
    token = token_for(user, owned_document(user))
    press_continue(Client(), token)

    response = press_continue(client, token)

    assert_sent_to_login(response, reverse("test-approve", args=["doc-1"]))
    assert signed_in_user(client) is None
    [entry] = refusals()
    assert entry["reason"] == "used"
    assert entry["target_key"] == str(user.public_id)


def test_the_used_token_record_is_unique() -> None:
    spent = ActionLinkUseFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        ActionLinkUseFactory.create(nonce_digest=spent.nonce_digest)


def test_a_token_for_user_x_cannot_open_user_y_object(client: Client) -> None:
    x, y = UserFactory.create(), UserFactory.create()
    owned_document(y, "doc-y")
    token = token_for(x, "doc-y")  # bound to X, but the object belongs to Y

    response = press_continue(client, token)

    assert_sent_to_login(response, reverse("test-approve", args=["doc-y"]))
    assert signed_in_user(client) is None
    assert refusals()[0]["reason"] == "wrong_recipient"


def test_a_token_for_user_x_is_refused_in_user_y_session(client: Client) -> None:
    x, y = UserFactory.create(), UserFactory.create()
    token = token_for(x, owned_document(x))
    verified_login(client, y)

    get = open_link(client, token)
    assert get.status_code == 200  # no shortcut for Y
    response = press_continue(client, token)

    assert_sent_to_login(response, reverse("test-approve", args=["doc-1"]))
    assert signed_in_user(client) == y.pk  # never switched to X
    assert refusals()[0]["reason"] == "wrong_recipient"
    assert not ActionLinkUse.objects.exists()


def test_an_old_version_token_is_refused(client: Client) -> None:
    user = UserFactory.create()
    token = token_for(user, owned_document(user))
    DOCUMENTS["doc-1"].version = "2"  # the proposal changed after the email

    response = press_continue(client, token)

    assert_sent_to_login(response, reverse("test-approve", args=["doc-1"]))
    assert signed_in_user(client) is None
    assert refusals()[0]["reason"] == "old_version"


def test_an_old_version_token_gives_no_shortcut_to_a_signed_in_user(client: Client) -> None:
    user = UserFactory.create()
    token = token_for(user, owned_document(user))
    DOCUMENTS["doc-1"].version = "2"
    verified_login(client, user)
    assert open_link(client, token).status_code == 200


def test_an_expired_token_is_refused(client: Client) -> None:
    user = UserFactory.create()
    owned_document(user)
    token = token_for(user, now=time.time() - 72 * 3600 - 60)

    response = press_continue(client, token)

    assert_sent_to_login(response, reverse("test-approve", args=["doc-1"]))
    assert refusals()[0]["reason"] == "expired"


def test_the_expiry_defaults_to_72_hours_and_is_configurable(
    client: Client, settings: SettingsWrapper
) -> None:
    assert settings.ACTION_LINK_MAX_AGE == 72 * 3600
    user = UserFactory.create()
    owned_document(user)
    fresh = token_for(user, now=time.time() - 72 * 3600 + 60)
    assert services.check_action_token(fresh).refusal is None

    settings.ACTION_LINK_MAX_AGE = 3600
    assert services.check_action_token(token_for(user, now=time.time() - 3700)).refusal == "expired"
    short = token_for(user, expires_in=60, now=time.time() - 120)
    assert services.check_action_token(short).refusal == "expired"


def test_a_tampered_token_is_refused_and_sent_to_plain_login(client: Client) -> None:
    user = UserFactory.create()
    token = token_for(user, owned_document(user))
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")

    response = press_continue(client, tampered)

    assert_sent_to_login(response, "/")


def test_a_token_signed_for_another_purpose_salt_is_refused() -> None:
    from django.core import signing

    user = UserFactory.create()
    owned_document(user)
    payload = {"p": APPROVE_PURPOSE, "o": "doc-1", "r": str(user.public_id), "v": "1"}
    forged = signing.dumps({**payload, "n": "x" * 22, "e": int(time.time()) + 60})
    assert services.check_action_token(forged).refusal == "bad_signature"


def test_an_inactive_recipient_is_refused(client: Client) -> None:
    user = UserFactory.create()
    token = token_for(user, owned_document(user))
    User.objects.filter(pk=user.pk).update(is_active=False)

    assert_sent_to_login(press_continue(client, token), reverse("test-approve", args=["doc-1"]))
    assert signed_in_user(client) is None
    assert refusals()[0]["reason"] == "inactive"


def test_unknown_purposes_cannot_be_issued() -> None:
    user = UserFactory.create()
    with pytest.raises(ValueError, match="purpose"):
        services.issue_action_token(
            purpose="tests.unknown", object_key="x", recipient=user, version=1
        )


def test_the_continue_post_is_rate_limited_per_ip(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.ACTION_LINK_RATE_LIMIT = 2
    for _ in range(2):
        press_continue(client, "not-a-token")
    response = press_continue(client, "not-a-token")
    assert response.status_code == 429
    assert int(response["Retry-After"]) > 0


def test_tokens_never_reach_the_audit_log(client: Client) -> None:
    user = UserFactory.create()
    TOTPDeviceFactory.create(user=user)
    token = token_for(user, owned_document(user))
    press_continue(client, token)
    press_continue(Client(), token)
    dumped = json.dumps([[e.before, e.after, e.target_key] for e in AuditLog.objects.all()])
    assert token not in dumped
    assert token.split(":")[-1] not in dumped


# --- The email helper on the ARC.16 outbox


def test_the_email_helper_queues_the_link_through_the_outbox(settings: SettingsWrapper) -> None:
    settings.SITE_BASE_URL = "https://shop.example.com"
    user = UserFactory.create(preferred_language="en")
    owned_document(user)

    services.send_action_link_email(
        purpose=APPROVE_PURPOSE,
        object_key="doc-1",
        recipient=user,
        version="1",
        template_key="tests.reminder",
        context={"item": "the document", "due": "Friday"},
        ctx=SYSTEM,
    )

    email = OutboxEmail.objects.get()
    assert email.recipient == user.email
    context = json.loads(email.context)
    assert context["action_url"].startswith("https://shop.example.com/account/link/")
    token = context["action_url"].removeprefix("https://shop.example.com/account/link/").strip("/")
    assert services.check_action_token(token).refusal is None
    entry = AuditLog.objects.get(action=services.ACTION_LINK_SENT)
    assert entry.after == {
        "purpose": APPROVE_PURPOSE,
        "object": "doc-1",
        "version": "1",
        "generation": "1",
    }
    assert token not in json.dumps(entry.after)


def test_the_email_helper_dry_run_queues_nothing() -> None:
    user = UserFactory.create()
    owned_document(user)
    changes = services.send_action_link_email(
        purpose=APPROVE_PURPOSE,
        object_key="doc-1",
        recipient=user,
        version="1",
        template_key="tests.reminder",
        context={"item": "x", "due": "y"},
        ctx=SYSTEM,
        dry_run=True,
    )
    assert not changes.is_empty
    assert not OutboxEmail.objects.exists()
    assert not AuditLog.objects.exists()


# --- Review follow-ups (#140): staff, revocation, version re-check, audit noise, concurrency


def privileged_user(kind: str) -> User:
    """Each way an account gets platform powers, alone: no other flag is set."""
    if kind == "staff":
        return UserFactory.create(is_staff=True, is_superuser=False)
    if kind == "superuser":
        return UserFactory.create(is_staff=False, is_superuser=True)
    user = UserFactory.create(is_staff=False, is_superuser=False)
    if kind == "group":
        user.groups.add(Group.objects.create(name="Platform role"))
    else:
        user.user_permissions.add(Permission.objects.get(codename="change_user"))
    return user


@pytest.mark.parametrize("kind", ["staff", "superuser", "group", "permission"])
def test_staff_recipients_get_no_passwordless_sign_in(client: Client, kind: str) -> None:
    # Staff sign in with their password and MFA, then continue (lane-a decision on #140).
    # Platform staff are defined by a role group (spec 02, Q-34), not only by `is_staff`.
    user = privileged_user(kind)
    assert user.is_staff == (kind == "staff")
    assert user.is_superuser == (kind == "superuser")
    TOTPDeviceFactory.create(user=user)
    token = token_for(user, owned_document(user))

    response = press_continue(client, token)

    assert_sent_to_login(response, reverse("test-approve", args=["doc-1"]))
    assert signed_in_user(client) is None
    assert not ActionLinkUse.objects.exists()
    [entry] = refusals()
    assert entry["reason"] == "staff_recipient"
    assert entry["target_key"] == str(user.public_id)
    assert services.check_action_token(token).refusal is None  # still usable once signed in


def test_a_signed_in_staff_recipient_goes_on_to_the_page(client: Client) -> None:
    user = UserFactory.create(staff=True)
    verified_login(client, user)
    response = press_continue(client, token_for(user, owned_document(user)))
    assert response["Location"] == action_page()
    assert not refusals()
    assert not ActionLinkUse.objects.exists()


def test_a_reissued_link_revokes_the_older_one(client: Client) -> None:
    user = UserFactory.create()
    TOTPDeviceFactory.create(user=user)
    first = token_for(user, owned_document(user))
    second = token_for(user)

    assert services.check_action_token(first).refusal == "revoked"
    assert services.check_action_token(second).refusal is None
    response = press_continue(client, first)
    assert_sent_to_login(response, reverse("test-approve", args=["doc-1"]))
    assert signed_in_user(client) is None
    assert refusals()[0]["reason"] == "revoked"
    assert press_continue(Client(), second)["Location"] == action_page()


def test_the_issue_record_is_unique_per_object_and_recipient() -> None:
    issue = ActionLinkIssueFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        ActionLinkIssueFactory.create(
            purpose=issue.purpose, object_key=issue.object_key, user=issue.user
        )


def test_a_link_whose_issue_record_moved_on_is_revoked() -> None:
    user = UserFactory.create()
    token = token_for(user, owned_document(user))
    ActionLinkIssue.objects.filter(user=user).update(generation=F("generation") + 1)
    assert services.check_action_token(token).refusal == "revoked"


def test_a_reissue_revokes_only_links_to_the_same_object_and_recipient() -> None:
    x, y = UserFactory.create(), UserFactory.create()
    owned_document(x, "doc-1")
    owned_document(x, "doc-2")
    first = token_for(x, "doc-1")
    token_for(x, "doc-2")
    DOCUMENTS["doc-1"].owner_id = y.pk
    token_for(y, "doc-1")
    DOCUMENTS["doc-1"].owner_id = x.pk
    assert services.check_action_token(first).refusal is None


def test_the_email_helper_reissues_but_its_dry_run_revokes_nothing() -> None:
    user = UserFactory.create()
    owned_document(user)
    outstanding = token_for(user)

    def send(*, dry_run: bool) -> None:
        services.send_action_link_email(
            purpose=APPROVE_PURPOSE,
            object_key="doc-1",
            recipient=user,
            version="1",
            template_key="tests.reminder",
            context={"item": "x", "due": "y"},
            ctx=SYSTEM,
            dry_run=dry_run,
        )

    send(dry_run=True)
    assert services.check_action_token(outstanding).refusal is None
    send(dry_run=False)
    assert services.check_action_token(outstanding).refusal == "revoked"


def send_link(user: User, *, template_key: str = "tests.reminder", key: str | None = None) -> str:
    """Email a link for doc-1 to `user`; return the token of the email queued last."""
    services.send_action_link_email(
        purpose=APPROVE_PURPOSE,
        object_key="doc-1",
        recipient=user,
        version="1",
        template_key=template_key,
        context={"item": "x", "due": "y"},
        ctx=SYSTEM,
        idempotency_key=key,
    )
    url = json.loads(OutboxEmail.objects.order_by("-pk").first().context)["action_url"]  # type: ignore[union-attr]
    return str(url).rstrip("/").rsplit("/", 1)[-1]


def test_a_retry_with_the_same_idempotency_key_keeps_the_delivered_link() -> None:
    user = UserFactory.create()
    owned_document(user)
    delivered = send_link(user, key="reminder-doc-1")

    send_link(user, key="reminder-doc-1")

    assert OutboxEmail.objects.count() == 1
    assert services.check_action_token(delivered).refusal is None
    assert ActionLinkIssue.objects.get(user=user).generation == 1
    assert AuditLog.objects.filter(action=services.ACTION_LINK_SENT).count() == 1


def test_a_failed_enqueue_keeps_the_delivered_link() -> None:
    user = UserFactory.create()
    owned_document(user)
    delivered = send_link(user)

    with pytest.raises(ValueError, match="is incomplete"):
        send_link(user, template_key="tests.missing")

    assert OutboxEmail.objects.count() == 1
    assert services.check_action_token(delivered).refusal is None
    assert ActionLinkIssue.objects.get(user=user).generation == 1


def test_a_new_key_still_revokes_the_delivered_link() -> None:
    user = UserFactory.create()
    owned_document(user)
    delivered = send_link(user, key="reminder-1")
    fresh = send_link(user, key="reminder-2")

    assert services.check_action_token(delivered).refusal == "revoked"
    assert services.check_action_token(fresh).refusal is None


def test_a_password_change_revokes_outstanding_links(client: Client) -> None:
    user = UserFactory.create()
    TOTPDeviceFactory.create(user=user)
    token = token_for(user, owned_document(user))
    user.set_password(secrets.token_urlsafe(16))
    user.save(update_fields=["password"])

    assert services.check_action_token(token).refusal == "revoked"
    assert_sent_to_login(press_continue(client, token), reverse("test-approve", args=["doc-1"]))
    assert signed_in_user(client) is None
    assert services.check_action_token(token_for(user)).refusal is None


def test_the_action_post_rechecks_the_version(client: Client) -> None:
    user = UserFactory.create()
    verified_login(client, user)
    owned_document(user)
    page = client.get(action_page())
    assert page.status_code == 200
    assert 'name="version" value="1"' in page.content.decode()
    DOCUMENTS["doc-1"].version = "2"  # the proposal changed while the page was open

    response = client.post(reverse("test-approve", args=["doc-1"]), {"version": "1"})

    assert response.status_code == 409
    assert APPROVED == []
    assert client.post(reverse("test-approve", args=["doc-1"])).status_code == 409
    assert APPROVED == []


def test_the_action_page_refuses_a_version_the_link_was_not_issued_for(client: Client) -> None:
    user = UserFactory.create()
    verified_login(client, user)
    owned_document(user, version="2")
    assert client.get(action_page(version="1")).status_code == 409


def test_action_version_matches_compares_with_the_current_version() -> None:
    user = UserFactory.create()
    owned_document(user, version="3")
    assert services.action_version_matches(APPROVE_PURPOSE, "doc-1", "3")
    assert not services.action_version_matches(APPROVE_PURPOSE, "doc-1", "2")
    assert not services.action_version_matches(APPROVE_PURPOSE, "doc-1", None)
    assert not services.action_version_matches(APPROVE_PURPOSE, "doc-9", "3")
    assert not services.action_version_matches("tests.unknown", "doc-1", "3")


def test_a_bad_signature_writes_no_audit_row_but_is_logged(
    client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=services.ACTION_LINK_LOGGER)
    user = UserFactory.create()
    token = token_for(user, owned_document(user))
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")

    press_continue(client, tampered)
    press_continue(client, "not-a-token")

    assert not AuditLog.objects.exists()
    records = [r for r in caplog.records if r.name == services.ACTION_LINK_LOGGER]
    assert [getattr(r, "reason", None) for r in records] == ["bad_signature", "bad_signature"]
    assert tampered not in caplog.text


def test_a_link_for_an_unknown_account_writes_no_audit_row(client: Client) -> None:
    user = UserFactory.create()
    token = token_for(user, owned_document(user))
    User.objects.filter(pk=user.pk).delete()
    press_continue(client, token)
    assert not AuditLog.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_continue_posts_sign_in_exactly_once() -> None:
    user = UserFactory.create()
    TOTPDeviceFactory.create(user=user)
    token = token_for(user, owned_document(user))
    barrier = threading.Barrier(2)
    clients = [Client(), Client()]
    locations: list[str] = []

    def continue_once(client: Client) -> None:
        try:
            barrier.wait(timeout=10)
            locations.append(press_continue(client, token)["Location"])
        finally:
            connection.close()

    threads = [threading.Thread(target=continue_once, args=(c,)) for c in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    refused = f"{LOGIN_PATH}?next={reverse('test-approve', args=['doc-1'])}"
    assert sorted(locations) == sorted([action_page(), refused])
    assert [signed_in_user(c) for c in clients].count(user.pk) == 1
    assert ActionLinkUse.objects.count() == 1
    assert AuditLog.objects.filter(action=services.ACTION_LINK_SIGNED_IN).count() == 1
    assert [entry["reason"] for entry in refusals()] == ["used"]
