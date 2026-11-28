"""ING.14 2/3: a vendor's AI provider choice and its encrypted, write-only key."""

import json
import logging
import secrets
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.ingest import provider_services as svc
from apps.ingest.models import VendorAIProvider
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="user:test", source="vendor", ip="127.0.0.1")


def owner_of(vendor: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor, role="owner").user


def raw_column(row: VendorAIProvider) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT api_key FROM ingest_vendoraiprovider WHERE id = %s", [row.pk])
        found = cursor.fetchone()
    assert found is not None
    return str(found[0])


def audit_text() -> str:
    return json.dumps(
        [
            [r.action, r.target_model, r.target_key, r.before, r.after]
            for r in AuditLog.objects.filter(action__startswith="ingest.provider")
        ]
    )


def test_setting_a_key_stores_it_encrypted_and_audits_without_the_value() -> None:
    vendor = VendorFactory.create()
    key = secrets.token_urlsafe(24)
    changes = svc.save_provider(
        vendor,
        provider="claude",
        model="m-1",
        api_key=key,
        actor=owner_of(vendor),
        ctx=CTX,
        passkey_confirmed=True,
    )
    row = VendorAIProvider.objects.get(vendor=vendor)
    assert row.api_key == key  # the model decrypts for the vendor's own jobs
    assert key not in raw_column(row)
    assert raw_column(row)
    assert (row.provider, row.model, row.key_version) == ("claude", "m-1", 1)
    assert row.has_key
    assert key not in json.dumps(changes.to_dict())
    entries = list(AuditLog.objects.filter(action="ingest.provider.key_set"))
    assert len(entries) == 1
    assert (entries[0].after or {}).get("has_key") is True
    assert key not in audit_text()


def test_rotation_audits_key_rotated_and_keeps_only_the_new_key() -> None:
    vendor = VendorFactory.create()
    actor = owner_of(vendor)
    old, new = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    svc.save_provider(
        vendor,
        provider="openai",
        model="m-1",
        api_key=old,
        actor=actor,
        ctx=CTX,
        passkey_confirmed=True,
    )
    svc.save_provider(
        vendor,
        provider="openai",
        model="m-1",
        api_key=new,
        actor=actor,
        ctx=CTX,
        passkey_confirmed=True,
    )
    row = VendorAIProvider.objects.get(vendor=vendor)
    assert row.api_key == new
    assert row.key_version == 2
    assert AuditLog.objects.filter(action="ingest.provider.key_rotated").count() == 1
    assert old not in audit_text()
    assert new not in audit_text()
    assert old not in raw_column(row)


def test_a_blank_key_keeps_the_stored_one_and_remove_deletes_it() -> None:
    vendor = VendorFactory.create()
    actor = owner_of(vendor)
    key = secrets.token_urlsafe(24)
    svc.save_provider(
        vendor,
        provider="gemini",
        model="m-1",
        api_key=key,
        actor=actor,
        ctx=CTX,
        passkey_confirmed=True,
    )
    svc.save_provider(
        vendor,
        provider="gemini",
        model="m-2",
        api_key="",
        actor=actor,
        ctx=CTX,
        passkey_confirmed=True,
    )
    row = VendorAIProvider.objects.get(vendor=vendor)
    assert (row.provider, row.model, row.api_key) == ("gemini", "m-2", key)
    assert AuditLog.objects.filter(action="ingest.provider.saved").count() == 1
    svc.save_provider(
        vendor,
        provider="gemini",
        model="m-2",
        remove_key=True,
        actor=actor,
        ctx=CTX,
        passkey_confirmed=True,
    )
    row.refresh_from_db()
    assert not row.has_key
    assert AuditLog.objects.filter(action="ingest.provider.key_removed").count() == 1
    assert key not in audit_text()


def test_a_provider_change_without_a_new_key_clears_the_old_key() -> None:
    vendor = VendorFactory.create()
    actor = owner_of(vendor)
    key = secrets.token_urlsafe(24)
    svc.save_provider(
        vendor,
        provider="gemini",
        model="m-1",
        api_key=key,
        actor=actor,
        ctx=CTX,
        passkey_confirmed=True,
    )
    svc.save_provider(
        vendor,
        provider="claude",
        model="m-2",
        api_key="",
        actor=actor,
        ctx=CTX,
        passkey_confirmed=True,
    )
    row = VendorAIProvider.objects.get(vendor=vendor)
    assert (row.provider, row.has_key, row.api_key) == ("claude", False, "")
    assert svc.key_for(vendor) is None
    assert AuditLog.objects.filter(action="ingest.provider.key_removed").count() == 1
    assert key not in raw_column(row)
    assert key not in audit_text()


