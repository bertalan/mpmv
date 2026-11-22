"""CORE.24 part 1: the per-user session index, the invalidations and the account's session list.

Real PostgreSQL and real sessions (Django's database backend): a "signed-in client" is a test
client after `force_login`, and a session that ended is gone from the session table, so its next
request is anonymous. Needs human review: authentication code.
"""

import datetime as dt
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import Permission
from django.contrib.sessions.models import Session
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts import levels, services
from apps.accounts.models import User, UserSession
from apps.accounts.session_store import SessionStore, index_session
from apps.accounts.tests.factories import (
    PasskeyDeviceFactory,
    TOTPDeviceFactory,
    UserFactory,
    UserSessionFactory,
)
from apps.accounts.tests.mfa import totp_token, verified_login
from apps.core.audit import AuditContext, user_context
from apps.core.models import AuditLog
from tests.i18n import translated

pytestmark = pytest.mark.django_db

SYSTEM = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
LIST = "/account/sessions/"


def signed_in_client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def key_of(client: Client) -> str:
    return client.cookies["sessionid"].value


def is_signed_in(client: Client) -> bool:
    return bool(client.session.get(SESSION_KEY))


def ended_entry() -> dict[str, Any]:
    """The `after` of the one "sessions ended" audit entry."""
    after = AuditLog.objects.get(action=services.SESSIONS_ENDED).after
    assert isinstance(after, dict)
    return after


def self_ctx(user: User) -> AuditContext:
    return user_context(user, None)


@contextmanager
def request_of(client: Client) -> Iterator[None]:
    """Run a service as if inside a request of `client` (its session is the request's)."""
    store = SessionStore(key_of(client))
    store.load()
    token = services.bind_current_session(store)
    try:
        yield
    finally:
        services.unbind_current_session(token)


# --- The index


def test_a_login_adds_the_session_to_the_index() -> None:
    user = UserFactory.create()
    client = signed_in_client(user)
    row = UserSession.objects.get(user=user)
    assert row.session_key == key_of(client)
    assert services.user_sessions(user) == [row]


def test_the_index_keeps_a_device_summary_and_a_network_prefix_only() -> None:
    user = UserFactory.create()
    store = SessionStore()
    store.create()
    index_session(
        user,
        store,
        user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
        ip="203.0.113.77",
    )
    row = UserSession.objects.get(session_key=store.session_key)
    assert (row.browser, row.system, row.network) == ("Firefox", "Linux", "203.0.113.0/24")
    values = UserSession.objects.filter(pk=row.pk).values_list().first() or ()
    stored = " ".join(str(v) for v in values)
    assert "203.0.113.77" not in stored
    assert "Gecko" not in stored


def test_a_rotated_key_keeps_its_index_row_and_a_deleted_session_loses_it() -> None:
    user = UserFactory.create()
    client = signed_in_client(user)
    store = SessionStore(key_of(client))
    store.load()
    store.cycle_key()
    row = UserSession.objects.get(user=user)
    assert row.session_key == store.session_key
    store.flush()
    assert not UserSession.objects.filter(user=user).exists()


def test_logout_removes_the_session_from_the_index() -> None:
    user = UserFactory.create()
    client = signed_in_client(user)
    client.post(reverse("logout"))
    assert not UserSession.objects.filter(user=user).exists()


def test_a_request_touches_last_use_at_most_every_few_minutes(settings: object) -> None:
    user = UserFactory.create()
    client = signed_in_client(user)
    row = UserSession.objects.get(user=user)
    first = row.last_seen
    client.get(LIST)
    assert UserSession.objects.get(pk=row.pk).last_seen == first  # fresh: nothing written
    settings.SESSION_TOUCH_SECONDS = 0  # type: ignore[attr-defined]
    client.get(LIST, HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0) Chrome/131.0 Safari/537.36")
    touched = UserSession.objects.get(pk=row.pk)
    assert touched.last_seen > first
    assert (touched.browser, touched.system) == ("Chrome", "Windows")


