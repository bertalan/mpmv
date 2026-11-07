# SEC.3: the IBAN and the tax code become encrypted at rest. The columns turn into `text` (a
# token is longer than the value), then the values stored in clear are encrypted in place with the
# primary field encryption key. Reversible: going back decrypts them.

from typing import Any

import apps.core.fields
from django.db import migrations

from apps.core.encryption import decrypt_column, encrypt_column

TABLE = "vendors_vendor"
COLUMNS = ("iban", "tax_code")


def encrypt(apps: Any, schema_editor: Any) -> None:
    for column in COLUMNS:
        encrypt_column(schema_editor.connection, TABLE, column, f"vendors.vendor.{column}")


def decrypt(apps: Any, schema_editor: Any) -> None:
    for column in COLUMNS:
        decrypt_column(schema_editor.connection, TABLE, column, f"vendors.vendor.{column}")


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
