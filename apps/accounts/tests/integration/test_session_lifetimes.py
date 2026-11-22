"""CORE.24 part 2: the role-based session lifetimes.

Customers keep a rolling session (14 days by default); staff, platform staff, vendor members and
editors get an idle timeout (30 min) and an absolute lifetime (12 h) that activity never resets.
The values come from the `PlatformOperator` configuration, read per request. Time moves by ageing
the session's own stamps (as the passkey window tests do). Needs human review: authentication code.
"""

import datetime as dt
import json
import time
from datetime import UTC, datetime
from typing import Any

import pytest
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import Permission
from django.contrib.sessions.models import Session
from django.db import connection
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.html import escape

from apps.accounts import levels, lifetimes
from apps.accounts.models import User, UserSession
from apps.accounts.session_store import SessionStore
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.fake_authenticator import FakeAuthenticator
from apps.accounts.tests.mfa import password_login, verified_login
from apps.core.audit import AuditContext
from apps.core.i18n import primary_language
from apps.core.models import AuditLog
from apps.core.services import configure_platform_operator
from apps.core.tests.factories import PlatformOperatorFactory

pytestmark = pytest.mark.django_db

LIST = "/account/sessions/"
LOGIN = "/account/login/"
ORIGIN = "http://testserver"
DAY = 24 * 3600
SYSTEM = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)


def key_of(client: Client) -> str:
    return client.cookies["sessionid"].value


def is_signed_in(client: Client) -> bool:
    return bool(client.session.get(SESSION_KEY))


def age(client: Client, seconds: float, *, idle: bool = True) -> None:
    """Move the session `seconds` into the past: its sign-in, and its last use when `idle`."""
    session = client.session
    session[lifetimes.SIGNED_IN_KEY] -= seconds
    if idle:
        session[lifetimes.SEEN_KEY] -= seconds
    session.save()


def customer_client() -> tuple[User, Client]:
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    return user, client


def staff_client() -> tuple[User, Client]:
    user = UserFactory.create(staff=True)
    client = Client()
    verified_login(client, user, level=levels.NO_WINDOW)
    return user, client


def expired_entries() -> list[dict[str, Any]]:
    rows = AuditLog.objects.filter(action=lifetimes.SESSION_EXPIRED).order_by("id")
    return [row.after for row in rows if isinstance(row.after, dict)]


def assert_ended(client: Client, user: User, key: str, response: Any, reason: str) -> None:
    assert response.status_code == 302
    assert response["Location"].startswith(LOGIN)
    assert not Session.objects.filter(session_key=key).exists()
    assert not UserSession.objects.filter(session_key=key).exists()
    assert not is_signed_in(client)
    entry = expired_entries()[-1]
    assert entry["reason"] == reason
    row = AuditLog.objects.filter(action=lifetimes.SESSION_EXPIRED).latest("id")
    assert row.target_key == str(user.public_id)
    assert row.ip is None
    stored = json.dumps([row.before, row.after, row.actor_label])
    assert user.email not in stored
    assert key not in stored


# --- Customers: rolling, 14 days


def test_a_customer_session_survives_13_days_idle() -> None:
    _, client = customer_client()
    age(client, 13 * DAY)
    assert client.get(LIST).status_code == 200
    assert is_signed_in(client)
    assert not expired_entries()


def test_a_customer_session_ends_after_14_days_idle() -> None:
    user, client = customer_client()
    key = key_of(client)
    age(client, 14 * DAY + 60)
    response = client.get(LIST)
    assert_ended(client, user, key, response, "idle")
    assert expired_entries()[-1]["lifetime"] == "customer"


def test_a_customer_session_is_rolling_with_no_absolute_limit() -> None:
    _, client = customer_client()
    age(client, 40 * DAY, idle=False)  # signed in 40 days ago, used just now
    assert client.get(LIST).status_code == 200
    assert is_signed_in(client)


def test_activity_rolls_a_customer_session_forward() -> None:
    _, client = customer_client()
    age(client, 10 * DAY)
    assert client.get(LIST).status_code == 200
    seen = client.session[lifetimes.SEEN_KEY]
    assert seen == pytest.approx(time.time(), abs=30)
    stored = Session.objects.get(session_key=key_of(client)).expire_date
    assert stored > timezone.now() + dt.timedelta(days=13, hours=23)


