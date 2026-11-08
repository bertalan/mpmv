"""Pure text helpers that engines may import (ARC.18).

Engines import no Django (import-linter contract in `pyproject.toml`), so the pure Django string
helpers they need come through this module, the contract's only ignored import. Keep it free of
anything that touches the database, settings or the app registry.
"""

from django.utils.text import slugify as _django_slugify


def slugify(value: str) -> str:
    """ASCII slug of `value`: Django's `slugify` without `allow_unicode`."""
    return str(_django_slugify(value))
