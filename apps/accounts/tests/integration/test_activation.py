"""CORE.19: on-demand activation of migrated customer accounts (D-16 amended, L-11).

- The one informative email has no link and no promotional content (EN, IT with "tu").
- No activation link exists before the customer asks for it on the site.
- The request answers the same whatever the address; it is rate limited per address and IP.
- The link is single use, time limited and dies when the password is set.
"""

import json
import re
import secrets
import time
from typing import Any

import pytest
from django.contrib.auth.hashers import is_password_usable
from django.test import Client
from django.urls import reverse

from apps.accounts import activation
from apps.accounts.models import ActionLinkUse, User
from apps.accounts.services import issue_action_token
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.core.services import render_email

pytestmark = pytest.mark.django_db

SYSTEM = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
URL = "/account/activate/"
IP = "203.0.113.55"


def migrated(**extra: Any) -> User:
    user = UserFactory.create(**extra)
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


def link_emails() -> list[OutboxEmail]:
    return list(OutboxEmail.objects.filter(template_key=activation.LINK_TEMPLATE))


def link_path(email: OutboxEmail) -> str:
    url = str(json.loads(email.context)["action_url"])
    return "/" + url.split("/", 3)[3]


def ask(client: Client, address: str, ip: str = IP) -> Any:
    return client.post(URL, {"email": address}, REMOTE_ADDR=ip)


def open_link(client: Client, path: str) -> Any:
    client.get(path)
    token = path.rstrip("/").rsplit("/", 1)[1]
    return client.post(reverse("action_link:continue"), {"token": token})


def activate_page(user: User) -> str:
    return reverse("activation:set_password", args=[user.public_id])


# --- The informative email: no link, nothing promotional


@pytest.mark.parametrize("language", ["en", "it", "de", "fr", "es", "xx"])
def test_the_notice_has_no_link_and_no_promotion(language: str) -> None:
    rendered = render_email(activation.NOTICE_TEMPLATE, language, {})
    for body in (rendered.text_body, rendered.html_body):
        assert not re.search(r"https?://|www\.|href=", body, re.IGNORECASE)
    text = (rendered.subject + rendered.text_body).lower()
    for promo in ("discount", "sconto", "% off", "offerta", "newsletter", "unsubscribe"):
        assert promo not in text.replace("no offers or promotions", "")


def test_the_notice_is_in_italian_with_informal_tu() -> None:
    rendered = render_email(activation.NOTICE_TEMPLATE, "it", {})
    assert "nuovo sito" in rendered.text_body
    assert "Lei" not in rendered.text_body
    assert "hai" in rendered.text_body.lower() or "tuo" in rendered.text_body.lower()


def test_the_notice_is_queued_once_per_account_without_a_link() -> None:
    user = migrated()
    assert not activation.send_activation_notice(user, ctx=SYSTEM).is_empty
    assert activation.send_activation_notice(user, ctx=SYSTEM).is_empty  # never twice
    [email] = OutboxEmail.objects.filter(template_key=activation.NOTICE_TEMPLATE)
    assert email.recipient == user.email
    assert "action_url" not in json.loads(email.context)
    assert AuditLog.objects.filter(action=activation.NOTICE_SENT).count() == 1


def test_the_notice_goes_only_to_accounts_awaiting_activation() -> None:
    with_password = UserFactory.create()
    staff = migrated(staff=True)
    inactive = migrated(is_active=False)
    for user in (with_password, staff, inactive):
        assert activation.send_activation_notice(user, ctx=SYSTEM).is_empty
    assert not OutboxEmail.objects.exists()


def test_a_notice_dry_run_queues_nothing() -> None:
    user = migrated()
    changes = activation.send_activation_notice(user, ctx=SYSTEM, dry_run=True)
    assert not changes.is_empty
    assert not OutboxEmail.objects.exists()


# --- The link exists only on demand


def test_no_link_exists_before_the_request() -> None:
    user = migrated()
    activation.send_activation_notice(user, ctx=SYSTEM)
    assert link_emails() == []
    assert not ActionLinkUse.objects.exists()


def test_a_request_for_a_migrated_account_sends_a_link_to_its_address(client: Client) -> None:
    user = migrated(email="holder@example.com", preferred_language="it")
    response = ask(client, "  Holder@Example.COM ")
    assert response.status_code == 302
    [email] = link_emails()
    assert email.recipient == user.email
    assert email.language == "it"
    assert "/account/link/" in link_path(email)


def test_the_answer_is_the_same_for_known_unknown_and_other_accounts(client: Client) -> None:
    known = migrated()
    active = UserFactory.create()
    staff = migrated(staff=True)
    answers = {
        (r.status_code, r["Location"])
        for r in (
            ask(client, known.email),
            ask(client, f"nobody-{secrets.token_hex(4)}@example.com"),
            ask(client, active.email),
            ask(client, staff.email),
        )
    }
    assert answers == {(302, reverse("activation:sent"))}
    assert [e.recipient for e in link_emails()] == [known.email]  # only the migrated account