def test_a_session_from_before_the_index_joins_it_on_its_next_request() -> None:
    user = UserFactory.create()
    client = signed_in_client(user)
    UserSession.objects.all().delete()
    client.get(LIST)
    assert UserSession.objects.filter(user=user, session_key=key_of(client)).exists()


def test_a_touch_never_modifies_the_django_session(settings: object) -> None:
    """Review of #491: a touch must not make a staff session rolling (expire_date stays)."""
    settings.SESSION_TOUCH_SECONDS = 0  # type: ignore[attr-defined]
    user = UserFactory.create(staff=True)
    client = Client()
    verified_login(client, user, level=levels.NO_WINDOW)
    before = Session.objects.get(session_key=key_of(client))
    seen = UserSession.objects.get(session_key=key_of(client)).last_seen
    Session.objects.filter(pk=before.pk).update(expire_date=before.expire_date)  # pin it
    pinned = Session.objects.get(pk=before.pk).expire_date
    assert client.get(LIST).status_code == 200
    assert Session.objects.get(pk=before.pk).expire_date == pinned
    assert Session.objects.get(pk=before.pk).session_data == before.session_data
    assert UserSession.objects.get(session_key=key_of(client)).last_seen > seen


def test_a_session_with_no_index_row_is_still_ended() -> None:
    """Review of #491: a session the index never saw (an old worker, a rolled-back row) ends
    through the per-user session generation."""
    user = UserFactory.create()
    keep, unknown = signed_in_client(user), signed_in_client(user)
    UserSession.objects.filter(session_key=key_of(unknown)).delete()
    services.end_sessions(user, keep_session_key=key_of(keep), reason="others", ctx=SYSTEM)
    response = unknown.get(LIST)
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login"))
    assert keep.get(LIST).status_code == 200  # the kept session is still valid


def test_a_session_written_back_after_the_panic_button_is_still_ended() -> None:
    """An old worker that saves a session after the panic button deleted it (no index row)
    gets it flushed on the next request: the generation moved for every user."""
    owner, user = UserFactory.create(platform_role="owner"), UserFactory.create()
    caller, unknown = signed_in_client(owner), signed_in_client(user)
    saved = Session.objects.get(session_key=key_of(unknown))
    services.end_all_sessions(actor=owner, keep_session_key=key_of(caller), ctx=SYSTEM)
    Session.objects.create(
        session_key=saved.session_key,
        session_data=saved.session_data,
        expire_date=saved.expire_date,
    )
    response = unknown.get(LIST)
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login"))
    assert is_signed_in(caller)
    assert not caller.get(LIST)["Location"].startswith(reverse("login"))  # MFA setup, signed in


def test_a_session_signed_in_before_a_bump_is_refused_after_it() -> None:
    """A session that still carries an old generation (a worker that missed the delete) is
    flushed on its next request."""
    user = UserFactory.create()
    client = signed_in_client(user)
    User.objects.filter(pk=user.pk).update(session_generation=user.session_generation + 5)
    assert client.get(LIST).status_code == 302
    assert not Session.objects.filter(session_key=key_of(client)).exists()


# --- end_sessions


def test_end_sessions_keeps_the_named_session_and_audits_the_count() -> None:
    user = UserFactory.create()
    keep, other, third = (signed_in_client(user) for _ in range(3))
    changes = services.end_sessions(
        user, keep_session_key=key_of(keep), reason="others", ctx=self_ctx(user)
    )
    assert not changes.is_empty
    assert is_signed_in(keep)
    assert not is_signed_in(other)
    assert not is_signed_in(third)
    entry = AuditLog.objects.get(action=services.SESSIONS_ENDED)
    assert entry.before == {"sessions": 2, "generation": 0}
    assert entry.after == {"sessions": 0, "generation": 1, "reason": "others", "kept": True}
    assert entry.target_key == str(user.public_id)


def test_end_sessions_dry_run_changes_and_audits_nothing() -> None:
    user = UserFactory.create()
    client = signed_in_client(user)
    changes = services.end_sessions(user, reason="others", ctx=SYSTEM, dry_run=True)
    assert not changes.is_empty
    assert is_signed_in(client)
    assert not AuditLog.objects.filter(action=services.SESSIONS_ENDED).exists()


