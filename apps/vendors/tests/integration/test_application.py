"""VEN.22: the public "Sell with us" application (owner decision Q-55).

Done when: a visitor applies from the public page, confirms the email, enrols MFA at first login
and appears as an applicant in the admin; a bot submission without a valid Turnstile token is
refused; the form passes the CMP.9 checks. Security (review of PR #202): the form takes no
password, so nobody can pre-set the secret of an address they do not own; the person who opens
the emailed link chooses it on the confirm page. An application never grants access by itself,
answers never tell whether an address already has an account, a pending marker (not the user's
state) decides what a link may activate, and every step is rate limited and audited.
"""

import datetime as dt
import json
import re
import secrets
from collections.abc import Iterator
from html import escape
from html.parser import HTMLParser
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.accounts.models import ActionLinkUse, User
from apps.accounts.services import REGISTRATION_LIMIT, mfa_required
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.fakes import FakeCaptchaVerifier
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog, OutboxEmail
from apps.core.services import render_email
from apps.vendors import engine, services
from apps.vendors.models import Vendor, VendorApplication, VendorMember
from apps.vendors.tests.factories import VendorApplicationFactory, VendorFactory
from apps.vendors.tests.integration.test_claim import open_link

pytestmark = pytest.mark.django_db

SYSTEM = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
URL = "/vendor/apply/"
IP = "203.0.113.20"
CAPTCHA = {
    "CAPTCHA_ENABLED": True,
    "CAPTCHA_VERIFIER": "apps.accounts.tests.fakes.FakeCaptchaVerifier",
    "TURNSTILE_SITE_KEY": "site-key-for-tests",
}
CONFIRM_TEMPLATE = "vendors.application_confirm"


@pytest.fixture(autouse=True)
def _reset_fake_captcha() -> Iterator[None]:
    FakeCaptchaVerifier.reset()
    yield
    FakeCaptchaVerifier.reset()


def form(**overrides: str) -> dict[str, str]:
    data = {
        "full_name": "Ada Example",
        "email": f"applicant-{secrets.token_hex(4)}@example.com",
        "business_name": "Ada Workshop",
        "country": "IT",
    }
    return {**data, **overrides}


def new_password() -> str:
    return secrets.token_urlsafe(18)


def apply(client: Client, data: dict[str, str] | None = None, ip: str = IP) -> object:
    return client.post(URL, data if data is not None else form(), REMOTE_ADDR=ip)


def emails(template_key: str = CONFIRM_TEMPLATE) -> list[OutboxEmail]:
    return list(OutboxEmail.objects.filter(template_key=template_key).order_by("id"))


def link_path(email: OutboxEmail) -> str:
    url = str(json.loads(email.context)["action_url"])
    return "/" + url.split("/", 3)[3]


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {name: value or "" for name, value in attrs}))


def inputs_of(html: str) -> dict[str, dict[str, str]]:
    parser = _Tags()
    parser.feed(html[html.index("<main") : html.index("</main>")])  # not the header's forms
    return {a["name"]: a for t, a in parser.tags if t in ("input", "select") and a.get("name")}


# --- The form (CMP.9, data minimisation)


def test_the_form_asks_only_for_what_the_application_needs(client: Client) -> None:
    response = client.get(URL)
    assert response.status_code == 200
    fields = inputs_of(response.content.decode())
    assert set(fields) - {"csrfmiddlewaretoken"} == {
        "full_name",
        "email",
        "business_name",
        "country",
    }  # no password: the person who opens the emailed link chooses it


def test_the_form_is_accessible_without_a_puzzle(client: Client) -> None:
    html = client.get(URL).content.decode()
    fields = inputs_of(html)
    assert fields["email"]["autocomplete"] == "email"
    assert fields["full_name"]["autocomplete"] == "name"
    assert fields["country"]["autocomplete"] == "country"
    assert all("onpaste" not in attrs for attrs in fields.values())
    for name in ("full_name", "email", "business_name", "country"):
        assert f'for="id_{name}"' in html  # a visible label for every field


@override_settings(**CAPTCHA)
def test_the_form_renders_the_turnstile_widget_when_enabled(client: Client) -> None:
    html = client.get(URL).content.decode()
    assert "cf-turnstile" in html
    assert "challenges.cloudflare.com/turnstile" in html


