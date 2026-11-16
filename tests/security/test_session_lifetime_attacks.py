"""SEC.5 attack scenarios, category "Accounts", part 3: the role-based session lifetimes (CORE.24).

A stolen or forgotten staff session must end on the idle timeout and on the absolute lifetime,
whatever the holder does to keep it alive: polling, a second-factor check, the passkey window, a
replayed cookie, or a broken configuration. Each test plays that holder. Needs human review:
authentication code.
"""

import datetime as dt
from datetime import UTC, datetime
from typing import Any

import pytest
from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.models import Session
from django.test import Client
from django.urls import reverse

from apps.accounts import levels, lifetimes
from apps.accounts.models import User, UserSession
from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import password_login, totp_token, verified_login
from apps.core.models import AuditLog, PlatformOperator
from apps.core.tests.factories import PlatformOperatorFactory

pytestmark = pytest.mark.django_db

LIST = "/account/sessions/"
LOGIN = "/account/login/"


def _staff_session() -> tuple[User, Client]:
    user = UserFactory.create(staff=True)
    client = Client()
    verified_login(client, user, level=levels.NO_WINDOW)
    return user, client


def _age(client: Client, seconds: float, *, idle: bool = True) -> None:
    session = client.session
    session[lifetimes.SIGNED_IN_KEY] -= seconds
    if idle:
        session[lifetimes.SEEN_KEY] -= seconds
    session.save()


def _set(client: Client, **values: Any) -> None:
    session = client.session
    for key, value in values.items():
        session[key] = value
    session.save()


def _signed_in(client: Client) -> bool:
    return bool(client.session.get(SESSION_KEY))


def _last_reason() -> str:
    after = AuditLog.objects.filter(action=lifetimes.SESSION_EXPIRED).latest("id").after
    assert isinstance(after, dict)
    return str(after["reason"])


def test_polling_does_not_keep_an_idle_staff_session_alive() -> None:
    _, client = _staff_session()
    _age(client, 20 * 60)
    before = client.session[lifetimes.SEEN_KEY]
    client.get(LIST, headers={"x-mpmv-poll": "1"})
    client.get(LIST, headers={"sec-purpose": "prefetch"})
    assert client.session[lifetimes.SEEN_KEY] == before
    _age(client, 11 * 60)
    assert client.get(LIST, headers={"x-mpmv-poll": "1"}).status_code == 302
    assert not _signed_in(client)
    assert _last_reason() == "idle"


def test_a_second_factor_check_does_not_reset_the_absolute_lifetime() -> None:
    user = UserFactory.create(staff=True)
    device = TOTPDeviceFactory.create(user=user)
    client = Client()
    password_login(client, user)
    _age(client, 12 * 3600 + 60, idle=False)
    client.post(reverse("mfa:totp"), {"token": totp_token(device)})
    assert not _signed_in(client)
    assert _last_reason() == "absolute"


def test_logging_in_again_is_a_new_sign_in() -> None:
    """A fresh password sign-in restarts the count (it is a new authentication)."""
    user, client = _staff_session()
    _age(client, 11 * 3600, idle=False)
    client.force_login(user)
    assert client.session[lifetimes.SIGNED_IN_KEY] > datetime.now(UTC).timestamp() - 60


def test_a_replayed_cookie_of_an_ended_session_is_anonymous() -> None:
    _, client = _staff_session()
    stolen = client.cookies["sessionid"].value
    _age(client, 31 * 60)
    client.get(LIST)
    attacker = Client()
    attacker.cookies["sessionid"] = stolen
    response = attacker.get(LIST)
    assert response.status_code == 302
    assert not Session.objects.filter(session_key=stolen).exists()


def test_a_post_on_an_expired_session_does_nothing() -> None:
    user, client = _staff_session()
    other = Client()
    verified_login(other, user, level=levels.NO_WINDOW)
    _age(client, 31 * 60)
    response = client.post(f"{LIST}others/end/")
    assert response.status_code == 302
    assert response["Location"].startswith(LOGIN)
    assert _signed_in(other)  # the other session was not ended by the expired one


@pytest.mark.parametrize(
    "stamps",
    [
        {lifetimes.SIGNED_IN_KEY: "yesterday"},
        {lifetimes.SIGNED_IN_KEY: True},
        {lifetimes.SEEN_KEY: None},
        {lifetimes.SIGNED_IN_KEY: datetime.now(UTC).timestamp() + 3600},
        {lifetimes.SEEN_KEY: datetime.now(UTC).timestamp() + 3600},
    ],
)
def test_a_session_with_a_broken_stamp_fails_closed(stamps: dict[str, Any]) -> None:
    _, client = _staff_session()
    key = client.cookies["sessionid"].value
    _set(client, **stamps)
    assert client.get(LIST).status_code == 302
    assert not _signed_in(client)
    assert not UserSession.objects.filter(session_key=key).exists()
    assert _last_reason() == "invalid"


def test_an_invalid_stored_policy_keeps_the_default_staff_limits() -> None:
    operator = PlatformOperatorFactory.create(
        open_ended=True, valid_from=datetime(2020, 1, 1, tzinfo=UTC)
    )
    PlatformOperator.objects.filter(pk=operator.pk).update(
        session_policy={"staff_absolute_hours": 100000}
    )
    _, client = _staff_session()
    _age(client, 12 * 3600 + 60, idle=False)
    assert client.get(LIST).status_code == 302
    assert _last_reason() == "absolute"


def test_a_broken_staff_check_fails_closed_to_the_staff_lifetime(settings: Any) -> None:
    settings.SESSION_STAFF_CHECKS = ["apps.does_not_exist.check"]
    user = UserFactory.create()  # a customer
    client = Client()
    client.force_login(user)
    _age(client, 31 * 60)
    assert client.get(LIST).status_code == 302
    after = AuditLog.objects.filter(action=lifetimes.SESSION_EXPIRED).latest("id").after
    assert isinstance(after, dict)
    assert after["lifetime"] == "staff"


def test_the_storage_expiry_never_outlives_the_staff_lifetime_by_more_than_the_grace() -> None:
    _, client = _staff_session()
    stored = Session.objects.get(session_key=client.cookies["sessionid"].value).expire_date
    assert stored <= datetime.now(UTC) + dt.timedelta(minutes=30) + lifetimes.grace()