def test_a_provider_change_with_a_new_key_keeps_only_the_new_key() -> None:
    vendor = VendorFactory.create()
    actor = owner_of(vendor)
    old, new = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    svc.save_provider(
        vendor,
        provider="gemini",
        model="m-1",
        api_key=old,
        actor=actor,
        ctx=CTX,
        passkey_confirmed=True,
    )
    svc.save_provider(
        vendor,
        provider="claude",
        model="m-2",
        api_key=new,
        actor=actor,
        ctx=CTX,
        passkey_confirmed=True,
    )
    assert svc.key_for(vendor) == new


def test_the_service_refuses_a_save_without_a_passkey_confirmation() -> None:
    vendor = VendorFactory.create()
    with pytest.raises(PermissionDenied):
        svc.save_provider(
            vendor,
            provider="claude",
            model="m-1",
            api_key=secrets.token_urlsafe(24),
            actor=owner_of(vendor),
            ctx=CTX,
            passkey_confirmed=False,
        )
    assert not VendorAIProvider.objects.exists()
    assert not AuditLog.objects.filter(action__startswith="ingest.provider").exists()


def test_dry_run_changes_and_audits_nothing() -> None:
    vendor = VendorFactory.create()
    key = secrets.token_urlsafe(24)
    changes = svc.save_provider(
        vendor,
        provider="claude",
        model="m-1",
        api_key=key,
        actor=owner_of(vendor),
        ctx=CTX,
        passkey_confirmed=True,
        dry_run=True,
    )
    assert not changes.is_empty
    assert key not in json.dumps(changes.to_dict())
    assert not VendorAIProvider.objects.exists()
    assert not AuditLog.objects.filter(action__startswith="ingest.provider").exists()


def test_only_the_owner_may_set_it_and_only_for_its_own_vendor() -> None:
    vendor, other = VendorFactory.create(), VendorFactory.create()
    staff_member = VendorMemberFactory.create(vendor=vendor, role="staff").user
    with pytest.raises(PermissionDenied):
        svc.save_provider(
            vendor,
            provider="claude",
            model="m-1",
            api_key="x" * 12,
            actor=staff_member,
            ctx=CTX,
            passkey_confirmed=True,
        )
    with pytest.raises(PermissionDenied):
        svc.save_provider(
            other,
            provider="claude",
            model="m-1",
            api_key="x" * 12,
            actor=owner_of(vendor),
            ctx=CTX,
            passkey_confirmed=True,
        )
    assert not VendorAIProvider.objects.exists()


def test_a_vendor_cannot_reach_another_vendors_key() -> None:
    a, b = VendorFactory.create(), VendorFactory.create()
    key = secrets.token_urlsafe(24)
    svc.save_provider(
        a,
        provider="claude",
        model="m-1",
        api_key=key,
        actor=owner_of(a),
        ctx=CTX,
        passkey_confirmed=True,
    )
    assert svc.key_for(b) is None
    assert svc.key_for(a) == key


@pytest.mark.parametrize(
    "kwargs",
    [
        {"provider": "other", "model": "m-1"},
        {"provider": "claude", "model": "../x?y"},
        {"provider": "claude", "model": "m-1", "api_key": "short"},
        {"provider": "claude", "model": "m-1", "api_key": "has space in the key"},
    ],
)
def test_invalid_input_is_refused_without_echoing_the_key(kwargs: dict[str, Any]) -> None:
    vendor = VendorFactory.create()
    with pytest.raises(ValidationError) as info:
        svc.save_provider(vendor, actor=owner_of(vendor), ctx=CTX, passkey_confirmed=True, **kwargs)
    assert kwargs.get("api_key", "\0") not in str(info.value)
    assert not VendorAIProvider.objects.exists()


def test_the_key_never_appears_in_repr_str_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    vendor = VendorFactory.create()
    key = secrets.token_urlsafe(24)
    with caplog.at_level(logging.DEBUG):
        svc.save_provider(
            vendor,
            provider="claude",
            model="m-1",
            api_key=key,
            actor=owner_of(vendor),
            ctx=CTX,
            passkey_confirmed=True,
        )
        row = VendorAIProvider.objects.get(vendor=vendor)
        svc.key_for(vendor)
    assert key not in repr(row)
    assert key not in str(row)
    assert key not in caplog.text


def test_the_first_save_locks_the_vendor_so_two_first_saves_cannot_clash() -> None:
    vendor = VendorFactory.create()
    with CaptureQueriesContext(connection) as queries:
        svc.save_provider(
            vendor,
            provider="claude",
            model="m-1",
            actor=owner_of(vendor),
            ctx=CTX,
            passkey_confirmed=True,
        )
    locks = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"]]
    assert any('"vendors_vendor"' in sql for sql in locks)
