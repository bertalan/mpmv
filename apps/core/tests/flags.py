"""Test helpers for feature flags (ARC.8)."""

from collections.abc import Iterator
from contextlib import contextmanager

from apps.core import flags


@contextmanager
def registered(spec: flags.FlagSpec) -> Iterator[flags.FlagSpec]:
    """Register `spec` for the duration of a test, then forget it."""
    flags.register(spec)
    try:
        yield spec
    finally:
        flags.unregister(spec.name)
