"""VEN.22: the public "Sell with us" application (owner decision Q-55).

Done when: a visitor applies from the public page, confirms the email, enrols MFA at first login
and appears as an applicant in the admin; a bot submission without a valid Turnstile token is
refused; the form passes the CMP.9 checks. Security: an application never grants access by
itself (the account stays inactive until the emailed link is used), answers never tell whether
an email address already has an account, and every step is rate limited and audited.
"""

import json
import secrets
from collections.abc import Iterator
from html.parser import HTMLParser

import pytest
from django.contrib.auth.hashers import check_password
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import ActionLinkUse, User
from apps.accounts.services import REGISTRATION_LIMIT, mfa_required
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.fakes import FakeCaptchaVerifier
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.vendors import engine, services
from apps.vendors.models import Vendor, VendorMember
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
        "password": secrets.token_urlsafe(18),
    }
    return {**data, **overrides}


def apply(client: Client, data: dict[str, str] | None = None, ip: str = IP) -> object:
    return client.post(URL, data if data is not None else form(), REMOTE_ADDR=ip)


def last_email(template_key: str | None = None) -> OutboxEmail:
    emails = OutboxEmail.objects.order_by("-id")
    if template_key:
        emails = emails.filter(template_key=template_key)
    email = emails.first()
    assert email is not None
    return email


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
        "password",
    }


def test_the_form_is_accessible_without_a_puzzle(client: Client) -> None:
    html = client.get(URL).content.decode()
    fields = inputs_of(html)
    assert fields["password"]["type"] == "password"
    assert fields["password"]["autocomplete"] == "new-password"
    assert fields["password"].get("data-password-toggle")
    assert fields["email"]["autocomplete"] == "email"
    assert fields["country"]["autocomplete"] == "country"
    assert fields["full_name"]["autocomplete"] == "name"
    assert all("onpaste" not in attrs for attrs in fields.values())
    for name in ("full_name", "email", "business_name", "country", "password"):
        assert f'for="id_{name}"' in html  # a visible label for every field
    assert "accounts/password-toggle.js" in html


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


def test_an_application_creates_an_inactive_account_and_an_applicant_vendor(
    client: Client,
) -> None:
    data = form()
    response = apply(client, data)

    assert response.status_code == 302  # type: ignore[attr-defined]
    assert response["Location"] == reverse("vendors:apply_sent")  # type: ignore[index]
    user = User.objects.get(email=data["email"])
    assert user.is_active is False
    assert user.full_name == "Ada Example"
    assert check_password(data["password"], user.password)
    assert user.password != data["password"]
    vendor = Vendor.objects.get(contact_email=data["email"])
    assert vendor.status == engine.APPLICANT
    assert vendor.legal_name == vendor.display_name == "Ada Workshop"
    member = VendorMember.objects.get(vendor=vendor)
    assert (member.user, member.role) == (user, engine.OWNER)


def test_an_application_never_signs_anyone_in_or_grants_access(client: Client) -> None:
    data = form()
    apply(client, data)
    assert "_auth_user_id" not in client.session
    assert client.login(username=data["email"], password=data["password"]) is False
    assert client.get("/manager/").status_code in (302, 403, 404)


def test_an_application_collects_nothing_beyond_the_form(client: Client) -> None:
    data = form()
    apply(client, data)
    vendor = Vendor.objects.get(contact_email=data["email"])
    assert (vendor.phone, vendor.vat_number, vendor.tax_code, vendor.pec) == ("", "", "", "")
    assert vendor.registered_street == ""


def test_the_confirmation_email_goes_through_the_outbox_without_tracking(
    client: Client,
) -> None:
    data = form()
    apply(client, data)
    email = last_email("vendors.application_confirm")
    assert email.recipient == data["email"]
    assert email.status == OutboxEmail.Status.QUEUED
    assert "/account/link/" in json.loads(email.context)["action_url"]
    from apps.core.services import render_email

    rendered = render_email(email.template_key, email.language, email.context_data)
    assert "<img" not in rendered.html_body.lower()
    assert data["password"] not in rendered.text_body
    assert data["password"] not in email.context


def test_the_application_is_audited_without_the_password(client: Client) -> None:
    data = form()
    apply(client, data, ip=IP)
    entries = AuditLog.objects.filter(action=services.APPLICATION_SUBMITTED)
    assert entries.count() == 2  # the vendor and its owner membership
    for entry in entries:
        assert entry.ip == IP
        assert data["password"] not in json.dumps([entry.before, entry.after])
    assert not AuditLog.objects.filter(after__icontains=data["password"]).exists()


def test_the_service_supports_dry_run(client: Client) -> None:
    data = form()
    changes = services.apply_to_sell(
        full_name=data["full_name"],
        email=data["email"],
        business_name=data["business_name"],
        country=data["country"],
        password=data["password"],
        language="en",
        ctx=SYSTEM,
        dry_run=True,
    )
    assert not changes.is_empty
    assert not User.objects.filter(email=data["email"]).exists()
    assert not Vendor.objects.filter(contact_email=data["email"]).exists()
    assert not OutboxEmail.objects.exists()


# --- No enumeration, no takeover


def test_an_existing_account_gets_the_same_answer_and_is_left_untouched(
    client: Client,
) -> None:
    password = secrets.token_urlsafe(18)
    existing = UserFactory.create(password=password)
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
    assert User.objects.filter(email=existing.email).count() == 1


def test_an_existing_account_is_told_by_email_with_no_link(client: Client) -> None:
    existing = UserFactory.create()
    apply(client, form(email=existing.email))
    email = last_email("vendors.application_exists")
    assert email.recipient == existing.email
    assert "action_url" not in json.loads(email.context)
    assert not OutboxEmail.objects.filter(template_key="vendors.application_confirm").exists()