def test_the_form_and_the_answers_are_not_cached_or_indexed(client: Client) -> None:
    response = client.get(URL)
    assert "no-store" in response["Cache-Control"]
    assert "noindex" in response["X-Robots-Tag"]


# --- Applying


def test_an_application_creates_an_inactive_account_with_no_usable_password(
    client: Client,
) -> None:
    data = form()
    response = apply(client, data)

    assert response.status_code == 302  # type: ignore[attr-defined]
    assert response["Location"] == reverse("vendors:apply_sent")  # type: ignore[index]
    user = User.objects.get(email=data["email"])
    assert user.is_active is False
    assert user.has_usable_password() is False  # B1: nobody chose a secret yet
    assert user.full_name == "Ada Example"
    vendor = Vendor.objects.get(contact_email=data["email"])
    assert vendor.status == engine.APPLICANT
    assert vendor.legal_name == vendor.display_name == "Ada Workshop"
    member = VendorMember.objects.get(vendor=vendor)
    assert (member.user, member.role) == (user, engine.OWNER)
    assert VendorApplication.objects.get(user=user).version == 1


def test_an_application_never_signs_anyone_in_or_grants_access(client: Client) -> None:
    data = form()
    apply(client, {**data, "password": "Attacker-chosen-1!"})  # an extra field is ignored
    assert "_auth_user_id" not in client.session
    assert client.login(username=data["email"], password="Attacker-chosen-1!") is False
    assert client.get("/manager/").status_code in (302, 403, 404)


def test_an_application_collects_nothing_beyond_the_form(client: Client) -> None:
    data = form()
    apply(client, data)
    vendor = Vendor.objects.get(contact_email=data["email"])
    assert (vendor.phone, vendor.vat_number, vendor.tax_code, vendor.pec) == ("", "", "", "")
    assert vendor.registered_street == ""


def test_the_confirmation_email_goes_through_the_outbox_without_tracking_or_user_text(
    client: Client,
) -> None:
    data = form(business_name="Phishing Workshop https://evil.example/login")
    apply(client, data)
    [email] = emails()
    assert email.recipient == data["email"]
    assert email.status == OutboxEmail.Status.QUEUED
    assert "/account/link/" in json.loads(email.context)["action_url"]
    rendered = render_email(email.template_key, email.language, email.context_data)
    assert "<img" not in rendered.html_body.lower()
    for text in (rendered.html_body, rendered.text_body, rendered.subject, email.context):
        assert "Phishing" not in text  # m3: nothing the applicant typed reaches the mail
        assert "evil.example" not in text


def test_the_application_is_audited_without_secrets(client: Client) -> None:
    data = form()
    apply(client, data, ip=IP)
    entries = AuditLog.objects.filter(action=services.APPLICATION_SUBMITTED)
    assert entries.count() == 2  # the vendor and its owner membership
    for entry in entries:
        assert entry.ip == IP
        assert "password" not in json.dumps([entry.before, entry.after]).lower()


def test_the_service_supports_dry_run() -> None:
    data = form()
    changes = services.apply_to_sell(
        full_name=data["full_name"],
        email=data["email"],
        business_name=data["business_name"],
        country=data["country"],
        language="en",
        ctx=SYSTEM,
        dry_run=True,
    )
    assert not changes.is_empty
    assert not User.objects.filter(email=data["email"]).exists()
    assert not Vendor.objects.filter(contact_email=data["email"]).exists()
    assert not VendorApplication.objects.exists()
    assert not OutboxEmail.objects.exists()


def test_vendor_validation_fails_the_same_way_for_new_and_existing_addresses() -> None:
    existing = UserFactory.create()
    for address in (existing.email, "brand-new@example.com"):
        with pytest.raises(ValidationError):
            services.apply_to_sell(
                full_name="Ada",
                email=address,
                business_name="Ada Workshop",
                country="US",  # outside the allowed countries
                language="en",
                ctx=SYSTEM,
            )
    assert not OutboxEmail.objects.exists()


# --- No enumeration, no takeover