def test_with_no_indexed_session_the_generation_still_moves_and_is_audited() -> None:
    user = UserFactory.create()
    assert not services.end_sessions(user, reason="others", ctx=SYSTEM).is_empty
    user.refresh_from_db()
    assert user.session_generation == 1
    assert ended_entry()["generation"] == 1


def test_end_sessions_touches_only_that_user() -> None:
    user, other = UserFactory.create(), UserFactory.create()
    client, bystander = signed_in_client(user), signed_in_client(other)
    services.end_sessions(user, reason="others", ctx=SYSTEM)
    assert not is_signed_in(client)
    assert is_signed_in(bystander)


# --- Invalidation on a change (self-service keeps the current session; staff keep none)


def test_a_self_service_password_change_keeps_the_current_session_only() -> None:
    user = UserFactory.create()
    current, other = signed_in_client(user), signed_in_client(user)
    with request_of(current):
        services.record_password_change(user, had_usable_password=True, ctx=self_ctx(user))
    assert is_signed_in(current)
    assert not is_signed_in(other)
    assert ended_entry()["reason"] == "password"


def test_a_password_change_by_staff_ends_every_session() -> None:
    user, staff = UserFactory.create(), UserFactory.create(staff=True)
    first, second = signed_in_client(user), signed_in_client(user)
    with request_of(signed_in_client(staff)):  # staff's own request: not the user's session
        services.record_password_change(user, had_usable_password=True, ctx=self_ctx(staff))
    assert not is_signed_in(first)
    assert not is_signed_in(second)


def test_a_completed_password_reset_ends_every_session() -> None:
    """Review of #491 point 4: the reset path goes through `record_password_change`."""
    user = UserFactory.create()
    client = signed_in_client(user)
    services.complete_password_reset(user, secrets.token_urlsafe(16), ctx=SYSTEM)
    assert not is_signed_in(client)
    assert not UserSession.objects.filter(user=user).exists()
    assert ended_entry()["reason"] == "password"


def test_the_daily_task_clears_expired_sessions_and_their_index_rows() -> None:
    from apps.accounts.tasks import clear_sessions

    user = UserFactory.create()
    live, old = signed_in_client(user), signed_in_client(user)
    past = timezone.now() - dt.timedelta(days=1)
    Session.objects.filter(session_key=key_of(old)).update(expire_date=past)
    clear_sessions.func(timestamp=int(timezone.now().timestamp()))
    assert not Session.objects.filter(session_key=key_of(old)).exists()
    assert not UserSession.objects.filter(session_key=key_of(old)).exists()
    assert UserSession.objects.filter(session_key=key_of(live)).exists()


def test_an_email_change_ends_the_users_other_sessions() -> None:
    victim = UserFactory.create(email="customer@example.com")
    stolen = signed_in_client(victim)
    victim.email = "customer.new@example.com"
    services.save_user(victim, ctx=SYSTEM)
    assert AuditLog.objects.filter(action=services.USER_UPDATED).count() == 1
    assert not is_signed_in(stolen)


def test_a_name_change_keeps_the_sessions() -> None:
    user = UserFactory.create()
    client = signed_in_client(user)
    user.full_name = "New Name"
    services.save_user(user, ctx=SYSTEM)
    assert is_signed_in(client)


def test_a_deactivated_account_loses_every_session() -> None:
    user = UserFactory.create()
    client = signed_in_client(user)
    user.is_active = False
    services.save_user(user, ctx=SYSTEM)
    assert not is_signed_in(client)


def test_a_group_change_from_the_admin_ends_the_sessions() -> None:
    user = UserFactory.create(staff=True)
    client = signed_in_client(user)
    before = services.access_snapshot(user)
    user.user_permissions.add(*Permission.objects.filter(codename="view_user"))
    services.record_access_change(user, before, ctx=SYSTEM)
    assert not is_signed_in(client)
    assert ended_entry()["reason"] == "role"


