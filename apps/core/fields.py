"""Shared model fields."""

import io
from typing import Any

from django.conf import settings
from django.core.exceptions import FieldError, ValidationError
from django.core.files.base import ContentFile
from django.db import models
from django.db.models import lookups
from django.db.models.expressions import Col
from wagtail.images.models import WagtailImageField, WagtailImageFieldFile
from wagtail.utils.file import hash_filelike

from .countries import InvalidCountry, normalize_country
from .crypto import DecryptionError
from .encryption import configured_keyring, encryption_context
from .i18n import language_choices
from .uploads import ImageLimits, UploadRefused, sanitise_image


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

    def get_col(self, alias: str, output_field: Any = None) -> Col:
        if output_field is None or output_field == self:
            return _EncryptedCol(alias, self)
        return super().get_col(alias, output_field)


class EncryptedCharField(EncryptedFieldMixin, models.CharField[Any, Any]):
    """A CharField encrypted at rest. `max_length` validates the plaintext; the column is
    `text`, since the token is longer than the value."""

    def get_internal_type(self) -> str:
        return "TextField"


class EncryptedTextField(EncryptedFieldMixin, models.TextField[Any, Any]):
    """A TextField encrypted at rest."""


class EncryptedDateField(EncryptedFieldMixin, models.DateField[Any, Any]):
    """A DateField encrypted at rest (e.g. a date of birth). The plaintext is the ISO date; the
    column is `text`. Store "no date" as NULL (`null=True`), never as an empty string."""

    def get_internal_type(self) -> str:
        return "TextField"

    def get_db_prep_value(self, value: Any, connection: Any, prepared: bool = False) -> Any:
        return value if prepared else self.get_prep_value(value)

    def from_db_value(self, value: Any, expression: Any, connection: Any) -> Any:
        plain = super().from_db_value(value, expression, connection)
        return None if plain is None or plain == "" else self.to_python(plain)


# --- Uploaded images (SEC.2) -----------------------------------------------------------------


def image_limits() -> ImageLimits:
    """The upload caps from settings (WAGTAILIMAGES_MAX_UPLOAD_SIZE and friends)."""
    return ImageLimits(
        max_bytes=settings.WAGTAILIMAGES_MAX_UPLOAD_SIZE,
        max_pixels=settings.WAGTAILIMAGES_MAX_IMAGE_PIXELS,
        max_side=settings.MPMV_MAX_IMAGE_SIDE,
        max_frames=settings.MPMV_MAX_IMAGE_FRAMES,
    )


def read_upload(content: Any, limit: int) -> bytes:
    """Up to `limit + 1` bytes of an uploaded or stored file, from its start; rewinds it after."""
    if hasattr(content, "open") and getattr(content, "closed", False):
        content.open("rb")
    content.seek(0)
    data = content.read(limit + 1)
    content.seek(0)
    return bytes(data)


class SanitisedImageFieldFile(WagtailImageFieldFile):  # type: ignore[misc]  # Wagtail is untyped
    """Stores only the re-encoded image under a safe name (`apps.core.uploads.sanitise_image`).

    `FieldFile.save` is the one place every new original goes through: a model save of a new
    upload (Wagtail's single and chooser uploads, file replacement, `services.save_image`, the
    seed) and Wagtail's multiple upload, which saves the file itself before the image. Size and
    hash are set from the stored bytes, so Wagtail's duplicate check compares stored files.
    """

    def get_image_dimensions(self) -> Any:
        """None for a file Willow cannot read (it may raise anything): `clean` refuses it."""
        try:
            return super().get_image_dimensions()
        except Exception:  # an XML parse error for "<?php", OSError for a truncated PNG...
            return (None, None)

    def save(self, name: str, content: Any, save: bool = True) -> None:
        # The name was checked with the content in `MediaImage.clean`; here it may already be
        # a storage rewrite (Wagtail's pending multiple uploads), so only the content counts.
        limits = image_limits()
        try:
            clean = sanitise_image(
                read_upload(content, limits.max_bytes), name or "", limits, check_name=False
            )
        except UploadRefused as refusal:
            raise ValidationError({self.field.name: refusal.message}, code=refusal.code) from None
        super().save(clean.name, ContentFile(clean.data), save=False)
        self.instance.file_size = len(clean.data)
        self.instance.file_hash = hash_filelike(io.BytesIO(clean.data))
        self.instance.width, self.instance.height = clean.width, clean.height
        if save:
            self.instance.save()


class SanitisedImageField(WagtailImageField):  # type: ignore[misc]  # Wagtail is untyped
    """Wagtail's image file field whose files are re-encoded when stored (SEC.2).

    Behaviour only: it deconstructs as Wagtail's field, so it needs no migration.
    """

    attr_class = SanitisedImageFieldFile

    def deconstruct(self) -> Any:
        name, _path, args, kwargs = super().deconstruct()
        return name, "wagtail.images.models.WagtailImageField", args, kwargs