def test_the_form_and_sent_pages_never_name_the_account(client: Client) -> None:
    user = migrated()
    ask(client, user.email)
    page = client.get(reverse("activation:sent")).content.decode()
    assert user.email not in page
    assert "If this address" in page


def test_a_fourth_request_within_the_hour_sends_nothing_and_looks_the_same(
    client: Client,
) -> None:
    user = migrated()
    responses = [ask(client, user.email, ip=f"203.0.113.{n}") for n in range(10, 14)]
    assert {(r.status_code, r["Location"]) for r in responses} == {
        (302, reverse("activation:sent"))
    }
    assert len(link_emails()) == 3
    assert AuditLog.objects.filter(action=activation.REQUEST_LIMITED).count() == 1


def test_the_request_is_rate_limited_per_ip(client: Client) -> None:
    from apps.accounts.services import REGISTRATION_LIMIT

    seen = [
        ask(client, f"u{n}-{secrets.token_hex(3)}@example.com").status_code
        for n in range(REGISTRATION_LIMIT.free_attempts + 2)
    ]
    assert 429 in seen


def test_the_request_form_asks_only_for_an_email(client: Client) -> None:
    html = client.get(URL).content.decode()
    names = set(re.findall(r'<input[^>]*name="([^"]+)"', html[html.index("<main") :]))
    assert names - {"csrfmiddlewaretoken", "cf-turnstile-response"} == {"email"}


# --- Opening and using the link


def start(client: Client) -> tuple[User, str]:
    user = migrated()
    ask(client, user.email)
    [email] = link_emails()
    return user, link_path(email)


def test_the_link_signs_nobody_in_and_leads_to_the_password_page(client: Client) -> None:
    user, path = start(client)
    response = open_link(client, path)
    assert response.status_code == 302
    assert response["Location"] == activate_page(user)
    assert "_auth_user_id" not in client.session


def test_choosing_a_password_activates_the_account_once(client: Client) -> None:
    user, path = start(client)
    open_link(client, path)
    password = secrets.token_urlsafe(18)
    done = client.post(activate_page(user), {"password": password})
    assert done.status_code == 200
    user.refresh_from_db()
    assert is_password_usable(user.password)
    assert user.check_password(password)
    assert AuditLog.objects.filter(action=activation.ACTIVATED).count() == 1
    assert ActionLinkUse.objects.count() == 1
    # The same link cannot be used again, in this session or a new one.
    assert client.post(activate_page(user), {"password": password}).status_code == 403
    other = Client()
    open_link(other, path)  # refused: the single use is spent
    assert (
        other.post(activate_page(user), {"password": secrets.token_urlsafe(18)}).status_code == 403
    )
    user.refresh_from_db()
    assert user.check_password(password)  # unchanged


def test_a_weak_password_is_refused_and_the_link_stays_usable(client: Client) -> None:
    user, path = start(client)
    open_link(client, path)
    refused = client.post(activate_page(user), {"password": "123"})
    assert refused.status_code == 200
    user.refresh_from_db()
    assert not user.has_usable_password()
    assert not ActionLinkUse.objects.exists()
    assert (
        client.post(activate_page(user), {"password": secrets.token_urlsafe(18)}).status_code == 200
    )
    user.refresh_from_db()
    assert user.has_usable_password()


def test_an_expired_link_is_refused() -> None:
    user = migrated()
    token = issue_action_token(
        purpose=activation.PURPOSE,
        object_key=str(user.public_id),
        version=activation._version_of(str(user.public_id)),
        expires_in=60,
        now=time.time() - 3600,
    )
    client = Client()
    response = client.post(reverse("action_link:continue"), {"token": token})
    assert response.status_code == 302
    assert client.get(activate_page(user)).status_code == 403
    assert (
        client.post(activate_page(user), {"password": secrets.token_urlsafe(18)}).status_code == 403
    )
    user.refresh_from_db()
    assert not user.has_usable_password()


def test_a_link_dies_when_the_password_is_set_another_way(client: Client) -> None:
    user, path = start(client)
    open_link(client, path)
    user.set_password(secrets.token_urlsafe(18))
    user.save(update_fields=["password"])
    response = client.post(activate_page(user), {"password": secrets.token_urlsafe(18)})
    assert response.status_code == 403


def test_the_link_is_bound_to_the_address_of_record(client: Client) -> None:
    user, path = start(client)
    user.email = f"moved-{secrets.token_hex(3)}@example.com"
    user.save(update_fields=["email"])
    open_link(client, path)
    assert client.get(activate_page(user)).status_code == 403


def test_the_set_password_page_gives_nothing_away_for_a_random_key(client: Client) -> None:
    for key in (secrets.token_urlsafe(8), "00000000-0000-0000-0000-000000000000"):
        response = client.get(reverse("activation:set_password", args=[key]))
        assert response.status_code == 403