def test_an_existing_account_gets_the_same_answer_and_is_left_untouched(
    client: Client,
) -> None:
    existing = UserFactory.create(password=new_password())
    before = (existing.password, existing.is_active, existing.full_name)

    response = apply(client, form(email=existing.email))
    fresh = apply(Client(), form())

    assert (response.status_code, response["Location"]) == (  # type: ignore[attr-defined,index]
        fresh.status_code,  # type: ignore[attr-defined]
        fresh["Location"],  # type: ignore[index]
    )
    existing.refresh_from_db()
    assert (existing.password, existing.is_active, existing.full_name) == before
    assert not Vendor.objects.filter(contact_email=existing.email).exists()
    assert not VendorMember.objects.filter(user=existing).exists()
    assert not VendorApplication.objects.filter(user=existing).exists()
    assert User.objects.filter(email=existing.email).count() == 1


def test_an_existing_account_is_told_by_email_with_no_link(client: Client) -> None:
    existing = UserFactory.create()
    apply(client, form(email=existing.email))
    [email] = emails("vendors.application_exists")
    assert email.recipient == existing.email
    assert "action_url" not in json.loads(email.context)
    assert not emails()


def test_the_email_case_does_not_reveal_an_account(client: Client) -> None:
    existing = UserFactory.create(email="Mixed.Case@example.com")
    apply(client, form(email="mixed.case@EXAMPLE.com"))
    assert User.objects.filter(email__iexact=existing.email).count() == 1
    assert not Vendor.objects.exists()


def test_an_inactive_account_without_a_pending_marker_is_never_reactivated(
    client: Client,
) -> None:
    """M1: a staff-blocked or migrated applicant owner looks like a pending one, but only the
    marker written by the public application makes an account eligible for a link."""
    blocked = UserFactory.create(is_active=False)
    vendor = VendorFactory.create(contact_email=blocked.email)
    VendorMember.objects.create(vendor=vendor, user=blocked, role=engine.OWNER)
    apply(client, form(email=blocked.email))
    blocked.refresh_from_db()
    assert blocked.is_active is False
    assert not emails()
    assert [e.template_key for e in OutboxEmail.objects.all()] == ["vendors.application_exists"]


def test_a_closed_application_is_not_pending_any_more(client: Client) -> None:
    data = form()
    apply(client, data)
    user = User.objects.get(email=data["email"])
    vendor = VendorMember.objects.get(user=user).vendor
    services.change_status(vendor, engine.CLOSED, ctx=SYSTEM)
    apply(Client(), data, ip="198.51.100.9")
    assert len(emails()) == 1  # no new link for a rejected application
    user.refresh_from_db()
    assert user.is_active is False


def test_applying_again_before_confirming_rotates_the_link_and_changes_nothing_else(
    client: Client,
) -> None:
    data = form()
    apply(client, data)
    user = User.objects.get(email=data["email"])
    first_link = link_path(emails()[0])

    response = apply(
        Client(), form(email=data["email"], business_name="Other Shop"), ip="198.51.100.9"
    )

    assert response.status_code == 302  # type: ignore[attr-defined]
    user_again = User.objects.get(email=data["email"])
    assert user_again.pk == user.pk
    assert user_again.full_name == "Ada Example"
    assert Vendor.objects.filter(contact_email=data["email"]).count() == 1
    assert Vendor.objects.get(contact_email=data["email"]).display_name == "Ada Workshop"
    assert len(emails()) == 2
    assert VendorApplication.objects.get(user=user).version == 2
    old = Client()
    open_link(old, first_link)
    assert old.post(confirm_url(user), REMOTE_ADDR=IP).status_code == 403  # the old link is dead
    fresh = Client()
    open_link(fresh, link_path(emails()[1]))
    assert fresh.get(confirm_url(user)).status_code == 200


def test_resends_to_one_address_are_limited_per_day_and_keep_the_delivered_link(
    client: Client,
) -> None:
    data = form()
    for index in range(4):
        apply(Client(), data, ip=f"198.51.100.{10 + index}")
    sent = emails()
    assert len(sent) == 2  # the first email and one resend per address and day
    user = User.objects.get(email=data["email"])
    assert VendorApplication.objects.get(user=user).version == 2
    delivered = Client()
    open_link(delivered, link_path(sent[1]))  # a suppressed resend must not kill this link
    assert delivered.get(confirm_url(user)).status_code == 200


def test_a_pending_user_with_two_memberships_does_not_cause_a_server_error(
    client: Client,
) -> None:
    data = form()
    apply(client, data)
    user = User.objects.get(email=data["email"])
    VendorMember.objects.create(
        vendor=VendorFactory.create(contact_email="other@example.com"),
        user=user,
        role=engine.OWNER,
    )
    assert apply(Client(), data, ip="198.51.100.9").status_code == 302  # type: ignore[attr-defined]
    opened = Client()
    open_link(opened, link_path(emails()[-1]))
    response = opened.post(confirm_url(user), {"password": new_password()}, REMOTE_ADDR=IP)
    assert response.status_code == 200


