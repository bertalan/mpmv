"""CORE.15: secure action links (spec 02, "Secure action links").

A link carries a signed token bound to an action object, its recipient and the object version.
A GET or HEAD only shows a "Continue" page; the Continue POST signs the recipient in once
(without a password) and leads to the action page, whose approve POST needs an MFA step-up.
"""

import json
import time
from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.accounts import services
from apps.accounts.models import ActionLinkUse, User
from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.integration.urls import APPROVE_PURPOSE, APPROVED, DOCUMENTS, Document
from apps.accounts.tests.mfa import totp_token, verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.accounts.tests.integration.urls")]

LOGIN_PATH = "/django-admin/login/"
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


def open_link(client: Client, token: str, method: str = "get") -> object:
    return getattr(client, method)(services.action_link_path(token))


def press_continue(client: Client, token: str) -> object:
    return client.post(reverse("action_link:continue"), {"token": token})


def signed_in_user(client: Client) -> int | None:
    value = client.session.get("_auth_user_id")
    return int(value) if value is not None else None


def assert_sent_to_login(response: object, next_path: str) -> None:
    assert response.status_code == 302  # type: ignore[attr-defined]
    parts = urlsplit(response["Location"])  # type: ignore[index]
    assert parts.path == LOGIN_PATH
    assert parse_qs(parts.query)["next"] == [next_path]


def refusals() -> list[AuditLog]:
    return list(AuditLog.objects.filter(action=services.ACTION_LINK_REFUSED))


# --- GET and HEAD never consume the token nor change state


@pytest.mark.parametrize("method", ["get", "head"])
def test_get_and_head_show_the_continue_page_without_changing_state(
    client: Client, method: str
) -> None:
    user = UserFactory.create()
    token = token_for(user, owned_document(user))

    response = open_link(client, token, method)

    assert response.status_code == 200  # type: ignore[attr-defined]
    assert response["Referrer-Policy"] == "no-referrer"  # type: ignore[index]
    assert "no-store" in response["Cache-Control"]  # type: ignore[index]
    assert signed_in_user(client) is None
    assert not ActionLinkUse.objects.exists()
    assert not AuditLog.objects.exists()


def test_the_continue_page_posts_the_token_to_a_url_without_it(client: Client) -> None:
    user = UserFactory.create()
    token = token_for(user, owned_document(user))
    html = open_link(client, token).content.decode()  # type: ignore[attr-defined]
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
    token = token_for(user, owned_document(user))
    Client().get(services.action_link_path(token))  # a mail scanner
    Client().head(services.action_link_path(token))
    response = press_continue(client, token)
    assert response["Location"] == reverse("test-approve", args=["doc-1"])
    assert signed_in_user(client) == user.pk


def test_a_signed_in_recipient_lands_directly_on_the_action_page(client: Client) -> None:
    user = UserFactory.create()
    verified_login(client, user)
    token = token_for(user, owned_document(user))
    response = open_link(client, token)
    assert response.status_code == 302  # type: ignore[attr-defined]
    assert response["Location"] == reverse("test-approve", args=["doc-1"])  # type: ignore[index]
    assert not ActionLinkUse.objects.exists()


# --- Continue POST: magic sign-in, then MFA step-up before the action


def test_a_valid_token_without_session_leads_to_the_step_up_and_then_to_the_page(
    client: Client,
) -> None:
    user = UserFactory.create()
    device = TOTPDeviceFactory.create(user=user)
    token = token_for(user, owned_document(user))
    action_url = reverse("test-approve", args=["doc-1"])

    response = press_continue(client, token)
    assert response.status_code == 302
    assert response["Location"] == action_url
    assert response["Referrer-Policy"] == "no-referrer"
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
    assert client.post(action_url).content == b"approved doc-1"
    assert [("doc-1", user.pk)] == APPROVED


def test_a_user_without_a_second_factor_must_enrol_before_the_page(client: Client) -> None:
    user = UserFactory.create()
    press_continue(client, token_for(user, owned_document(user)))
    page = client.get(reverse("test-approve", args=["doc-1"]))
    assert urlsplit(page["Location"]).path == reverse("mfa:setup")


