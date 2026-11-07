"""Shared model fields."""

from typing import Any

from django.core.exceptions import FieldError, ValidationError
from django.db import models
from django.db.models import lookups
from django.db.models.expressions import Col

from .countries import InvalidCountry, normalize_country
from .crypto import DecryptionError
from .encryption import configured_keyring, encryption_context
from .i18n import language_choices


class MoneyField(models.DecimalField[Any, Any]):
    """Amount in EUR with 2 decimals (`DecimalField(12, 2)`). Floats are refused."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("max_digits", 12)
        kwargs.setdefault("decimal_places", 2)
        super().__init__(*args, **kwargs)

    def get_prep_value(self, value: Any) -> Any:
        if isinstance(value, float):
            raise TypeError("float amounts are not allowed; use Decimal")
        return super().get_prep_value(value)


class CountryField(models.CharField[Any, Any]):
    """ISO 3166-1 alpha-2 country code, normalized to upper case (EL → GR, UK → GB)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("max_length", 2)
        super().__init__(*args, **kwargs)

    def to_python(self, value: Any) -> Any:
        value = super().to_python(value)
        if value in (None, ""):
            return value
        try:
            return normalize_country(value)
        except InvalidCountry as exc:
            raise ValidationError(str(exc), code="invalid_country") from exc


class LanguageField(models.CharField[Any, Any]):
    """A language code from settings.LANGUAGES, validated against the settings at runtime.

    The choices are the `language_choices` callable, so migrations never copy the language list:
    adding a language is one settings entry (spec 09 §9.2). There is deliberately no database
    check constraint on the codes, for the same reason.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("max_length", 8)
        kwargs.setdefault("choices", language_choices)
        super().__init__(*args, **kwargs)


class _EncryptedCol(Col):
    """The column of an encrypted field. Resolving it on its own means sorting by it (`ORDER BY`
    resolves its columns, a SELECT does not), so it raises `FieldError`: the order of random
    ciphertexts would look plausible and be wrong. The allowed lookups unwrap it first."""

    def resolve_expression(self, *args: Any, **kwargs: Any) -> Any:
        raise FieldError(_not_searchable(self.target))


def _plain(lhs: Any) -> Any:
    if isinstance(lhs, _EncryptedCol):
        return Col(lhs.alias, lhs.target, lhs.output_field)
    return lhs


class _EmptyOnlyExact(lookups.Exact):
    """`field=""` only: ciphertexts are randomized, so equality on a value never matches."""

    def __init__(self, lhs: Any, rhs: Any) -> None:
        super().__init__(_plain(lhs), rhs)

    def get_prep_lookup(self) -> Any:
        if self.rhs not in ("", None):
            raise FieldError(_not_searchable(self.lhs.output_field))
        return super().get_prep_lookup()


class _IsNull(lookups.IsNull):
    def __init__(self, lhs: Any, rhs: Any) -> None:
        super().__init__(_plain(lhs), rhs)


def _not_searchable(field: Any) -> str:
    return (
        f"{field.model._meta.label_lower}.{field.name} is encrypted at rest and cannot be "
        "searched, compared or sorted in SQL. Only `=''` and `__isnull` work; a lookup needs a "
        "keyed blind index (docs/dev/field-encryption.md)."
    )


class EncryptedFieldMixin(models.Field[Any, Any]):
    """Encrypts the value at rest (SEC.3): AES-256-GCM, key outside the database, rotatable.

    Python code sees the plaintext; the column holds an `enc1:<key id>:...` token
    (`apps.core.crypto`). Empty strings and NULL are stored as they are. A value that does not
    decrypt (wrong or missing key, cleartext, tampering) raises `DecryptionError`: never a
    silent fallback. Lookups other than `=''` and `__isnull`, and ordering by the field, raise
    `FieldError`.

    Mask the field in audit snapshots too: an encrypted column is pointless if the audit log
    copies the value in clear. See docs/dev/field-encryption.md.
    """

    def _context(self) -> str:
        return encryption_context(self.model, self.name)

    def get_prep_value(self, value: Any) -> Any:
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        return configured_keyring().encrypt(str(value), context=self._context())

    def from_db_value(self, value: Any, expression: Any, connection: Any) -> Any:
        if value is None or value == "":
            return value
        try:
            return configured_keyring().decrypt(value, context=self._context())
        except DecryptionError as exc:
            raise DecryptionError(
                f"Cannot read {exc}. Check the field encryption keys: "
                "`manage.py check --database default`."
            ) from None

    def get_lookup(self, lookup_name: str) -> Any:
        if lookup_name == "exact":
            return _EmptyOnlyExact
        if lookup_name == "isnull":
            return _IsNull
        return None  # Django raises FieldError("Unsupported lookup ...")

    def get_transform(self, lookup_name: str) -> Any:
        return None

    def get_col(self, alias: str | None, output_field: Any = None) -> Col:
        if output_field is None or output_field == self:
            return _EncryptedCol(alias, self)
        return super().get_col(alias, output_field)

    @property
    def cached_col(self) -> Col:  # type: ignore[override]
        return _EncryptedCol(self.model._meta.db_table, self)


class EncryptedCharField(EncryptedFieldMixin, models.CharField[Any, Any]):
    """A CharField encrypted at rest. `max_length` validates the plaintext; the column is
    `text`, since the token is longer than the value."""

    def get_internal_type(self) -> str:
        return "TextField"


class EncryptedTextField(EncryptedFieldMixin, models.TextField[Any, Any]):
    """A TextField encrypted at rest."""