# --- Confirming the email and choosing the password


def confirm_url(user: User) -> str:
    return reverse("vendors:apply_confirm", args=[user.public_id])


def applied(client: Client) -> tuple[dict[str, str], User, str]:
    data = form()
    apply(client, data)
    user = User.objects.get(email=data["email"])
    return data, user, link_path(emails()[-1])


def test_opening_the_link_changes_nothing_until_the_password_is_set(client: Client) -> None:
    _, user, path = applied(client)
    response = open_link(client, path)
    assert response["Location"] == confirm_url(user)
    page = client.get(confirm_url(user))
    assert page.status_code == 200
    user.refresh_from_db()
    assert user.is_active is False
    assert not ActionLinkUse.objects.exists()


def test_the_confirm_page_offers_one_accessible_password_field(client: Client) -> None:
    _, user, path = applied(client)
    open_link(client, path)
    html = client.get(confirm_url(user)).content.decode()
    fields = inputs_of(html)
    assert set(fields) - {"csrfmiddlewaretoken"} == {"password"}
    assert fields["password"]["type"] == "password"
    assert fields["password"]["autocomplete"] == "new-password"
    assert fields["password"].get("data-password-toggle")
    assert 'for="id_password"' in html
    assert "accounts/password-toggle.js" in html


def test_confirming_sets_the_chosen_password_and_activates_the_account(client: Client) -> None:
    data, user, path = applied(client)
    open_link(client, path)
    password = new_password()
    response = client.post(confirm_url(user), {"password": password}, REMOTE_ADDR=IP)

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.is_active is True
    assert user.has_usable_password()
    assert "_auth_user_id" not in client.session  # confirming never signs in
    assert Client().login(username=data["email"], password=new_password()) is False
    assert client.login(username=data["email"], password=password) is True
    user.refresh_from_db()
    assert mfa_required(user)  # CORE.4: an owner enrols a second factor at first login
    vendor = Vendor.objects.get(contact_email=data["email"])
    assert vendor.status == engine.APPLICANT  # staff review comes next (VEN.5)
    assert not VendorApplication.objects.filter(user=user).exists()  # no longer pending
    assert ActionLinkUse.objects.filter(user=user).count() == 1
    [entry] = AuditLog.objects.filter(action=services.APPLICATION_CONFIRMED)
    assert entry.before is not None
    assert entry.before["is_active"] is False
    assert entry.after is not None
    assert entry.after["is_active"] is True
    assert password not in json.dumps([entry.before, entry.after])


@pytest.mark.parametrize("weak", ["12345678", "short", "p" * 129, ""])
def test_a_refused_password_keeps_the_account_inactive_and_the_link_usable(
    client: Client, weak: str
) -> None:
    _, user, path = applied(client)
    open_link(client, path)
    response = client.post(confirm_url(user), {"password": weak}, REMOTE_ADDR=IP)
    assert response.status_code == 200
    assert b"error-summary" in response.content
    user.refresh_from_db()
    assert user.is_active is False
    assert not ActionLinkUse.objects.exists()
    good = client.post(confirm_url(user), {"password": new_password()}, REMOTE_ADDR=IP)
    assert good.status_code == 200
    user.refresh_from_db()
    assert user.is_active is True


def test_the_link_works_once(client: Client) -> None:
    _, user, path = applied(client)
    open_link(client, path)
    client.post(confirm_url(user), {"password": new_password()}, REMOTE_ADDR=IP)
    other = Client()
    open_link(other, path)
    assert other.post(confirm_url(user), {"password": new_password()}).status_code == 403


def test_the_confirm_page_refuses_without_an_opened_link(client: Client) -> None:
    _, user, _ = applied(client)
    assert client.get(confirm_url(user)).status_code == 403
    assert client.post(confirm_url(user), {"password": new_password()}).status_code == 403
    user.refresh_from_db()
    assert user.is_active is False


def test_a_link_never_activates_an_account_without_the_pending_marker(client: Client) -> None:
    _, user, path = applied(client)
    VendorApplication.objects.filter(user=user).delete()  # staff cleared it
    open_link(client, path)
    assert client.post(confirm_url(user), {"password": new_password()}).status_code == 403
    user.refresh_from_db()
    assert user.is_active is False


