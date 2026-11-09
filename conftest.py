"""Root pytest configuration: every test belongs to exactly one suite (ADR-0006).

The suite comes from the directory: .../tests/unit/, .../tests/integration/ or tests/e2e/.
Test data comes from factory_boy factories, reseeded before every test (ARC.12).
"""

import os
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import factory.random
import pytest
from hypothesis import HealthCheck, settings

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from apps.accounts.models import User
    from apps.core.changeset import ChangeSet
    from tests.tenancy import CrossTenant

# Fixed seed for Faker and factory_boy, so a failure replays with the same data.
FACTORY_SEED = int(os.environ.get("MPMV_TEST_SEED", "20271"))

SUITES = ("unit", "integration", "e2e")

# Hypothesis profiles for every `@given` test (SEC.5). `ci` is the default: a fixed example
# sequence, no example database and no per-example deadline, because one slow example on a
# loaded runner would otherwise fail as `Flaky`. `HYPOTHESIS_PROFILE=dev` runs more random
# examples locally, with a deadline to spot slow code.
HYPOTHESIS_PROFILES = {
    "ci": settings(
        max_examples=30,
        deadline=None,
        database=None,
        derandomize=True,
        suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
    ),
    "dev": settings(
        max_examples=200,
        deadline=timedelta(milliseconds=5000),
        database=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
    ),
}
for _name, _profile in HYPOTHESIS_PROFILES.items():
    settings.register_profile(_name, _profile)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


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


@pytest.fixture
def mfa_login(db: None) -> "Callable[[Client, User], Client]":
    """Log a user in with a verified second factor (CORE.4): staff need it for every page.

    Like `superuser`, it lets apps below `accounts` (spec 01 §1.3) act as staff in the admin.
    """
    from apps.accounts.tests.mfa import verified_login

    def login(client: "Client", user: "User") -> "Client":
        verified_login(client, user)
        return client

    return login


@pytest.fixture
def cross_tenant(db: None) -> "CrossTenant":
    """Vendor A and Vendor B, with an owner each, for negative cross-tenant tests (CORE.3).

    See `tests.tenancy`: `check_endpoint(client, endpoint, access, cross_tenant)` asserts that
    Vendor A's owner gets 404 on Vendor B's objects and sees none of them in lists and pages.
    `cross_tenant.seed_b()` makes one object of every vendor-owned model for Vendor B.
    """
    from tests.tenancy import make_cross_tenant
    from tests.tenancy_registry import OWNED_OBJECTS

    return make_cross_tenant(OWNED_OBJECTS.values())


@pytest.fixture
def demo_seed(db: None) -> "ChangeSet":
    """The ARC.10 demo data set: vendors A-E with every VAT regime, 50 EN/IT products, shipping,
    CMS pages and events, built through the services (`apps.seed`, docs/dev/demo-seed.md)."""
    from apps.seed.services import TEST_CONTEXT, seed

    return seed(ctx=TEST_CONTEXT)
