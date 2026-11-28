"""ING.14: the vendors' AI keys have a key ring of their own and are bound to their vendor.

Lane-a security review of #1160: a dedicated key-encryption key (spec 08, spec 12 SEC.3), with its
own rotation and a system check, and the ciphertext bound to the vendor id (AAD), so a token copied
to another vendor's row does not decrypt.
"""

import io
from typing import Any

import pytest
from django.conf import settings
from django.core import checks
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import override_settings

from apps.core.audit import AuditContext
from apps.core.checks import check_encrypted_data, check_field_encryption_keys
from apps.core.crypto import DecryptionError, generate_key, key_id_of
from apps.core.encryption import AI_KEYS_SETTING, KEYS_SETTING, configured_keyring
from apps.core.models import AuditLog
from apps.core.services import rotate_field_keys
from apps.ingest.models import VendorAIProvider
from apps.ingest.tests.factories import VendorAIProviderFactory
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_id=None, actor_label="command:test", source="command")


def raw(row: VendorAIProvider) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT api_key FROM ingest_vendoraiprovider WHERE id = %s", [row.pk])
        found = cursor.fetchone()
    assert found is not None
    return str(found[0])


def set_raw(row: VendorAIProvider, token: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE ingest_vendoraiprovider SET api_key = %s WHERE id = %s", [token, row.pk]
        )


def ai_check() -> list[str]:
    messages: list[checks.CheckMessage] = check_encrypted_data(None, databases=["default"])
    return [message.id or "" for message in messages]


def run(*args: Any) -> str:
    out = io.StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


def ai_key() -> str:
    return str(settings.AI_KEY_ENCRYPTION_KEYS[0])


# --- A key ring of its own


def test_the_key_is_encrypted_with_the_ai_keyring_not_the_field_keys() -> None:
    row = VendorAIProviderFactory.create()
    token = raw(row)
    assert key_id_of(token) == configured_keyring(AI_KEYS_SETTING).primary_id == "test-ai"
    assert configured_keyring(KEYS_SETTING).primary_id == "test"
    context = f"ingest.vendoraiprovider.api_key:{row.vendor_id}"
    assert configured_keyring(AI_KEYS_SETTING).decrypt(token, context=context) == row.api_key
    with (
        override_settings(AI_KEY_ENCRYPTION_KEYS=[generate_key("test-ai")]),
        pytest.raises(DecryptionError),  # another AI key does not read it
    ):
        VendorAIProvider.objects.get(pk=row.pk).api_key  # noqa: B018


def test_the_field_keys_do_not_decrypt_the_vendor_key() -> None:
    row = VendorAIProviderFactory.create()
    with override_settings(AI_KEY_ENCRYPTION_KEYS=[], FIELD_ENCRYPTION_KEYS=[generate_key("x")]):
        assert VendorAIProvider.objects.get(pk=row.pk).has_key  # a raw read: no keyring needed
    with (
        override_settings(AI_KEY_ENCRYPTION_KEYS=list(settings.FIELD_ENCRYPTION_KEYS)),
        pytest.raises(DecryptionError),
    ):
        VendorAIProvider.objects.get(pk=row.pk).api_key  # noqa: B018


# --- The ciphertext is bound to its vendor (AAD)


def test_a_ciphertext_copied_to_another_vendors_row_does_not_decrypt() -> None:
    victim = VendorAIProviderFactory.create()
    thief = VendorAIProviderFactory.create()
    set_raw(thief, raw(victim))
    with pytest.raises(DecryptionError):
        VendorAIProvider.objects.get(pk=thief.pk).api_key  # noqa: B018
    assert VendorAIProvider.objects.get(pk=victim.pk).api_key == victim.api_key


def test_a_row_moved_to_another_vendor_does_not_decrypt() -> None:
    row = VendorAIProviderFactory.create()
    other = VendorFactory.create()
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE ingest_vendoraiprovider SET vendor_id = %s WHERE id = %s", [other.pk, row.pk]
        )
    with pytest.raises(DecryptionError):
        VendorAIProvider.objects.get(pk=row.pk).api_key  # noqa: B018