# --- Staff: idle 30 min, absolute 12 h


def test_a_staff_session_survives_29_minutes_idle() -> None:
    _, client = staff_client()
    age(client, 29 * 60)
    assert client.get(LIST).status_code == 200
    assert is_signed_in(client)


def test_a_staff_session_ends_after_31_minutes_idle() -> None:
    user, client = staff_client()
    key = key_of(client)
    age(client, 31 * 60)
    response = client.get(LIST)
    assert_ended(client, user, key, response, "idle")
    assert expired_entries()[-1]["lifetime"] == "staff"


def test_a_staff_session_ends_after_12_hours_even_if_active() -> None:
    user, client = staff_client()
    key = key_of(client)
    age(client, 11 * 3600 + 59 * 60, idle=False)
    assert client.get(LIST).status_code == 200
    age(client, 2 * 60, idle=False)  # 12 h 1 min since sign-in, used a moment ago
    response = client.get(LIST)
    assert_ended(client, user, key, response, "absolute")


def test_activity_never_moves_a_staff_session_past_its_absolute_limit() -> None:
    _, client = staff_client()
    age(client, 11 * 3600 + 50 * 60, idle=False)
    session = client.session
    session[lifetimes.SEEN_KEY] -= 2 * 60  # last used 2 min ago: this request counts as use
    session.save()
    client.get(LIST)
    assert client.session[lifetimes.SEEN_KEY] == pytest.approx(time.time(), abs=30)
    stored = Session.objects.get(session_key=key_of(client)).expire_date
    signed_in = dt.datetime.fromtimestamp(client.session[lifetimes.SIGNED_IN_KEY], tz=UTC)
    limit = signed_in + dt.timedelta(hours=12) + lifetimes.grace()
    assert abs(stored - limit) < dt.timedelta(seconds=2)


def test_the_login_page_says_why_the_session_ended() -> None:
    _, client = staff_client()
    age(client, 31 * 60)
    response = client.get(LIST, follow=True)
    with translation.override(primary_language()):
        message = str(lifetimes.IDLE_MESSAGE)
    assert escape(message) in response.content.decode()


def test_a_json_client_gets_401_when_its_session_ends() -> None:
    _, client = staff_client()
    age(client, 31 * 60)
    response = client.get(LIST, headers={"accept": "application/json"})
    assert response.status_code == 401
    assert response.json()["error"] == "session_expired"


def test_the_login_page_after_an_expiry_is_reached_without_a_loop() -> None:
    _, client = staff_client()
    age(client, 31 * 60)
    response = client.get(LIST, follow=True)
    assert response.status_code == 200
    assert response.redirect_chain[0][0].startswith(LOGIN)


def test_the_end_writes_one_audit_entry_and_nothing_on_a_dry_run() -> None:
    user, client = staff_client()
    session = SessionStore(key_of(client))
    session.load()
    changes = lifetimes.end_expired_session(
        user, session, reason="idle", lifetime="staff", ctx=SYSTEM, dry_run=True
    )
    assert not changes.is_empty
    assert Session.objects.filter(session_key=key_of(client)).exists()
    assert not expired_entries()
    lifetimes.end_expired_session(user, session, reason="idle", lifetime="staff", ctx=SYSTEM)
    assert not Session.objects.filter(session_key=key_of(client)).exists()
    assert expired_entries() == [{"reason": "idle", "lifetime": "staff"}]


# --- Who counts as staff: decided per request


def test_platform_staff_without_the_staff_flag_get_the_staff_lifetime() -> None:
    user = UserFactory.create(permissions=["core.platform_owner"])
    client = Client()
    verified_login(client, user, level=levels.NO_WINDOW)
    key = key_of(client)
    age(client, 31 * 60)
    assert_ended(client, user, key, client.get(LIST), "idle")


def test_an_editor_of_the_cms_gets_the_staff_lifetime() -> None:
    user = UserFactory.create(permissions=["wagtailadmin.access_admin"])
    client = Client()
    verified_login(client, user, level=levels.NO_WINDOW)
    key = key_of(client)
    age(client, 31 * 60)
    assert_ended(client, user, key, client.get(LIST), "idle")