def test_a_rejected_applicant_cannot_confirm(client: Client) -> None:
    _, user, path = applied(client)
    vendor = VendorMember.objects.get(user=user).vendor
    services.change_status(vendor, engine.CLOSED, ctx=SYSTEM)
    open_link(client, path)
    assert client.post(confirm_url(user), {"password": new_password()}).status_code == 403


def test_the_confirm_page_gives_nothing_away_for_a_random_key(client: Client) -> None:
    url = reverse("vendors:apply_confirm", args=["00000000-0000-0000-0000-000000000000"])
    assert client.get(url).status_code == 403
    assert client.get(reverse("vendors:apply_confirm", args=["not-a-uuid"])).status_code == 403


def test_confirm_posts_are_rate_limited_per_ip(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise services.ApplicationLinkInvalid("simulated")  # the link stays usable

    monkeypatch.setattr(services, "confirm_application", refuse)
    _, user, path = applied(client)
    open_link(client, path)
    for _ in range(20):
        client.post(confirm_url(user), {"password": new_password()}, REMOTE_ADDR="198.51.100.77")
    response = client.post(
        confirm_url(user), {"password": new_password()}, REMOTE_ADDR="198.51.100.77"
    )
    assert response.status_code == 429
    assert int(response["Retry-After"]) > 0


# --- Abuse protection


@override_settings(**CAPTCHA)
def test_a_submission_without_a_valid_turnstile_token_is_refused(client: Client) -> None:
    data = form()
    assert apply(client, data).status_code == 400  # type: ignore[attr-defined]
    bad = apply(client, {**data, "cf-turnstile-response": "forged"})
    assert bad.status_code == 400  # type: ignore[attr-defined]
    assert not User.objects.filter(email=data["email"]).exists()
    assert not Vendor.objects.exists()
    ok = apply(client, {**data, "cf-turnstile-response": FakeCaptchaVerifier.VALID_TOKEN})
    assert ok.status_code == 302  # type: ignore[attr-defined]


def test_too_many_applications_from_one_ip_get_429(client: Client) -> None:
    for _ in range(REGISTRATION_LIMIT.free_attempts):
        apply(client)
    response = apply(client)
    assert response.status_code == 429  # type: ignore[attr-defined]
    assert "Retry-After" in response  # type: ignore[operator]
    assert apply(Client(), ip="198.51.100.88").status_code == 302  # type: ignore[attr-defined]
    assert client.get(URL, REMOTE_ADDR=IP).status_code == 200  # the form stays viewable


def test_a_post_without_the_csrf_token_is_refused() -> None:
    strict = Client(enforce_csrf_checks=True)
    response = strict.post(URL, form(), REMOTE_ADDR=IP)
    assert response.status_code == 403
    assert not Vendor.objects.exists()


def test_an_oversized_body_is_refused_before_it_is_parsed(client: Client) -> None:
    response = apply(client, form(business_name="x" * 20000))
    assert response.status_code == 413  # type: ignore[attr-defined]
    assert not Vendor.objects.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("full_name", "n" * 151),
        ("business_name", "b" * 151),
        ("email", "not-an-email"),
        ("email", "a" * 250 + "@example.com"),
        ("country", "US"),  # outside the allowed countries (CMP.13)
        ("country", "ZZ"),
        ("country", ""),
        ("full_name", ""),
        ("business_name", "   "),
    ],
)
def test_invalid_fields_are_refused_with_the_form_again(
    client: Client, field: str, value: str
) -> None:
    response = apply(client, form(**{field: value}))
    assert response.status_code == 200  # type: ignore[attr-defined]
    assert not Vendor.objects.exists()
    assert not User.objects.filter(is_active=False).exists()


def test_an_email_with_a_line_break_never_reaches_the_outbox(client: Client) -> None:
    response = apply(client, form(email="victim@example.com\nBcc: other@example.com"))
    assert response.status_code == 200  # type: ignore[attr-defined]
    assert not OutboxEmail.objects.exists()


def test_the_rate_limit_is_audited(client: Client) -> None:
    for _ in range(REGISTRATION_LIMIT.free_attempts + 1):
        apply(client)
    assert AuditLog.objects.filter(action="accounts.registration.rate_limited").exists()