def test_an_mfa_reset_ends_the_users_sessions() -> None:
    victim = UserFactory.create(staff=True)
    TOTPDeviceFactory.create(user=victim)
    stolen = signed_in_client(victim)
    services.reset_mfa(victim, ctx=SYSTEM)
    assert AuditLog.objects.filter(action=services.DISABLED).count() == 1
    assert stolen.get(reverse("mfa:totp")).status_code != 200
    assert not is_signed_in(stolen)


def test_an_mfa_reset_ends_even_the_session_that_asked_for_it() -> None:
    user = UserFactory.create()
    TOTPDeviceFactory.create(user=user)
    client = signed_in_client(user)
    with request_of(client):
        services.reset_mfa(user, ctx=self_ctx(user))
    assert not Session.objects.filter(session_key=key_of(client)).exists()


def test_a_stolen_session_after_an_mfa_reset_gets_the_login_page_and_no_enrolment() -> None:
    victim = UserFactory.create(staff=True)
    TOTPDeviceFactory.create(user=victim)
    stolen = signed_in_client(victim)
    services.reset_mfa(victim, ctx=SYSTEM)
    response = stolen.get(reverse("mfa:totp"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login"))
    assert not TOTPDevice.objects.filter(user=victim).exists()  # no pending device was made
    page = stolen.get(reverse("mfa:setup"))
    assert page.status_code == 302
    assert page["Location"].startswith(reverse("login"))


def test_enrolling_an_authenticator_app_ends_the_other_sessions_and_keeps_this_one() -> None:
    user = UserFactory.create()
    current, other = signed_in_client(user), signed_in_client(user)
    assert current.get(reverse("mfa:totp")).status_code == 200
    device = TOTPDevice.objects.get(user=user, confirmed=False)
    response = current.post(reverse("mfa:totp"), {"token": totp_token(device)})
    assert response.status_code == 200
    assert TOTPDevice.objects.get(pk=device.pk).confirmed
    assert is_signed_in(current)
    assert not is_signed_in(other)
    assert ended_entry()["reason"] == "mfa_added"


def test_removing_a_factor_ends_the_other_sessions() -> None:
    user = UserFactory.create()
    TOTPDeviceFactory.create(user=user)
    passkey = PasskeyDeviceFactory.create(user=user)
    current, other = signed_in_client(user), signed_in_client(user)
    with request_of(current):
        services.remove_device(passkey, ctx=self_ctx(user))
    assert is_signed_in(current)
    assert not is_signed_in(other)


# --- The account page


def test_the_page_lists_the_users_own_sessions_only() -> None:
    user, other = UserFactory.create(), UserFactory.create()
    client = signed_in_client(user)
    signed_in_client(other)
    UserSessionFactory.create(user=user, browser="Safari", system="iOS")
    UserSessionFactory.create(user=other, browser="Opera", system="Windows")
    page = client.get(LIST)
    assert page.status_code == 200
    on = translated("%(browser)s on %(system)s") % {"browser": "Safari", "system": "iOS"}
    assert on.encode() in page.content
    assert b"Opera" not in page.content
    assert escape(translated("this session")).encode() in page.content


def test_an_anonymous_visitor_is_sent_to_sign_in() -> None:
    response = Client().get(LIST)
    assert response.status_code == 302
    assert "login" in response["Location"]


def test_log_out_everywhere_else_keeps_this_session() -> None:
    user = UserFactory.create()
    current, other = signed_in_client(user), signed_in_client(user)
    response = current.post(reverse("sessions:end_others"))
    assert response.status_code == 302
    assert is_signed_in(current)
    assert not is_signed_in(other)


def test_ending_one_session_ends_only_that_one() -> None:
    user = UserFactory.create()
    current, first, second = (signed_in_client(user) for _ in range(3))
    row = UserSession.objects.get(session_key=key_of(first))
    current.post(reverse("sessions:end_one", kwargs={"public_id": row.public_id}))
    assert is_signed_in(current)
    assert not is_signed_in(first)
    assert is_signed_in(second)
    assert ended_entry()["session"] == str(row.public_id)


def test_ending_the_current_session_signs_the_user_out() -> None:
    user = UserFactory.create()
    current = signed_in_client(user)
    row = UserSession.objects.get(session_key=key_of(current))
    response = current.post(reverse("sessions:end_one", kwargs={"public_id": row.public_id}))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login"))
    assert current.get(LIST).status_code == 302


def test_a_user_cannot_end_another_users_session() -> None:
    user, other = UserFactory.create(), UserFactory.create()
    client, victim = signed_in_client(user), signed_in_client(other)
    row = UserSession.objects.get(session_key=key_of(victim))
    response = client.post(reverse("sessions:end_one", kwargs={"public_id": row.public_id}))
    assert response.status_code == 404
    assert is_signed_in(victim)


@pytest.mark.parametrize("name", ["sessions:end_others", "sessions:end_one"])
def test_the_actions_need_post_and_csrf(name: str) -> None:
    user = UserFactory.create()
    client = signed_in_client(user)
    kwargs = {}
    if name == "sessions:end_one":
        kwargs = {"public_id": UserSession.objects.get(user=user).public_id}
    url = reverse(name, kwargs=kwargs)
    assert client.get(url).status_code == 405
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)
    assert strict.post(url).status_code == 403
    assert is_signed_in(client)


def test_the_backfill_indexes_signed_in_sessions_in_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration 0010 (batched): every live signed-in session of an existing user gets a row;
    anonymous sessions and already indexed ones are skipped."""
    import importlib

    from django.apps import apps as django_apps

    migration = importlib.import_module("apps.accounts.migrations.0010_user_session")
    monkeypatch.setattr(migration, "BATCH", 2)
    users = UserFactory.create_batch(3)
    clients = [signed_in_client(user) for user in users]
    anonymous = SessionStore()
    anonymous["anything"] = 1
    anonymous.save()
    UserSession.objects.filter(user__in=users[:2]).delete()
    migration.index_existing_sessions(django_apps, None)
    for user, client in zip(users, clients, strict=True):
        assert UserSession.objects.filter(user=user, session_key=key_of(client)).exists()
    assert UserSession.objects.count() == 3


def _elevate_group() -> Any:
    from django.contrib.auth.models import Group

    group = Group.objects.create(name=f"elevators-{secrets.token_hex(4)}")
    group.permissions.add(Permission.objects.get(codename=levels.ELEVATE_CODENAME))
    return group


def test_removing_elevate_from_a_group_ends_its_members_sessions_and_windows() -> None:
    """CORE.25: staff who lose `elevate` through their role keep no passkey window."""
    group = _elevate_group()
    user = UserFactory.create(staff=True)
    user.groups.add(group)
    client = Client()
    verified_login(client, user, elevate=False)  # the window is open, the right is the group's
    assert is_signed_in(client)
    before = services.group_access_snapshot(group)
    group.permissions.clear()
    services.record_group_access_change(group, before, ctx=SYSTEM)
    assert not is_signed_in(client)
    assert ended_entry()["reason"] == "role"


def test_deleting_a_group_ends_its_members_sessions() -> None:
    group = _elevate_group()
    user = UserFactory.create(staff=True)
    user.groups.add(group)
    client = signed_in_client(user)
    services.delete_group(group, ctx=SYSTEM)
    assert not is_signed_in(client)


def test_a_group_change_with_no_effect_keeps_the_members_sessions() -> None:
    group = _elevate_group()
    user = UserFactory.create(staff=True)
    user.groups.add(group)
    client = signed_in_client(user)
    services.record_group_access_change(group, services.group_access_snapshot(group), ctx=SYSTEM)
    assert is_signed_in(client)


def test_removing_a_direct_elevate_permission_closes_the_window() -> None:
    user = UserFactory.create(staff=True)
    client = Client()
    verified_login(client, user)  # grants the direct elevate permission and a passkey
    assert is_signed_in(client)
    before = services.access_snapshot(user)
    user.user_permissions.remove(Permission.objects.get(codename=levels.ELEVATE_CODENAME))
    services.record_access_change(user, before, ctx=SYSTEM)
    assert not is_signed_in(client)