def test_a_role_change_mid_session_applies_the_stricter_limit_at_once() -> None:
    user, client = customer_client()
    age(client, 13 * 3600, idle=False)  # signed in 13 h ago, active: fine for a customer
    assert client.get(LIST).status_code == 200
    key = key_of(client)
    # Straight in the table: no service call, so part 1 ends no session (a data fix, a script).
    User.objects.filter(pk=user.pk).update(is_staff=True)
    response = client.get(LIST)
    assert_ended(client, user, key, response, "absolute")
    assert expired_entries()[-1]["lifetime"] == "staff"


def test_a_staff_member_who_becomes_a_customer_gets_the_customer_lifetime() -> None:
    user, client = staff_client()
    User.objects.filter(pk=user.pk).update(is_staff=False)
    age(client, 13 * 3600, idle=False)
    assert client.get(LIST).status_code == 200


# --- The values come from the PlatformOperator configuration


def test_a_new_operator_version_changes_the_lifetime_with_no_code_change() -> None:
    PlatformOperatorFactory.create(open_ended=True, valid_from=datetime(2020, 1, 1, tzinfo=UTC))
    _, client = staff_client()
    age(client, 20 * 60)
    assert client.get(LIST).status_code == 200  # 20 min idle: within the default 30
    new = PlatformOperatorFactory.build(
        valid_from=timezone.now() - dt.timedelta(seconds=1),
        valid_to=None,
        session_policy={"staff_idle_minutes": 10},
    )
    configure_platform_operator(new, ctx=SYSTEM)
    age(client, 11 * 60)
    response = client.get(LIST)
    assert response.status_code == 302
    assert expired_entries()[-1]["reason"] == "idle"


def test_a_longer_customer_lifetime_from_the_configuration_applies() -> None:
    PlatformOperatorFactory.create(
        open_ended=True,
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        session_policy={"customer_days": 30},
    )
    _, client = customer_client()
    age(client, 20 * DAY)
    assert client.get(LIST).status_code == 200
    stored = Session.objects.get(session_key=key_of(client)).expire_date
    assert stored > timezone.now() + dt.timedelta(days=29)


# --- The passkey window lives inside the session


def _staff_with_passkey() -> tuple[User, FakeAuthenticator]:
    """A staff user who may elevate, with a passkey enrolled in another session."""
    user = UserFactory.create(staff=True)
    user.user_permissions.add(Permission.objects.get(codename=levels.ELEVATE_CODENAME))
    setup = Client()
    password_login(setup, user)
    authenticator = FakeAuthenticator(ORIGIN)
    options = _post(setup, "mfa:passkey_register_options").json()["publicKey"]
    credential = authenticator.create(options)
    assert _post(setup, "mfa:passkey_register", {"credential": credential}).status_code == 200
    return user, authenticator


def _post(client: Client, name: str, payload: dict[str, Any] | None = None) -> Any:
    return client.post(
        reverse(name), data=json.dumps(payload or {}), content_type="application/json"
    )


def _open_window(client: Client, authenticator: FakeAuthenticator) -> None:
    options = _post(client, "levels:options")
    assert options.status_code == 200
    credential = authenticator.get(options.json()["publicKey"])
    assert _post(client, "levels:window", {"credential": credential}).status_code == 200


def test_opening_the_passkey_window_keeps_the_sign_in_time() -> None:
    user, authenticator = _staff_with_passkey()
    client = Client()
    verified_login(client, user, level=levels.NO_WINDOW)
    age(client, 3600, idle=False)
    signed_in = client.session[lifetimes.SIGNED_IN_KEY]
    key = key_of(client)
    _open_window(client, authenticator)
    assert key_of(client) != key  # the window cycles the session key
    assert client.session[lifetimes.SIGNED_IN_KEY] == signed_in


