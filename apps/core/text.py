"""Pure text helpers that engines may import (ARC.18).

Engines import no Django (import-linter contract in `pyproject.toml`), so the pure Django string
helpers they need come through this module, the contract's only ignored import. Keep it free of
anything that touches the database, settings or the app registry.
"""

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
