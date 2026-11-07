"""Root pytest configuration: every test belongs to exactly one suite (ADR-0006).

The suite comes from the directory: .../tests/unit/, .../tests/integration/ or tests/e2e/.
Test data comes from factory_boy factories, reseeded before every test (ARC.12).
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING

import factory.random
import pytest

if TYPE_CHECKING:
    from apps.accounts.models import User

# Fixed seed for Faker and factory_boy, so a failure replays with the same data.
FACTORY_SEED = int(os.environ.get("MPMV_TEST_SEED", "20271"))

SUITES = ("unit", "integration", "e2e")


def suite_for(path: Path) -> str | None:
    """Return the suite a test file belongs to, from its parent directories."""
    for part in reversed(path.parts[:-1]):
        if part in SUITES:
            return part
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        suite = suite_for(Path(str(item.path)))
        if suite is None:
            raise pytest.UsageError(
                f"{item.nodeid}: tests must live under tests/unit, tests/integration or tests/e2e"
            )
        if suite == "unit" and item.get_closest_marker("django_db") is not None:
            raise pytest.UsageError(f"{item.nodeid}: unit tests must not use the database")
        item.add_marker(getattr(pytest.mark, suite))


def pytest_report_header() -> str:
    return f"factory_boy/Faker seed: {FACTORY_SEED} (set MPMV_TEST_SEED to change it)"


@pytest.fixture
def factory_seed() -> int:
    return FACTORY_SEED


@pytest.fixture(autouse=True)
def _reproducible_factories(factory_seed: int) -> None:
    """Same seed and fresh sequences per test: data does not depend on which tests ran before."""
    from tests.factory_registry import app_factories

    factory.random.reseed_random(factory_seed)
    for factory_class in app_factories():
        factory_class.reset_sequence()


@pytest.fixture
def superuser(db: None) -> "User":
    """A logged-in-ready superuser, shared by the admin tests of every app.

    Lets apps below `accounts` in the dependency graph (spec 01 §1.3) use a real user without
    importing it.
    """
    from apps.accounts.tests.factories import UserFactory

    return UserFactory.create(email="staff@example.com", superuser=True)
