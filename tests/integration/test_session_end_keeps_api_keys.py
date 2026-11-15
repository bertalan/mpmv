"""CORE.24: API keys are not sessions. Ending a user's sessions, Support's action and the platform
owner's "End every session" leave every API key valid; revoking keys is a separate action.

Here and not in the apps: the check needs both accounts and api (the layers forbid the import
from either app's own tests). Needs human review: authentication code.
"""

import pytest
from django.contrib.auth import SESSION_KEY
from django.test import Client

from apps.accounts import services
from apps.accounts.tests.factories import UserFactory
from apps.api.models import ApiClient
from apps.api.tests.factories import ApiClientFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="test", source=AuditLog.Source.ADMIN)


def _signed_in(user: object) -> Client:
    client = Client()
    client.force_login(user)  # type: ignore[arg-type]
    return client


def _still_valid(key: ApiClient) -> bool:
    key.refresh_from_db()
    return key.is_active and key.revoked_at is None


def test_ending_one_users_sessions_leaves_the_api_keys() -> None:
    support, target = UserFactory.create(platform_role="support"), UserFactory.create()
    client, key = _signed_in(target), ApiClientFactory.create()
    services.support_end_sessions(target, actor=support, reason="Lost laptop", ctx=CTX)
    assert not client.session.get(SESSION_KEY)
    assert _still_valid(key)


def test_the_panic_button_leaves_the_api_keys() -> None:
    owner = UserFactory.create(platform_role="owner")
    client, key = _signed_in(UserFactory.create()), ApiClientFactory.create()
    services.end_all_sessions(actor=owner, keep_session_key=None, ctx=CTX)
    assert not client.session.get(SESSION_KEY)
    assert _still_valid(key)
