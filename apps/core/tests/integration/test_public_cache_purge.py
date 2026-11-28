"""SEO.6 2/3: the registry a service calls to purge public pages without knowing the cache."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from apps.core import public_cache

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clean_registry() -> Iterator[None]:
    saved = list(public_cache._listeners)
    public_cache._listeners.clear()
    yield
    public_cache._listeners[:] = saved


def test_a_purge_without_a_listener_is_a_no_op() -> None:
    public_cache.purge_public_pages("test")


def test_listeners_run_once_the_transaction_commits(
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    seen: list[str] = []
    public_cache.register_purge_listener(seen.append)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        public_cache.purge_public_pages("vendor storefront")
        assert seen == []  # not before the commit
    assert len(callbacks) == 1
    callbacks[0]()
    assert seen == ["vendor storefront"]


def test_a_failing_listener_does_not_stop_the_others_or_the_caller(
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    seen: list[str] = []

    def broken(reason: str) -> None:
        raise RuntimeError("cache down")

    public_cache.register_purge_listener(broken)
    public_cache.register_purge_listener(seen.append)
    with django_capture_on_commit_callbacks(execute=True):
        public_cache.purge_public_pages("x")
    assert seen == ["x"]


def test_registering_the_same_listener_twice_runs_it_once(
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    seen: list[str] = []
    public_cache.register_purge_listener(seen.append)
    public_cache.register_purge_listener(seen.append)
    with django_capture_on_commit_callbacks(execute=True):
        public_cache.purge_public_pages("x")
    assert seen == ["x"]