def test_an_action_without_step_up_is_refused(client: Client) -> None:
    user = UserFactory.create()
    TOTPDeviceFactory.create(user=user)
    press_continue(client, token_for(user, owned_document(user)))

    response = client.post(reverse("test-approve", args=["doc-1"]))

    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == reverse("mfa:verify")
    assert APPROVED == []


def test_an_old_step_up_is_not_enough_for_the_action(client: Client) -> None:
    user = UserFactory.create()
    verified_login(client, user, verified_at=time.time() - 3600)
    owned_document(user)
    response = client.post(reverse("test-approve", args=["doc-1"]))
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


# --- Single use and misuse: normal login, then the same action page, with audit


def test_the_sign_in_use_is_single(client: Client) -> None:
    user = UserFactory.create()
    token = token_for(user, owned_document(user))
    press_continue(Client(), token)

    response = press_continue(client, token)

    assert_sent_to_login(response, reverse("test-approve", args=["doc-1"]))
    assert signed_in_user(client) is None
    [entry] = refusals()
    assert entry.after["reason"] == "used"
    assert entry.target_key == str(user.public_id)


def test_the_used_token_record_is_unique() -> None:
    user = UserFactory.create()
    ActionLinkUse.objects.create(nonce_digest="a" * 64, purpose="p", object_key="o", user=user)
    with pytest.raises(IntegrityError), transaction.atomic():
        ActionLinkUse.objects.create(nonce_digest="a" * 64, purpose="p", object_key="o", user=user)


def test_a_token_for_user_x_cannot_open_user_y_object(client: Client) -> None:
    x, y = UserFactory.create(), UserFactory.create()
    owned_document(y, "doc-y")
    token = token_for(x, "doc-y")  # bound to X, but the object belongs to Y

    response = press_continue(client, token)

    assert_sent_to_login(response, reverse("test-approve", args=["doc-y"]))
    assert signed_in_user(client) is None
    assert refusals()[0].after["reason"] == "wrong_recipient"


def test_a_token_for_user_x_is_refused_in_user_y_session(client: Client) -> None:
    x, y = UserFactory.create(), UserFactory.create()
    token = token_for(x, owned_document(x))
    verified_login(client, y)

    get = open_link(client, token)
    assert get.status_code == 200  # type: ignore[attr-defined]  # no shortcut for Y
    response = press_continue(client, token)

    assert_sent_to_login(response, reverse("test-approve", args=["doc-1"]))
    assert signed_in_user(client) == y.pk  # never switched to X
    assert refusals()[0].after["reason"] == "wrong_recipient"
    assert not ActionLinkUse.objects.exists()


def test_an_old_version_token_is_refused(client: Client) -> None:
    user = UserFactory.create()
    token = token_for(user, owned_document(user))
    DOCUMENTS["doc-1"].version = "2"  # the proposal changed after the email

    response = press_continue(client, token)

    assert_sent_to_login(response, reverse("test-approve", args=["doc-1"]))
    assert signed_in_user(client) is None
    assert refusals()[0].after["reason"] == "old_version"


def test_an_old_version_token_gives_no_shortcut_to_a_signed_in_user(client: Client) -> None:
    user = UserFactory.create()
    token = token_for(user, owned_document(user))
    DOCUMENTS["doc-1"].version = "2"
    verified_login(client, user)
    assert open_link(client, token).status_code == 200  # type: ignore[attr-defined]


def test_an_expired_token_is_refused(client: Client) -> None:
    user = UserFactory.create()
    owned_document(user)
    token = token_for(user, now=time.time() - 72 * 3600 - 60)

    response = press_continue(client, token)

    assert_sent_to_login(response, reverse("test-approve", args=["doc-1"]))
    assert refusals()[0].after["reason"] == "expired"


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
    [entry] = refusals()
    assert entry.after["reason"] == "bad_signature"
    assert entry.target_key == services.UNKNOWN_ACCOUNT


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
    assert refusals()[0].after["reason"] == "inactive"


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
    assert len(refusals()) == 2


def test_tokens_never_reach_the_audit_log(client: Client) -> None:
    user = UserFactory.create()
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
    assert entry.after == {"purpose": APPROVE_PURPOSE, "object": "doc-1", "version": "1"}
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
    assert changes
    assert not OutboxEmail.objects.exists()
    assert not AuditLog.objects.exists()
