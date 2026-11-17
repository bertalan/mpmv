"""Root pytest configuration: every test belongs to exactly one suite (ADR-0006).

The suite comes from the directory: .../tests/unit/, .../tests/integration/ or tests/e2e/.
The attack scenarios in tests/security/ (SEC.5) run in the integration suite, on every PR, and
also carry the `security` marker (`pytest -m security`).
Test data comes from factory_boy factories, reseeded before every test (ARC.12).
"""

import os
import zlib
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import factory.random
import pytest
from hypothesis import HealthCheck, settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from django.test import Client

    from apps.accounts.models import User
    from apps.core.changeset import ChangeSet
    from tests.tenancy import CrossTenant


@pytest.fixture
def admin_open(settings: Any) -> None:
    """OPS.5: the technical admin answers every address, for tests of the sign-in protections
    that connect from several client addresses (the test settings allow loopback only)."""
    settings.ADMIN_ALLOWED_NETWORKS = ["0.0.0.0/0", "::/0"]


@pytest.fixture
def default_languages(settings: Any) -> None:
    """I18N.15: pins the default language set (English primary, then it, de, fr, es) for tests
    whose subject is exactly that set. The suite also runs with another `MPMV_LANGUAGES` (CI job
    `it,de`), so a test must not read the default from the environment it runs in."""
    from mpmv.settings.languages import ALL_LANGUAGE_CODES, language_settings

    settings.LANGUAGE_CODE, settings.LANGUAGES = language_settings(ALL_LANGUAGE_CODES)
    settings.WAGTAIL_CONTENT_LANGUAGES = settings.LANGUAGES


@pytest.fixture
def english_interface(default_languages: None) -> "Iterator[None]":
    """Renders in English: for tests that assert the English msgids of the interface."""
    from django.utils import translation

    with translation.override("en"):
        yield


# Fixed seed for Faker and factory_boy, so a failure replays with the same data.
FACTORY_SEED = int(os.environ.get("MPMV_TEST_SEED", "20271"))

SUITES = ("unit", "integration", "e2e")
# Directories whose tests join a suite under another name: attack scenarios need the full stack.
SUITE_ALIASES = {"security": "integration"}