# --- Retention, reminder and cancel link (owner decision D-24)


def age(user: User, *, hours: float) -> None:
    """Make the application `hours` hours older (the request and its link)."""
    when = timezone.now() - dt.timedelta(hours=hours)
    VendorApplication.objects.filter(user=user).update(created_at=when, link_sent_at=when)


def cancel_path(email: OutboxEmail) -> str:
    url = str(json.loads(email.context)["cancel_url"])
    return "/" + url.split("/", 3)[3]


def page(response: Any) -> str:
    """The page without its CSRF tokens, which differ on every response."""
    return re.sub(r"csrfmiddlewaretoken\" value=\"[^\"]*", "", response.content.decode())


def cancel_url(user: User) -> str:
    return reverse("vendors:apply_cancel", args=[user.public_id])


def test_d24_defaults_are_three_days_and_a_reminder_at_48_hours(
    settings: SettingsWrapper,
) -> None:
    assert settings.VENDOR_APPLICATION_RETENTION_DAYS == 3
    assert settings.VENDOR_APPLICATION_REMINDER_HOURS == 48


def test_the_purge_deletes_unconfirmed_applications_three_days_after_the_request(
    client: Client,
) -> None:
    old_data, old_user, _ = applied(client)
    fresh_data, fresh_user, _ = applied(Client())
    young_data, young_user, _ = applied(Client())
    age(old_user, hours=72 + 1)
    age(young_user, hours=71)
    confirmed = UserFactory.create()  # an ordinary account is never touched

    changes = services.purge_unconfirmed_applications(ctx=SYSTEM)

    assert not changes.is_empty
    assert not User.objects.filter(pk=old_user.pk).exists()
    assert not Vendor.objects.filter(contact_email=old_data["email"]).exists()
    assert not VendorMember.objects.filter(user_id=old_user.pk).exists()
    assert not VendorApplication.objects.filter(user_id=old_user.pk).exists()
    assert User.objects.filter(pk=fresh_user.pk).exists()
    assert User.objects.filter(pk=young_user.pk).exists()
    assert User.objects.filter(pk=confirmed.pk).exists()
    assert Vendor.objects.filter(contact_email=young_data["email"]).exists()
    assert Vendor.objects.filter(contact_email=fresh_data["email"]).exists()


def test_a_new_link_does_not_extend_the_three_days(client: Client) -> None:
    data, user, _ = applied(client)
    age(user, hours=60)
    apply(Client(), data, ip="198.51.100.9")  # a re-application: a new link
    VendorApplication.objects.filter(user=user).update(
        created_at=timezone.now() - dt.timedelta(hours=73)
    )
    services.purge_unconfirmed_applications(ctx=SYSTEM)
    assert not User.objects.filter(pk=user.pk).exists()


def test_the_purge_never_touches_a_confirmed_applicant(client: Client) -> None:
    _, user, path = applied(client)
    open_link(client, path)
    client.post(confirm_url(user), {"password": new_password()}, REMOTE_ADDR=IP)
    services.purge_unconfirmed_applications(ctx=SYSTEM)
    assert User.objects.filter(pk=user.pk, is_active=True).exists()


def test_the_purge_is_audited_by_count_only(client: Client) -> None:
    data, user, _ = applied(client)
    age(user, hours=80)
    services.purge_unconfirmed_applications(ctx=SYSTEM)
    [entry] = AuditLog.objects.filter(action=services.APPLICATION_PURGED)
    assert entry.after == {"deleted": 1, "retention_days": 3}
    assert data["email"] not in json.dumps([entry.before, entry.after, entry.target_key])


def test_the_purge_supports_dry_run(client: Client) -> None:
    _, user, _ = applied(client)
    age(user, hours=80)
    changes = services.purge_unconfirmed_applications(ctx=SYSTEM, dry_run=True)
    assert not changes.is_empty
    assert User.objects.filter(pk=user.pk).exists()
    assert not AuditLog.objects.filter(action=services.APPLICATION_PURGED).exists()


@override_settings(VENDOR_APPLICATION_RETENTION_DAYS=1)
def test_the_retention_period_is_a_setting(client: Client) -> None:
    _, user, _ = applied(client)
    age(user, hours=25)
    services.purge_unconfirmed_applications(ctx=SYSTEM)
    assert not User.objects.filter(pk=user.pk).exists()


