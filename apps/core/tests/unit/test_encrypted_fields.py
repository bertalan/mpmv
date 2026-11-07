"""SEC.3: the reusable encrypted model fields and the key configuration check, without a database.

Models are declared in an isolated app registry; keys are generated at runtime.
"""

import io
from typing import Any

import pytest
from django.core import checks
from django.core.exceptions import FieldError, ImproperlyConfigured, ValidationError
from django.core.management import call_command
from django.db import models
from django.test import override_settings
from django.test.utils import isolate_apps

from apps.core.checks import check_field_encryption_keys
from apps.core.crypto import DecryptionError, generate_key, key_id_of, parse_keys
from apps.core.encryption import configured_keyring
from apps.core.fields import EncryptedCharField, EncryptedTextField


def ids(messages: list[checks.CheckMessage]) -> list[str]:
    return [message.id or "" for message in messages]


def secret_model() -> Any:
    class Secret(models.Model):  # noqa: DJ008
        account = EncryptedCharField(max_length=34, blank=True)
        notes = EncryptedTextField(blank=True, null=True)

        class Meta:
            app_label = "core"

    return Secret


@isolate_apps("apps.core")
def test_values_are_encrypted_for_the_database_and_decrypted_back() -> None:
    field = secret_model()._meta.get_field("account")
    stored = field.get_prep_value("IT60X0542811101000000123456")
    assert "123456" not in stored
    assert key_id_of(stored) == configured_keyring().primary_id
    assert field.from_db_value(stored, None, None) == "IT60X0542811101000000123456"


@isolate_apps("apps.core")
def test_the_column_is_text_whatever_the_plaintext_length() -> None:
    field = secret_model()._meta.get_field("account")
    assert field.get_internal_type() == "TextField"
    assert field.max_length == 34  # still validates the plaintext


@isolate_apps("apps.core")
def test_the_plaintext_length_is_validated() -> None:
    field = secret_model()._meta.get_field("account")
    with pytest.raises(ValidationError):
        field.clean("X" * 35, None)


@isolate_apps("apps.core")
def test_empty_and_null_values_stay_as_they_are() -> None:
    model = secret_model()
    account, notes = model._meta.get_field("account"), model._meta.get_field("notes")
    assert account.get_prep_value("") == ""
    assert account.from_db_value("", None, None) == ""
    assert notes.get_prep_value(None) is None
    assert notes.from_db_value(None, None, None) is None


@isolate_apps("apps.core")
def test_a_value_bound_to_another_column_does_not_decrypt() -> None:
    model = secret_model()
    stored = model._meta.get_field("account").get_prep_value("secret")
    with pytest.raises(DecryptionError, match=r"core\.secret\.notes"):
        model._meta.get_field("notes").from_db_value(stored, None, None)


@isolate_apps("apps.core")
def test_cleartext_in_the_database_fails_loudly() -> None:
    field = secret_model()._meta.get_field("account")
    with pytest.raises(DecryptionError, match=r"core\.secret\.account"):
        field.from_db_value("IT60X0542811101000000123456", None, None)


@isolate_apps("apps.core")
def test_searching_an_encrypted_field_is_refused() -> None:
    model = secret_model()
    with pytest.raises(FieldError, match="encrypted"):
        model.objects.filter(account="IT60X0542811101000000123456")
    with pytest.raises(FieldError):
        model.objects.filter(account__icontains="1234")
    with pytest.raises(FieldError):
        model.objects.filter(account__in=["a", "b"])
    with pytest.raises(FieldError):
        model.objects.filter(account__upper="A")


@isolate_apps("apps.core")
def test_empty_and_null_lookups_are_allowed() -> None:
    model = secret_model()
    assert "account" in str(model.objects.filter(account="").query)
    assert "IS NULL" in str(model.objects.filter(notes__isnull=True).query)


@isolate_apps("apps.core")
@pytest.mark.parametrize("ordering", ["account", "-notes", "pk"])
def test_ordering_by_an_encrypted_field_is_refused(ordering: str) -> None:
    model = secret_model()
    query = model.objects.order_by("pk", ordering).query
    if ordering == "pk":
        assert "ORDER BY" in str(query)
        return
    with pytest.raises(FieldError, match="encrypted"):
        str(query)  # the ciphertext order is random: sorting by it would mislead


@isolate_apps("apps.core")
def test_an_encrypted_field_in_the_default_ordering_is_refused() -> None:
    class Sorted(models.Model):  # noqa: DJ008
        account = EncryptedCharField(max_length=34, blank=True)

        class Meta:
            app_label = "core"
            ordering = ["account"]

    with pytest.raises(FieldError, match="encrypted"):
        str(Sorted.objects.all().query)


@isolate_apps("apps.core")
def test_empty_and_null_lookups_still_work_in_a_subquery() -> None:
    model = secret_model()
    inner = model.objects.filter(account="", notes__isnull=True).values("pk")
    sql = str(model.objects.filter(models.Exists(inner)).query)
    assert "EXISTS" in sql
    assert "IS NULL" in sql
    sql = str(model.objects.filter(pk__in=inner).query)
    assert "IN (SELECT" in sql


@isolate_apps("apps.core")
def test_selecting_an_encrypted_field_still_works() -> None:
    model = secret_model()
    assert "account" in str(model.objects.values("account").query)
    assert "account" in str(model.objects.only("account").query)


@isolate_apps("apps.core")
def test_the_field_deconstructs_for_migrations() -> None:
    field = secret_model()._meta.get_field("account")
    _, path, _, kwargs = field.deconstruct()
    assert path == "apps.core.fields.EncryptedCharField"
    assert kwargs["max_length"] == 34


@override_settings(FIELD_ENCRYPTION_KEYS=[])
def test_missing_keys_fail_the_system_check_and_any_use() -> None:
    assert ids(check_field_encryption_keys(None)) == ["core.E301"]
    with pytest.raises(ImproperlyConfigured, match="MPMV_FIELD_ENCRYPTION_KEYS"):
        configured_keyring()


def test_malformed_keys_fail_the_system_check_without_showing_them() -> None:
    bad = "k1:" + "A" * 10
    with override_settings(FIELD_ENCRYPTION_KEYS=[bad]):
        messages = check_field_encryption_keys(None)
    assert ids(messages) == ["core.E302"]
    assert "A" * 10 not in str(messages[0])


def test_valid_keys_pass_the_system_check() -> None:
    with override_settings(FIELD_ENCRYPTION_KEYS=[generate_key("k2"), generate_key("k1")]):
        assert check_field_encryption_keys(None) == []
        assert configured_keyring().primary_id == "k2"


def test_the_test_settings_generate_a_key_at_runtime() -> None:
    assert configured_keyring().primary_id == "test"


def test_the_key_command_prints_a_new_valid_key() -> None:
    out = io.StringIO()
    call_command("generate_field_key", "k3", stdout=out)
    assert parse_keys([out.getvalue().strip()]).primary_id == "k3"
