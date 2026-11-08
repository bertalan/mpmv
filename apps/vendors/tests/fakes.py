"""Test doubles for the vendors' external boundaries (ADR-0006: our own interfaces only)."""

import importlib

from django.apps import apps

from apps.vendors import engine


class FakeVatNumberValidator:
    """A `VatNumberValidator` answering from a table; VIES is never called from tests."""

    def __init__(self, answers: dict[str, str] | None = None, default: str = engine.VIES_VALID):
        self.answers = answers or {}
        self.default = default
        self.calls: list[tuple[str, str]] = []

    def check(self, country_prefix: str, number: str) -> str:
        self.calls.append((country_prefix, number))
        return self.answers.get(country_prefix + number, self.default)


def seed_legal_requirements() -> None:
    """Re-run the seeding data migrations (idempotent).

    Transactional tests flush the tables, seeded rows included; tests that rely on the seeded
    rules call this instead of assuming the migration's rows are still there.
    """
    for name in (
        "0005_seed_legal_requirements",
        "0007_seed_dac7_requirements",
        "0015_seed_dac7_seller_dataset",
    ):
        importlib.import_module(f"apps.vendors.migrations.{name}").seed(apps, None)