def test_the_jobs_exist() -> None:
    from apps.vendors import tasks

    assert tasks.purge_unconfirmed_applications is not None
    assert tasks.remind_unconfirmed_applications is not None


# --- The reminder: exactly one, at 48 hours


def test_one_reminder_is_sent_at_48_hours_and_never_a_second(client: Client) -> None:
    data, user, _ = applied(client)
    assert services.send_application_reminders(ctx=SYSTEM).is_empty  # too early
    age(user, hours=49)

    changes = services.send_application_reminders(ctx=SYSTEM)

    assert not changes.is_empty
    [reminder] = emails("vendors.application_reminder")
    assert reminder.recipient == data["email"]
    assert "action_url" in json.loads(reminder.context)
    assert VendorApplication.objects.get(user=user).reminder_sent_at is not None
    assert services.send_application_reminders(ctx=SYSTEM).is_empty
    assert len(emails("vendors.application_reminder")) == 1


def test_the_reminder_goes_only_to_pending_applications(client: Client) -> None:
    _, confirmed, path = applied(client)
    open_link(client, path)
    client.post(confirm_url(confirmed), {"password": new_password()}, REMOTE_ADDR=IP)
    _, closed, _ = applied(Client())
    services.change_status(VendorMember.objects.get(user=closed).vendor, engine.CLOSED, ctx=SYSTEM)
    _, pending, _ = applied(Client())
    VendorApplication.objects.update(created_at=timezone.now() - dt.timedelta(hours=49))
    services.send_application_reminders(ctx=SYSTEM)
    [reminder] = emails("vendors.application_reminder")
    assert reminder.recipient == pending.email


def test_the_reminder_supports_dry_run(client: Client) -> None:
    _, user, _ = applied(client)
    age(user, hours=49)
    changes = services.send_application_reminders(ctx=SYSTEM, dry_run=True)
    assert not changes.is_empty
    assert not emails("vendors.application_reminder")
    assert VendorApplication.objects.get(user=user).reminder_sent_at is None


def test_the_reminder_link_confirms_and_the_first_link_keeps_working(client: Client) -> None:
    _, user, first = applied(client)
    age(user, hours=49)
    services.send_application_reminders(ctx=SYSTEM)
    second = link_path(emails("vendors.application_reminder")[0])
    assert second != first
    for path in (first, second):
        opened = Client()
        open_link(opened, path)
        assert opened.get(confirm_url(user)).status_code == 200


# --- The cancel link in every email: GET shows a button, POST deletes, nothing blocks


def test_the_confirmation_and_the_reminder_carry_a_cancel_link(client: Client) -> None:
    _, user, _ = applied(client)
    age(user, hours=49)
    services.send_application_reminders(ctx=SYSTEM)
    for email in (emails()[0], emails("vendors.application_reminder")[0]):
        context = json.loads(email.context)
        assert context["cancel_url"] != context["action_url"]
        assert "/account/link/" in context["cancel_url"]
        rendered = render_email(email.template_key, email.language, email.context_data)
        assert context["cancel_url"] in rendered.text_body
        assert escape(context["cancel_url"]) in rendered.html_body


def test_a_get_on_the_cancel_link_changes_nothing_and_keeps_the_token_valid(
    client: Client,
) -> None:
    _, user, _ = applied(client)
    path = cancel_path(emails()[0])
    # The mail scanner: GET and HEAD of the emailed link, then of the page it leads to.
    assert client.get(path).status_code == 200
    assert client.head(path).status_code == 200
    open_link(client, path)
    for method in (client.get, client.head):
        assert method(cancel_url(user)).status_code == 200
    assert User.objects.filter(pk=user.pk).exists()
    assert Vendor.objects.filter(contact_email=user.email).exists()
    assert VendorApplication.objects.filter(user=user).exists()
    assert not ActionLinkUse.objects.exists()
    fresh = Client()  # the token is still valid: another browser can open it
    open_link(fresh, path)
    assert b"<button" in fresh.get(cancel_url(user)).content


def test_a_get_on_the_confirm_link_never_confirms_it(client: Client) -> None:
    _, user, path = applied(client)
    assert client.get(path).status_code == 200
    assert client.head(path).status_code == 200
    open_link(client, path)
    assert client.get(confirm_url(user)).status_code == 200
    assert client.head(confirm_url(user)).status_code == 200
    user.refresh_from_db()
    assert user.is_active is False
    assert not ActionLinkUse.objects.exists()
    assert VendorApplication.objects.filter(user=user).exists()


