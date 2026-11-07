"""ARC.12: every model has a factory_boy factory that the tests use; data is reproducible."""

import re
import secrets
from pathlib import Path

import factory.random
import pytest

from apps.accounts.tests.factories import UserFactory
from tests.factory_registry import Factory, app_factories, factory_model, project_models

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_TESTS = {
    ROOT / "tests" / "unit" / "test_factories.py",
    ROOT / "tests" / "integration" / "test_factories.py",
}


def _test_sources() -> str:
    files = [*ROOT.glob("apps/*/tests/**/test_*.py"), *ROOT.glob("tests/**/test_*.py")]
    return "\n".join(p.read_text() for p in files if p not in REGISTRY_TESTS)


def test_every_project_model_has_a_factory() -> None:
    covered = {factory_model(f) for f in app_factories()}
    missing = sorted(m._meta.label for m in project_models() if m not in covered)
    assert not missing, (
        "Add a DjangoModelFactory in apps/<app>/tests/factories.py for: " + ", ".join(missing)
    )


def test_every_factory_is_used_by_a_test() -> None:
    sources = _test_sources()
    unused = sorted(
        f.__name__ for f in app_factories() if not re.search(rf"\b{f.__name__}\b", sources)
    )
    assert not unused, "Factories not used by any test: " + ", ".join(unused)


@pytest.mark.parametrize("factory_class", app_factories(), ids=lambda f: f.__name__)
def test_every_factory_builds_without_the_database(factory_class: Factory) -> None:
    instance = factory_class.build()
    assert isinstance(instance, factory_model(factory_class))
    assert instance.pk is None


def test_factories_are_reseeded_before_every_test(factory_seed: int) -> None:
    first = UserFactory.build()
    assert first.email == "user0@example.com"  # sequences restart in every test
    factory.random.reseed_random(factory_seed)
    assert UserFactory.build().full_name == first.full_name  # Faker replays from the seed


def test_built_users_get_a_hashed_password_unless_one_is_given() -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    known = UserFactory.build(password=password)
    assert known.check_password(password)
    assert known.password != password
    assert UserFactory.build().has_usable_password()
