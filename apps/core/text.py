"""Pure text helpers that engines may import (ARC.18).

Engines import no Django (import-linter contract in `pyproject.toml`), so the pure Django string
helpers they need come through this module, the contract's only ignored import. Keep it free of
anything that touches the database, settings or the app registry.
"""

import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

from django.utils.text import slugify as _django_slugify


def slugify(value: str) -> str:
    """ASCII slug of `value`: Django's `slugify` without `allow_unicode`."""
    return str(_django_slugify(value))


def ascii_int(text: str, *, max_digits: int = 18) -> int | None:
    """`text` as a non-negative integer when it is 1 to `max_digits` ASCII digits, else None.

    For untrusted text (a query parameter, a header, a posted field). `str.isdigit` alone is
    True for superscript and circled digits that `int()` refuses, and `int()` itself accepts
    other scripts' digits, signs, spaces and underscores and refuses very long numbers; the
    default limit keeps the value inside a bigint column.
    """
    if not 0 < len(text) <= max_digits or not (text.isascii() and text.isdigit()):
        return None
    return int(text)


def nfc(value: str | None) -> str | None:
    """`value` in Unicode NFC (I18N.10); None stays None, an NFC string is returned unchanged.

    For free text a person, an API client or an importer typed. Not for the SKU match key (spec
    03), which stays NFKC (`apps.catalog.engine.sku_match_key`).
    """
    if value is None:
        return None
    return unicodedata.normalize("NFC", value)


def nfc_deep(value: Any) -> Any:
    """`value` with every string in it (a mapping's values, a list's items, nested) in NFC.

    Mapping keys are kept. HTML and StreamField JSON keep their structure: only the text changes.
    """
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        return {key: nfc_deep(item) for key, item in value.items()}
    if isinstance(value, list):
        return [nfc_deep(item) for item in value]
    if isinstance(value, tuple):
        return tuple(nfc_deep(item) for item in value)
    return value


def nfc_attrs(instance: Any, names: Iterable[str]) -> None:
    """Put the named text attributes of `instance` in NFC, in place (a non-text value is kept)."""
    for name in names:
        value = getattr(instance, name, None)
        if isinstance(value, str):
            setattr(instance, name, unicodedata.normalize("NFC", value))


def nfc_text_fields(instance: Any) -> None:
    """Put every CharField and TextField value of the model `instance` in NFC, in place."""
    nfc_attrs(
        instance,
        (
            field.attname
            for field in instance._meta.concrete_fields
            if field.get_internal_type() in {"CharField", "TextField"}
        ),
    )