def test_a_post_on_the_cancel_page_deletes_the_application_at_once(client: Client) -> None:
    data, user, _ = applied(client)
    open_link(client, cancel_path(emails()[0]))

    response = client.post(cancel_url(user), REMOTE_ADDR=IP)

    assert response.status_code == 200
    assert not User.objects.filter(email=data["email"]).exists()
    assert not Vendor.objects.filter(contact_email=data["email"]).exists()
    assert not VendorMember.objects.exists()
    assert not VendorApplication.objects.exists()
    [entry] = AuditLog.objects.filter(action=services.APPLICATION_CANCELLED)
    dumped = json.dumps([entry.before, entry.after, entry.target_key, entry.actor_label])
    assert data["email"] not in dumped
    assert "Ada" not in dumped


def test_a_second_post_and_a_new_visit_are_idempotent_and_neutral(client: Client) -> None:
    _, user, _ = applied(client)
    path = cancel_path(emails()[0])
    open_link(client, path)
    first = client.post(cancel_url(user), REMOTE_ADDR=IP)
    second = client.post(cancel_url(user), REMOTE_ADDR=IP)
    other = Client()
    open_link(other, path)
    third = other.post(cancel_url(user), REMOTE_ADDR=IP)
    never = Client().get(cancel_url(user))
    assert {first.status_code, second.status_code, third.status_code, never.status_code} == {200}
    assert page(first) == page(second) == page(third) == page(never)
    assert AuditLog.objects.filter(action=services.APPLICATION_CANCELLED).count() == 1


def test_the_cancel_link_never_deletes_a_confirmed_account(client: Client) -> None:
    _, user, path = applied(client)
    cancel = cancel_path(emails()[0])
    open_link(client, path)
    client.post(confirm_url(user), {"password": new_password()}, REMOTE_ADDR=IP)
    other = Client()
    open_link(other, cancel)
    response = other.post(cancel_url(user), REMOTE_ADDR=IP)
    assert response.status_code == 200  # the same neutral page
    assert User.objects.filter(pk=user.pk, is_active=True).exists()
    assert Vendor.objects.filter(contact_email=user.email).exists()


def test_the_cancel_link_survives_a_new_confirmation_link(client: Client) -> None:
    data, user, _ = applied(client)
    cancel = cancel_path(emails()[0])
    apply(Client(), data, ip="198.51.100.9")  # a re-application kills the older confirm link
    open_link(client, cancel)
    client.post(cancel_url(user), REMOTE_ADDR=IP)
    assert not User.objects.filter(pk=user.pk).exists()


def test_a_cancel_does_not_block_a_new_application_for_the_address(client: Client) -> None:
    data, user, _ = applied(client)
    open_link(client, cancel_path(emails()[0]))
    client.post(cancel_url(user), REMOTE_ADDR=IP)
    apply(Client(), data, ip="198.51.100.9")
    assert User.objects.filter(email=data["email"], is_active=False).exists()
    assert len(emails()) == 2  # a new confirmation mail


def test_the_cancel_page_gives_nothing_away_for_a_random_key(client: Client) -> None:
    neutral = client.get(reverse("vendors:apply_cancel", args=["not-a-uuid"]))
    random_key = client.get(
        reverse("vendors:apply_cancel", args=["00000000-0000-0000-0000-000000000000"])
    )
    assert neutral.status_code == random_key.status_code == 200
    assert page(neutral) == page(random_key)


def test_cancel_posts_are_rate_limited_per_ip(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> ChangeSet:
        return ChangeSet()  # nothing cancelled: the link stays usable

    monkeypatch.setattr(services, "cancel_application", refuse)
    _, user, _ = applied(client)
    open_link(client, cancel_path(emails()[0]))
    for _ in range(20):
        client.post(cancel_url(user), REMOTE_ADDR="198.51.100.77")
    response = client.post(cancel_url(user), REMOTE_ADDR="198.51.100.77")
    assert response.status_code == 429


def test_a_marker_without_a_vendor_is_not_pending() -> None:
    orphan = VendorApplicationFactory.create(owns_applicant_vendor=False)
    pending = VendorApplicationFactory.create()
    assert services._pending_application(str(orphan.user.public_id)) is None
    assert services._pending_application(str(pending.user.public_id)) is not None
