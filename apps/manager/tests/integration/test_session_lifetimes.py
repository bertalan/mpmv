"""CORE.24 part 2: Manager users get the staff session lifetime (idle 30 min, absolute 12 h).

A vendor member holds no staff flag and platform staff need not hold one: both are decided per
request (`apps.accounts.lifetimes.is_strict`). Needs human review: authentication code.
"""

import pytest
from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.models import Session
from django.test import Client

from apps.accounts import levels, lifetimes
from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog

from .conftest import LOGIN, PLATFORM, SHOP

pytestmark = pytest.mark.django_db


def _age(client: Client, seconds: float) -> None:
    session = client.session
    session[lifetimes.SIGNED_IN_KEY] -= seconds
    session[lifetimes.SEEN_KEY] -= seconds
    session.save()


def _client(user: User) -> Client:
    client = Client()
    verified_login(client, user, level=levels.NO_WINDOW)
    return client


def _last_expiry() -> dict[str, object]:
    after = AuditLog.objects.filter(action=lifetimes.SESSION_EXPIRED).latest("id").after
    assert isinstance(after, dict)
    return after


def test_a_vendor_member_is_treated_like_staff_in_the_manager(owner: User) -> None:
    client = _client(owner)
    _age(client, 29 * 60)
    response = client.get(SHOP)
    assert not response.get("Location", "").startswith(LOGIN)
    assert client.session.get(SESSION_KEY)
    key = client.cookies["sessionid"].value
    _age(client, 31 * 60)
    response = client.get(SHOP)
    assert response.status_code == 302
    assert response["Location"].startswith(LOGIN)
    assert not Session.objects.filter(session_key=key).exists()
    assert _last_expiry() == {"reason": "idle", "lifetime": "staff"}


def test_a_vendor_member_session_ends_after_12_hours_even_if_active(owner: User) -> None:
    client = _client(owner)
    session = client.session
    session[lifetimes.SIGNED_IN_KEY] -= 12 * 3600 + 60
    session.save()
    response = client.get(SHOP)
    assert response["Location"].startswith(LOGIN)
    assert _last_expiry() == {"reason": "absolute", "lifetime": "staff"}


def test_platform_staff_get_the_staff_lifetime_in_the_platform_space(
    platform_staff: User,
) -> None:
    assert not platform_staff.is_staff
    client = _client(platform_staff)
    _age(client, 31 * 60)
    response = client.get(PLATFORM)
    assert response["Location"].startswith(LOGIN)
    assert _last_expiry() == {"reason": "idle", "lifetime": "staff"}


def test_a_customer_in_the_manager_keeps_the_customer_lifetime(customer: User) -> None:
    client = Client()
    client.force_login(customer)
    _age(client, 31 * 60)
    client.get(SHOP)
    assert client.session.get(SESSION_KEY)
    assert not AuditLog.objects.filter(action=lifetimes.SESSION_EXPIRED).exists()
