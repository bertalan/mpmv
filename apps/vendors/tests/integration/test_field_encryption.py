"""SEC.3: field-level encryption against the real database.

Done when: a DB dump holds no cleartext data. The dump below reads every table of the schema
with raw SQL (what `pg_dump` or a stolen backup would see), audit log included. Also covered:
key rotation with dry-run and audit, and the loud failure of a wrong or missing key.
"""

import io
from typing import Any

import pytest
from django.conf import settings
from django.core import checks
from django.core.management import call_command
from django.db import connection
from django.test import override_settings

from apps.core.audit import AuditContext
from apps.core.checks import check_encrypted_data
from apps.core.crypto import DecryptionError, generate_key, key_id_of
from apps.core.models import AuditLog
from apps.core.services import FIELD_KEYS_ROTATED, rotate_field_keys
from apps.vendors.models import Vendor
from apps.vendors.services import save_vendor
from apps.vendors.tests.factories import VendorFactory, codice_fiscale, iban

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=None, actor_label="command:test", source="command")


def database_dump() -> str:
    """Every row of every table in the schema, as JSON text, read with raw SQL."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
        tables = [row[0] for row in cursor.fetchall()]
        rows: list[str] = []
        for table in tables:
            quoted = connection.ops.quote_name(table)
            cursor.execute(f"SELECT row_to_json(t)::text FROM {quoted} t")  # noqa: S608
            rows.extend(row[0] for row in cursor.fetchall())
    return "\n".join(rows)


def raw_values(vendor: Vendor) -> dict[str, str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT iban, tax_code FROM vendors_vendor WHERE id = %s", [vendor.pk])
        stored_iban, stored_tax_code = cursor.fetchone()
    return {"iban": stored_iban, "tax_code": stored_tax_code}


def raw_key_ids(vendor: Vendor) -> set[str | None]:
    return {key_id_of(value) for value in raw_values(vendor).values()}


def set_raw(vendor: Vendor, column: str, value: str) -> None:
    with connection.cursor() as cursor:
        quoted = connection.ops.quote_name(column)
        cursor.execute(
            f"UPDATE vendors_vendor SET {quoted} = %s WHERE id = %s",  # noqa: S608
            [value, vendor.pk],
        )


def database_check() -> list[str]:
    messages: list[checks.CheckMessage] = check_encrypted_data(None, databases=["default"])
    return [message.id or "" for message in messages]


def current_key() -> str:
    return str(settings.FIELD_ENCRYPTION_KEYS[0])


# --- Done when: DB dump with no cleartext data


def test_a_database_dump_holds_no_cleartext_iban_or_tax_code() -> None:
    vendor = VendorFactory.create(italian_sole_proprietor=True)
    secrets_written = [vendor.iban, vendor.tax_code]
    # Change both through the audited service, so the audit log is part of the dump too.
    vendor.iban = iban("IT", "X0300003200999999999999")
    vendor.tax_code = codice_fiscale(4242)
    save_vendor(vendor, ctx=CTX)
    secrets_written += [vendor.iban, vendor.tax_code]

    dump = database_dump()
    assert "vendor.updated" in dump  # the audit rows are in the dump
    assert vendor.vat_number in dump  # public in VIES: deliberately not encrypted
    for secret in secrets_written:
        assert secret not in dump

    stored = Vendor.objects.get(pk=vendor.pk)
    assert (stored.iban, stored.tax_code) == (vendor.iban, vendor.tax_code)


def test_stored_values_are_tokens_of_the_primary_key_and_blanks_stay_blank() -> None:
    vendor = VendorFactory.create(italian_sole_proprietor=True)
    assert raw_key_ids(vendor) == {"test"}
    blank = VendorFactory.create(vat_number="")
    assert raw_values(blank) == {"iban": "", "tax_code": ""}
    assert Vendor.objects.filter(iban="").get() == blank


def test_a_tax_code_replaced_with_the_same_mask_is_still_audited() -> None:
    vendor = VendorFactory.create(non_eu_without_vat=True, tax_code="11-1116789")
    vendor.tax_code = "22-2226789"
    save_vendor(vendor, ctx=CTX)
    entry = AuditLog.objects.get(action="vendor.updated")
    assert entry.after is not None
    assert entry.after["tax_code"].endswith("(replaced)")
    assert "2226789" not in str(entry.before) + str(entry.after)


# --- A wrong or missing key fails loudly


def test_the_database_check_passes_with_the_right_key() -> None:
    VendorFactory.create(italian_sole_proprietor=True)
    assert database_check() == []


def test_a_wrong_key_fails_the_database_check_and_every_read() -> None:
    vendor = VendorFactory.create(italian_sole_proprietor=True)
    with override_settings(FIELD_ENCRYPTION_KEYS=[generate_key("test")]):
        assert database_check() == ["core.E304", "core.E304"]
        with pytest.raises(DecryptionError, match=r"vendors\.vendor"):
            Vendor.objects.get(pk=vendor.pk)


def test_a_retired_key_still_in_use_fails_the_database_check() -> None:
    VendorFactory.create(italian_sole_proprietor=True)
    with override_settings(FIELD_ENCRYPTION_KEYS=[generate_key("k2")]):
        assert database_check() == ["core.E303", "core.E303"]


def test_rows_on_an_old_key_or_in_clear_are_reported() -> None:
    vendor = VendorFactory.create(italian_sole_proprietor=True)
    with override_settings(FIELD_ENCRYPTION_KEYS=[generate_key("k2"), current_key()]):
        assert database_check() == ["core.W301", "core.W301"]
    set_raw(vendor, "tax_code", "RSSMRA85T10A562S")
    assert database_check() == ["core.W302"]


def test_the_database_check_needs_a_database() -> None:
    assert check_encrypted_data(None) == []


# --- Key rotation


def test_rotation_re_encrypts_with_the_new_key_audited_without_values() -> None:
    vendor = VendorFactory.create(italian_sole_proprietor=True)
    old, new = current_key(), generate_key("k2")
    with override_settings(FIELD_ENCRYPTION_KEYS=[new, old]):
        preview = rotate_field_keys(ctx=CTX, dry_run=True)
        assert [(c.op, c.model, c.key) for c in preview.changes] == [
            ("update", "vendors.vendor", str(vendor.public_id))
        ]
        assert preview.changes[0].fields == {
            "iban": ("key test", "key k2"),
            "tax_code": ("key test", "key k2"),
        }
        assert raw_key_ids(vendor) == {"test"}
        assert not AuditLog.objects.exists()

        rotate_field_keys(ctx=CTX)
        assert raw_key_ids(vendor) == {"k2"}
        entry = AuditLog.objects.get(action=FIELD_KEYS_ROTATED)
        assert entry.target_key == str(vendor.public_id)
        assert entry.vendor_key == str(vendor.public_id)
        assert vendor.iban not in str(entry.before) + str(entry.after)
        assert vendor.tax_code not in str(entry.before) + str(entry.after)
        assert rotate_field_keys(ctx=CTX).is_empty

    with override_settings(FIELD_ENCRYPTION_KEYS=[new]):  # the old key can now be retired
        stored = Vendor.objects.get(pk=vendor.pk)
        assert (stored.iban, stored.tax_code) == (vendor.iban, vendor.tax_code)
        assert database_check() == []


def test_rotation_encrypts_values_found_in_clear() -> None:
    vendor = VendorFactory.create(italian_sole_proprietor=True)
    set_raw(vendor, "iban", vendor.iban)
    changes = rotate_field_keys(ctx=CTX)
    assert changes.changes[0].fields == {"iban": ("cleartext", "key test")}
    assert raw_key_ids(vendor) == {"test"}
    assert Vendor.objects.get(pk=vendor.pk).iban == vendor.iban


def test_rotation_with_a_wrong_key_changes_nothing() -> None:
    vendor = VendorFactory.create(italian_sole_proprietor=True)
    before = raw_values(vendor)
    with (
        override_settings(FIELD_ENCRYPTION_KEYS=[generate_key("k2"), generate_key("test")]),
        pytest.raises(DecryptionError),
    ):
        rotate_field_keys(ctx=CTX)
    assert raw_values(vendor) == before
    assert not AuditLog.objects.exists()


def run(*args: Any) -> str:
    out = io.StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


def test_the_rotation_command_previews_then_applies() -> None:
    vendor = VendorFactory.create(italian_sole_proprietor=True)
    with override_settings(FIELD_ENCRYPTION_KEYS=[generate_key("k2"), current_key()]):
        preview = run("rotate_field_keys", "--dry-run")
        assert "Would re-encrypt 1 object(s) with key k2" in preview
        assert raw_key_ids(vendor) == {"test"}
        assert "Re-encrypted 1 object(s) with key k2" in run("rotate_field_keys")
        assert raw_key_ids(vendor) == {"k2"}
    entry = AuditLog.objects.get(action=FIELD_KEYS_ROTATED)
    assert entry.source == AuditLog.Source.COMMAND
    assert vendor.iban not in preview
