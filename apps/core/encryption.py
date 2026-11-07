"""Field-level encryption plumbing (SEC.3): the configured keys and raw access to the encrypted
columns, for the system checks, key rotation and verification.

Data migrations do not import these helpers: each one freezes its own copy (see
`apps/vendors/migrations/0008_encrypt_iban_and_tax_code.py`), so a later change here cannot
change what an old migration does.

The cipher itself is `apps.core.crypto` (pure); the model fields are
`apps.core.fields.EncryptedCharField` / `EncryptedTextField`. Raw SQL is used on purpose: the
ORM always decrypts, while these helpers need to see which key (if any) each stored value uses.
Table and column names come from model metadata and are quoted; values are parameters.
"""

import functools
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.backends.base.base import BaseDatabaseWrapper

from . import crypto

KEYS_SETTING = "FIELD_ENCRYPTION_KEYS"
KEYS_HELP = "Set MPMV_FIELD_ENCRYPTION_KEYS or MPMV_FIELD_ENCRYPTION_KEYS_FILE (SEC.3)."
CLEARTEXT = ""  # the "key id" of a value stored in clear


def configured_keyring() -> crypto.Keyring:
    """The keyring from `settings.FIELD_ENCRYPTION_KEYS`; ImproperlyConfigured if unusable."""
    return _keyring(tuple(getattr(settings, KEYS_SETTING, ())))


@functools.lru_cache(maxsize=8)
def _keyring(entries: tuple[str, ...]) -> crypto.Keyring:
    try:
        return crypto.parse_keys(entries)
    except crypto.KeyConfigurationError as exc:
        raise ImproperlyConfigured(f"{exc} {KEYS_HELP}") from None


def encryption_context(model: type[models.Model], field_name: str) -> str:
    """The associated data that binds a token to its column: `app_label.model.field`."""
    return f"{model._meta.label_lower}.{field_name}"


@dataclass(frozen=True)
class EncryptedColumn:
    model: type[models.Model]
    field: models.Field[Any, Any]

    @property
    def context(self) -> str:
        return encryption_context(self.model, self.field.name)

    @property
    def column(self) -> str:
        return str(self.field.column)


def encrypted_columns() -> Iterator[EncryptedColumn]:
    """Every encrypted field of every installed concrete model."""
    from .fields import EncryptedFieldMixin

    for model in apps.get_models():
        if model._meta.proxy or not model._meta.managed:
            continue
        for model_field in model._meta.concrete_fields:
            if isinstance(model_field, EncryptedFieldMixin):
                yield EncryptedColumn(model, model_field)


def _names(connection: BaseDatabaseWrapper, table: str, column: str) -> tuple[str, str]:
    return connection.ops.quote_name(table), connection.ops.quote_name(column)


def key_usage(connection: BaseDatabaseWrapper, table: str, column: str) -> dict[str, list[Any]]:
    """{key id: [row count, one sample token]} for the non-empty values of a column.

    Values in clear are counted under `CLEARTEXT`, with no sample (it would be the cleartext).
    """
    quoted_table, quoted_column = _names(connection, table, column)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT CASE WHEN {quoted_column} LIKE %s THEN split_part({quoted_column}, ':', 2)"  # noqa: S608
            f" ELSE '' END AS key_id, count(*),"
            f" min(CASE WHEN {quoted_column} LIKE %s THEN {quoted_column} END)"
            f" FROM {quoted_table} WHERE {quoted_column} <> '' GROUP BY 1 ORDER BY 1",
            [f"{crypto.PREFIX}:%", f"{crypto.PREFIX}:%"],
        )
        return {key_id: [count, sample] for key_id, count, sample in cursor.fetchall()}


def stale_rows(
    connection: BaseDatabaseWrapper, model: type[models.Model], columns: list[EncryptedColumn]
) -> list[tuple[Any, str, dict[str, str]]]:
    """Rows with a value in clear or under an older key, locked: [(pk, audit key, {field: raw})].

    The audit key is the public id when the model has one, else the primary key.
    """
    primary = configured_keyring().primary_id
    table = model._meta.db_table
    pk_column = str(model._meta.pk.column) if model._meta.pk else "id"
    selected = [pk_column, _row_key_column(model)]
    selected += [column.column for column in columns]
    quoted = ", ".join(connection.ops.quote_name(name) for name in selected)
    conditions = " OR ".join(
        f"({name} <> '' AND ({name} NOT LIKE %s OR split_part({name}, ':', 2) <> %s))"
        for name in (connection.ops.quote_name(column.column) for column in columns)
    )
    params = [f"{crypto.PREFIX}:%", primary] * len(columns)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {quoted} FROM {connection.ops.quote_name(table)}"  # noqa: S608
            f" WHERE {conditions} ORDER BY 1 FOR UPDATE",
            params,
        )
        found = cursor.fetchall()
    rows = []
    for pk, key, *values in found:
        stale = {
            column.field.name: value
            for column, value in zip(columns, values, strict=True)
            if value and key_id_of_raw(value) != primary
        }
        rows.append((pk, str(key), stale))
    return rows


def _row_key_column(model: type[models.Model]) -> str:
    """The column that names a row in reports: the public id when the model has one."""
    if any(f.name == "public_id" for f in model._meta.concrete_fields):
        return "public_id"
    return str(model._meta.pk.column) if model._meta.pk else "id"


@dataclass
class ColumnReport:
    """What `verify_column` found in one encrypted column. Rows are named by their key only."""

    context: str
    decrypted: int = 0
    older_key: int = 0  # among `decrypted`
    cleartext: int = 0
    unknown_key: dict[str, list[str]] = field(default_factory=dict)  # key id -> row keys
    undecryptable: list[str] = field(default_factory=list)  # row keys

    @property
    def ok(self) -> bool:
        return not self.unknown_key and not self.undecryptable


def verify_column(
    connection: BaseDatabaseWrapper, column: EncryptedColumn, keyring: crypto.Keyring
) -> ColumnReport:
    """Decrypt every non-empty value of `column` (read-only; the values are never returned).

    The `migrate` check decrypts one sample per key id, which is fast but misses a single row
    that does not decrypt (altered, or moved from another column or row): this reads them all.
    """
    model = column.model
    report = ColumnReport(column.context)
    quoted_key = connection.ops.quote_name(_row_key_column(model))
    quoted_table, quoted_column = _names(connection, model._meta.db_table, column.column)
    with connection.chunked_cursor() as cursor:
        cursor.execute(
            f"SELECT {quoted_key}, {quoted_column} FROM {quoted_table}"  # noqa: S608
            f" WHERE {quoted_column} <> '' ORDER BY 1"
        )
        while rows := cursor.fetchmany(1000):
            for key, value in rows:
                _verify_value(report, str(key), value, keyring)
    return report


def _verify_value(report: ColumnReport, key: str, value: str, keyring: crypto.Keyring) -> None:
    key_id = key_id_of_raw(value)
    if key_id == CLEARTEXT:
        report.cleartext += 1
    elif key_id not in keyring.key_ids:
        report.unknown_key.setdefault(key_id, []).append(key)
    else:
        try:
            keyring.decrypt(value, context=report.context)
        except crypto.DecryptionError:
            report.undecryptable.append(key)
            return
        report.decrypted += 1
        if key_id != keyring.primary_id:
            report.older_key += 1


def key_id_of_raw(value: str) -> str:
    """The key id of a stored value, `CLEARTEXT` when it is not a token."""
    return crypto.key_id_of(value) or CLEARTEXT