def test_the_email_case_does_not_reveal_an_account(client: Client) -> None:
    existing = UserFactory.create(email="Mixed.Case@example.com")
    apply(client, form(email="mixed.case@EXAMPLE.com"))
    assert User.objects.filter(email__iexact=existing.email).count() == 1
    assert not Vendor.objects.exists()


def test_a_weak_password_is_refused_the_same_way_for_any_email(client: Client) -> None:
    existing = UserFactory.create()
    weak = {"password": "12345678"}
    known = client.post(URL, form(email=existing.email, **weak), REMOTE_ADDR=IP)
    unknown = client.post(URL, form(**weak), REMOTE_ADDR="198.51.100.7")
    assert known.status_code == unknown.status_code == 200
    assert not Vendor.objects.exists()
    assert not OutboxEmail.objects.exists()
    assert b"error-summary" in known.content
    assert b"error-summary" in unknown.content


def test_an_inactive_account_without_an_application_is_never_reactivated(
    client: Client,
) -> None:
    blocked = UserFactory.create(is_active=False)
    apply(client, form(email=blocked.email))
    blocked.refresh_from_db()
    assert blocked.is_active is False
    assert not OutboxEmail.objects.filter(template_key="vendors.application_confirm").exists()


def test_applying_again_before_confirming_resends_the_link_and_changes_nothing(
    client: Client,
) -> None:
    data = form()
    apply(client, data)
    user = User.objects.get(email=data["email"])
    stored_hash = user.password

    attacker = form(email=data["email"], business_name="Other Shop")
    response = apply(Client(), attacker, ip="198.51.100.9")

    assert response.status_code == 302  # type: ignore[attr-defined]
    user.refresh_from_db()
    assert user.password == stored_hash  # the second password is never stored
    assert Vendor.objects.filter(contact_email=data["email"]).count() == 1
    assert OutboxEmail.objects.filter(template_key="vendors.application_confirm").count() == 2


def test_resends_to_one_address_are_limited_per_day(client: Client) -> None:
    data = form()
    for index in range(4):
        apply(Client(), data, ip=f"198.51.100.{10 + index}")
    sent = OutboxEmail.objects.filter(recipient=data["email"]).count()
    assert sent == 2  # the first email and one resend per address and day


# --- Confirming the email


def confirm_url(user: User) -> str:
    return reverse("vendors:apply_confirm", args=[user.public_id])


def applied(client: Client) -> tuple[dict[str, str], User, str]:
    data = form()
    apply(client, data)
    user = User.objects.get(email=data["email"])
    return data, user, link_path(last_email("vendors.application_confirm"))


def test_opening_the_link_changes_nothing_until_the_confirm_button(client: Client) -> None:
    _, user, path = applied(client)
    response = open_link(client, path)
    assert response["Location"] == confirm_url(user)
    page = client.get(confirm_url(user))
    assert page.status_code == 200
    user.refresh_from_db()
    assert user.is_active is False
    assert not ActionLinkUse.objects.exists()


def test_confirming_activates_the_account_which_must_then_enrol_mfa(client: Client) -> None:
    data, user, path = applied(client)
    open_link(client, path)
    response = client.post(confirm_url(user), REMOTE_ADDR=IP)

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.is_active is True
    assert "_auth_user_id" not in client.session  # confirming never signs in
    assert client.login(username=data["email"], password=data["password"]) is True
    user.refresh_from_db()
    assert mfa_required(user)  # CORE.4: an owner enrols a second factor at first login
    vendor = Vendor.objects.get(contact_email=data["email"])
    assert vendor.status == engine.APPLICANT  # staff review comes next (VEN.5)
    assert ActionLinkUse.objects.filter(user=user).count() == 1
    [entry] = AuditLog.objects.filter(action=services.APPLICATION_CONFIRMED)
    assert entry.before is not None
    assert entry.before["is_active"] is False
    assert entry.after is not None
    assert entry.after["is_active"] is True


def test_the_link_works_once(client: Client) -> None:
    _, user, path = applied(client)
    open_link(client, path)
    client.post(confirm_url(user), REMOTE_ADDR=IP)
    other = Client()
    open_link(other, path)
    assert other.post(confirm_url(user), REMOTE_ADDR=IP).status_code == 403


def test_the_confirm_page_refuses_without_an_opened_link(client: Client) -> None:
    _, user, _ = applied(client)
    assert client.get(confirm_url(user)).status_code == 403
    assert client.post(confirm_url(user), REMOTE_ADDR=IP).status_code == 403
    user.refresh_from_db()
    assert user.is_active is False


def test_a_link_never_activates_an_account_that_has_signed_in_or_is_not_pending(
    client: Client,
) -> None:
    _, user, path = applied(client)
    user.last_login = user.date_joined  # staff blocked it after use: not a pending application
    user.save(update_fields=["last_login"])
    open_link(client, path)
    assert client.post(confirm_url(user), REMOTE_ADDR=IP).status_code == 403
    user.refresh_from_db()
    assert user.is_active is False


def test_a_rejected_applicant_cannot_confirm(client: Client) -> None:
    _, user, path = applied(client)
    vendor = VendorMember.objects.get(user=user).vendor
    services.change_status(vendor, engine.CLOSED, ctx=SYSTEM)
    open_link(client, path)
    assert client.post(confirm_url(user), REMOTE_ADDR=IP).status_code == 403


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
        client.post(confirm_url(user), REMOTE_ADDR="198.51.100.77")
    response = client.post(confirm_url(user), REMOTE_ADDR="198.51.100.77")
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
        ("password", "p" * 129),
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