def test_saving_a_row_that_did_not_change_the_key_keeps_the_token() -> None:
    row = VendorAIProviderFactory.create()
    token = raw(row)
    loaded = VendorAIProvider.objects.get(pk=row.pk)
    loaded.key_version += 1
    loaded.save()
    assert raw(loaded) == token
    assert VendorAIProvider.objects.get(pk=row.pk).api_key == row.api_key


def test_a_plaintext_cannot_be_written_around_the_row() -> None:
    row = VendorAIProviderFactory.create()
    with pytest.raises(ValueError, match="bound to its vendor"):
        VendorAIProvider.objects.filter(pk=row.pk).update(api_key="a-plain-looking-value")


# --- Rotation of its own


def test_the_ai_keyring_rotates_on_its_own_and_the_field_rotation_ignores_it() -> None:
    row = VendorAIProviderFactory.create()
    old, new = ai_key(), generate_key("ai2")
    with override_settings(AI_KEY_ENCRYPTION_KEYS=[new, old]):
        assert rotate_field_keys(ctx=CTX).is_empty  # the SEC.3 rotation: not these columns
        preview = rotate_field_keys(ctx=CTX, dry_run=True, keyring=AI_KEYS_SETTING)
        assert [(c.model, c.fields) for c in preview.changes] == [
            ("ingest.vendoraiprovider", {"api_key": ("key test-ai", "key ai2")})
        ]
        assert key_id_of(raw(row)) == "test-ai"
        rotate_field_keys(ctx=CTX, keyring=AI_KEYS_SETTING)
        assert key_id_of(raw(row)) == "ai2"
        assert rotate_field_keys(ctx=CTX, keyring=AI_KEYS_SETTING).is_empty
        entry = AuditLog.objects.get(action="core.field_keys.rotated")
        assert row.api_key not in str(entry.before) + str(entry.after)
    with override_settings(AI_KEY_ENCRYPTION_KEYS=[new]):  # the old key can be retired
        assert VendorAIProvider.objects.get(pk=row.pk).api_key == row.api_key
        assert ai_check() == []


def test_the_rotation_command_takes_the_keyring() -> None:
    row = VendorAIProviderFactory.create()
    with override_settings(AI_KEY_ENCRYPTION_KEYS=[generate_key("ai2"), ai_key()]):
        assert "Would re-encrypt 1 object(s) with key ai2" in run(
            "rotate_field_keys", "--keyring", "ai", "--dry-run"
        )
        assert "Re-encrypted 0 object(s)" in run("rotate_field_keys")
        assert "Re-encrypted 1 object(s) with key ai2" in run(
            "rotate_field_keys", "--keyring", "ai"
        )
    assert key_id_of(raw(row)) == "ai2"


def test_verify_encrypted_data_reads_the_bound_keys() -> None:
    row = VendorAIProviderFactory.create()
    assert "ingest.vendoraiprovider.api_key: 1 value(s) decrypt" in run("verify_encrypted_data")
    set_raw(row, raw(VendorAIProviderFactory.create()))
    with pytest.raises(CommandError, match=r"vendoraiprovider\.api_key: 1 value"):
        run("verify_encrypted_data")


# --- The system check


def test_a_stored_vendor_key_without_the_keyring_is_a_system_error() -> None:
    VendorAIProviderFactory.create()
    assert ai_check() == []
    with override_settings(AI_KEY_ENCRYPTION_KEYS=[]):
        assert ai_check() == ["core.E305"]


def test_no_stored_key_needs_no_keyring() -> None:
    VendorAIProviderFactory.create(api_key="")
    with override_settings(AI_KEY_ENCRYPTION_KEYS=[]):
        assert ai_check() == []
        assert check_field_encryption_keys(None) == []


def test_a_malformed_ai_keyring_is_an_error_whatever_is_stored() -> None:
    with override_settings(AI_KEY_ENCRYPTION_KEYS=["not-a-key"]):
        assert [m.id for m in check_field_encryption_keys(None)] == ["core.E306"]


def test_the_check_decrypts_the_sample_with_the_vendor_of_its_row() -> None:
    VendorAIProviderFactory.create()
    VendorAIProviderFactory.create()
    assert ai_check() == []  # fails if the sample were decrypted with another row's vendor id