def test_the_passkey_window_cannot_outlive_the_absolute_limit() -> None:
    user, authenticator = _staff_with_passkey()
    client = Client()
    verified_login(client, user, level=levels.NO_WINDOW)
    age(client, 11 * 3600 + 50 * 60, idle=False)
    _open_window(client, authenticator)
    request = RequestFactory().get(LIST)
    request.session = client.session
    request.user = user
    assert levels.current_level(request) == levels.PASSKEY_WINDOW
    left = levels.seconds_left(request)  # what the banner shows: the session's end comes first
    assert left is not None
    assert left <= 10 * 60
    key = key_of(client)
    age(client, 11 * 60, idle=False)  # 12 h 1 min: the window (2 h cap) is still young
    response = client.get(LIST)
    assert_ended(client, user, key, response, "absolute")
    closed = AuditLog.objects.filter(action=levels.SESSION_ENDED).latest("id").after
    assert isinstance(closed, dict)
    assert closed["reason"] == "expired"


def test_session_activity_does_not_keep_the_passkey_window_open() -> None:
    user, authenticator = _staff_with_passkey()
    client = Client()
    verified_login(client, user, level=levels.NO_WINDOW)
    _open_window(client, authenticator)
    session = client.session
    session[levels.ACTIVITY_KEY] -= 16 * 60
    session[lifetimes.SEEN_KEY] -= 16 * 60
    session[lifetimes.SIGNED_IN_KEY] -= 16 * 60
    session.save()
    assert client.get(LIST).status_code == 200  # the session lives (16 < 30 min idle)
    assert levels.STARTED_KEY not in client.session  # the window closed on its own idle


# --- Sign-in and sessions from before the lifetimes


def test_a_login_stamps_the_session() -> None:
    _, client = customer_client()
    session = client.session
    assert session[lifetimes.SIGNED_IN_KEY] == pytest.approx(time.time(), abs=30)
    assert session[lifetimes.SEEN_KEY] == session[lifetimes.SIGNED_IN_KEY]


def test_a_session_from_before_the_lifetimes_is_stamped_from_its_index_row() -> None:
    user, client = customer_client()
    session = client.session
    del session[lifetimes.SIGNED_IN_KEY]
    del session[lifetimes.SEEN_KEY]
    session.save()
    created = timezone.now() - dt.timedelta(hours=13)
    UserSession.objects.filter(session_key=key_of(client)).update(created_at=created)
    User.objects.filter(pk=user.pk).update(is_staff=True)
    # Staff now, signed in 13 h ago by the index: past the absolute limit.
    response = client.get(LIST)
    assert response.status_code == 302
    assert expired_entries()[-1]["reason"] == "absolute"


def test_a_session_from_before_the_lifetimes_with_no_index_row_starts_now() -> None:
    _, client = customer_client()
    session = client.session
    del session[lifetimes.SIGNED_IN_KEY]
    del session[lifetimes.SEEN_KEY]
    session.save()
    UserSession.objects.all().delete()
    assert client.get(LIST).status_code == 200
    assert client.session[lifetimes.SIGNED_IN_KEY] == pytest.approx(time.time(), abs=30)


# --- Cost: the lifetime is decided once per request


def _request_of(client: Client, user: User) -> Any:
    request = RequestFactory().get(LIST)
    request.session = client.session
    request.user = user
    return request


@pytest.mark.parametrize("make", [customer_client, staff_client])
def test_the_lifetime_is_decided_once_per_request(make: Any) -> None:
    user, client = make()
    session = client.session
    session[levels.STARTED_KEY] = session[levels.ACTIVITY_KEY] = time.time()  # the banner asks
    session.save()
    request = _request_of(client, user)
    with CaptureQueriesContext(connection) as first:
        assert lifetimes.enforce(request) is None
    assert len(first) <= 7
    with CaptureQueriesContext(connection) as again:
        lifetimes.lifetime_for_request(request)
        lifetimes.seconds_left(request)
        levels.seconds_left(request)  # the passkey window banner
        levels.policy(request)
    assert len(again) == 0


def test_the_per_request_lifetime_follows_a_change_of_user() -> None:
    customer, client = customer_client()
    request = _request_of(client, customer)
    assert lifetimes.lifetime_for_request(request).kind == "customer"
    request.user = UserFactory.create(staff=True)
    assert lifetimes.lifetime_for_request(request).kind == "staff"