# Hypothesis profiles for every `@given` test (SEC.5). `ci` is the default: a fixed example
# sequence, no example database and no per-example deadline, because one slow example on a
# loaded runner would otherwise fail as `Flaky`. `HYPOTHESIS_PROFILE=dev` runs more random
# examples locally, with a deadline to spot slow code. `nightly` (security-nightly.yml) runs 1000
# fresh random examples per test, no deadline.
HYPOTHESIS_PROFILES = {
    "ci": settings(
        max_examples=30,
        deadline=None,
        database=None,
        derandomize=True,
        suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
    ),
    "nightly": settings(
        max_examples=1000,
        deadline=None,
        database=None,
        derandomize=False,
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
        if part in SUITE_ALIASES:
            return SUITE_ALIASES[part]
    return None


def _shard_selector() -> tuple[int, int] | None:
    """ARC.34: `MPMV_TEST_SHARD=i/N` (1-based) keeps only the tests whose hash falls in shard i.

    The split is by test id (crc32): deterministic, no timing data, evenly sized, and every test
    runs in exactly one shard.
    """
    raw = os.environ.get("MPMV_TEST_SHARD", "").strip()
    if not raw:
        return None
    index, _, total = raw.partition("/")
    if not (index.isdigit() and total.isdigit() and 1 <= int(index) <= int(total)):
        raise pytest.UsageError(f"MPMV_TEST_SHARD must look like 2/4, got {raw!r}")
    return int(index), int(total)


def _in_shard(item: pytest.Item, index: int, total: int) -> bool:
    return zlib.crc32(item.nodeid.encode()) % total == index - 1


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    shard = _shard_selector()
    if shard is not None:
        kept = [item for item in items if _in_shard(item, *shard)]
        dropped = [item for item in items if not _in_shard(item, *shard)]
        if dropped:
            config.hook.pytest_deselected(items=dropped)
        items[:] = kept
    for item in items:
        suite = suite_for(Path(str(item.path)))
        if suite is None:
            raise pytest.UsageError(
                f"{item.nodeid}: tests must live under tests/unit, tests/integration, tests/e2e "
                "or tests/security"
            )
        if suite == "unit" and item.get_closest_marker("django_db") is not None:
            raise pytest.UsageError(f"{item.nodeid}: unit tests must not use the database")
        item.add_marker(getattr(pytest.mark, suite))
        if "security" in Path(str(item.path)).parts[:-1]:
            item.add_marker(pytest.mark.security)
        if _flushes_database(item):
            # Under xdist (--dist loadgroup) a transactional test flushes the rows its worker's
            # database got from data migrations. pytest-django runs those tests last; the group
            # keeps that order: the group starts only after every other test is handed out, and
            # a worker that ran it takes no ordinary test afterwards.
            item.add_marker(pytest.mark.xdist_group(name=f"flush-{item.nodeid}"))


def _flushes_database(item: pytest.Item) -> bool:
    """True for a test that truncates the database (transaction=True, TransactionTestCase)."""
    marker = item.get_closest_marker("django_db")
    if marker is not None and marker.kwargs.get("transaction"):
        return True
    cls = getattr(item, "cls", None)
    if cls is not None:
        from django.test import TestCase, TransactionTestCase

        return issubclass(cls, TransactionTestCase) and not issubclass(cls, TestCase)
    return "transactional_db" in getattr(item, "fixturenames", ())


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


@pytest.fixture(autouse=True)
def _migration_tests_start_from_flushed_tables(request: pytest.FixtureRequest) -> None:
    """A test that rolls migrations back starts from empty tables, whatever ran before it.

    Reversing past `events.0002` deletes the event pages through historical models; with rows
    left by `post_migrate` (the Events index page) that delete cascades into tables of models the
    same rollback has already dropped (`cms_blogindexpage`, ...). The old single-job run only
    passed because an earlier transactional test had flushed its worker's database (ARC.34: a
    shard may start with the migration test). The test would flush the database afterwards
    anyway, and these tests run last (see `xdist_group` above).
    """
    module = request.module
    if "MigrationExecutor" not in vars(module) or not _flushes_database(request.node):
        return
    request.getfixturevalue("transactional_db")
    from django.core.management import call_command

    call_command("flush", interactive=False, inhibit_post_migrate=True, verbosity=0)


@pytest.fixture(autouse=True)
def _no_language_leaks_between_tests() -> "Iterator[None]":
    """A request activates its language (LocaleMiddleware, a user's preference) and nothing
    deactivates it, so the next test of the thread would resolve `i18n_patterns` URLs in it."""
    from django.utils import translation

    translation.deactivate()
    yield
    translation.deactivate()


@pytest.fixture(autouse=True)
def _no_operator_setting_leaks_between_tests() -> "Iterator[None]":
    """The process copy of the operator's sales-terms setting is dropped around each test: a
    rolled-back operator row does not fire a signal."""
    from apps.vendors.services import forget_require_terms

    forget_require_terms()
    yield
    forget_require_terms()


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
    The session also holds a fresh passkey with user verification, as D-32 group 3 actions
    (bank details, owner role) ask; tests of the levels themselves use `verified_login`.
    """
    from apps.accounts.tests.mfa import verified_login

    def login(client: "Client", user: "User") -> "Client":
        verified_login(client, user, passkey=True)
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


@pytest.fixture
def consumer_prices(db: None) -> "Iterator[None]":
    """TAX.12: an Italian platform operator, the IT 22 %, DE 19 % and FR 20 % standard rates and
    an OSS tax profile for every vendor (existing ones and those made during the test), so shop
    pages show VAT-inclusive consumer prices (an English visitor gets the operator's country,
    IT). Through the app registry: the catalog and cms tests use it without importing the tax
    app (spec 01 graph). A test about another regime replaces the vendor's profile."""
    import datetime as dt
    from decimal import Decimal

    from django.apps import apps
    from django.db.models.signals import post_save
    from django.utils import timezone

    since = timezone.now() - timedelta(days=365)
    operator = apps.get_model("core", "PlatformOperator")
    operator.objects.create(
        legal_name="Operator",
        country="IT",
        vat_number="IT00000000000",
        tax_regime=operator.TaxRegime.SMALL_BUSINESS,
        commission_vat_mode=operator.CommissionVatMode.NONE,
        dac7_authority_country="IT",
        valid_from=since,
    )
    rate = apps.get_model("tax", "TaxRate")
    for country, value in (("IT", "22"), ("DE", "19"), ("FR", "20")):
        rate.objects.create(
            country=country,
            category="standard",
            rate=Decimal(value),
            source="manual",
            valid_from=since,
        )
    vendor_model = apps.get_model("vendors", "Vendor")
    profile = apps.get_model("tax", "VendorTaxProfile")

    def give_profile(vendor: Any) -> None:
        if not profile.objects.filter(vendor=vendor).exists():
            profile.objects.create(
                vendor=vendor,
                regime="oss",
                oss_registered_since=dt.date(2020, 1, 1),
                valid_from=since,
            )

    def on_save(sender: Any, instance: Any, created: bool, **kwargs: Any) -> None:
        if created:
            give_profile(instance)

    for vendor in vendor_model.objects.all():
        give_profile(vendor)
    post_save.connect(on_save, sender=vendor_model, dispatch_uid="tax12-consumer-prices")
    try:
        yield
    finally:
        post_save.disconnect(sender=vendor_model, dispatch_uid="tax12-consumer-prices")
