# SEC.3: the IBAN and the tax code become encrypted at rest. The columns turn into `text` (a
# token is longer than the value), then the values stored in clear are encrypted in place with the
# primary field encryption key. Reversible: going back decrypts them.

from collections.abc import Callable
from typing import Any

import apps.core.fields
from django.conf import settings
from django.db import migrations

from apps.core import crypto

# The rewrite is frozen here, as it was when this migration was written: later changes to
# `apps.core.encryption` (e.g. what the associated data binds) must not change what it does.
# Only the token format (`apps.core.crypto`, versioned by its `enc1` prefix) is shared.
COLUMNS = ("iban", "tax_code")


def _context(column: str) -> str:
    return f"vendors.vendor.{column}"  # the associated data of SEC.3: the column


def _rewrite(
    apps: Any, schema_editor: Any, convert: Callable[[crypto.Keyring, str, str], str | None]
) -> None:
    """Apply `convert(keyring, value, context)` to every non-empty value; None leaves it alone."""
    keyring = crypto.parse_keys(settings.FIELD_ENCRYPTION_KEYS)
    model = apps.get_model("vendors", "Vendor")
    connection = schema_editor.connection
    quote = connection.ops.quote_name
    table, pk = quote(model._meta.db_table), quote(model._meta.pk.column)
    with connection.cursor() as cursor:
        for column in COLUMNS:
            name = quote(column)
            cursor.execute(
                f"SELECT {pk}, {name} FROM {table} WHERE {name} <> '' FOR UPDATE"  # noqa: S608
            )
            for key, value in cursor.fetchall():
                new = convert(keyring, value, _context(column))
                if new is not None:
                    cursor.execute(
                        f"UPDATE {table} SET {name} = %s WHERE {pk} = %s",  # noqa: S608
                        [new, key],
                    )


def encrypt(apps: Any, schema_editor: Any) -> None:
    _rewrite(
        apps,
        schema_editor,
        lambda keyring, value, context: (
            None if crypto.is_encrypted(value) else keyring.encrypt(value, context=context)
        ),
    )


def decrypt(apps: Any, schema_editor: Any) -> None:
    _rewrite(
        apps,
        schema_editor,
        lambda keyring, value, context: (
            keyring.decrypt(value, context=context) if crypto.is_encrypted(value) else None
        ),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('vendors', '0007_seed_dac7_requirements'),
    ]

    operations = [
        migrations.AlterField(
            model_name='vendor',
            name='iban',
            field=apps.core.fields.EncryptedCharField(blank=True, max_length=34, verbose_name='IBAN'),
        ),
        migrations.AlterField(
            model_name='vendor',
            name='tax_code',
            field=apps.core.fields.EncryptedCharField(blank=True, help_text='Italy: 16 characters, or 11 digits for entities. Elsewhere: the national TIN.', max_length=32, verbose_name='tax code (codice fiscale) or TIN'),
        ),
        migrations.RunPython(encrypt, decrypt),
    ]
