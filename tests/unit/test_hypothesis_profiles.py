"""The Hypothesis profiles named by CI and the nightly workflow must be registered (SEC.5)."""

from __future__ import annotations

import pytest
from hypothesis import settings
from hypothesis.errors import InvalidArgument


@pytest.mark.parametrize("name", ["ci", "dev", "nightly"])
def test_profile_is_registered(name: str) -> None:
    try:
        profile = settings.get_profile(name)
    except InvalidArgument:  # pragma: no cover - the failure this test guards against
        pytest.fail(f"Hypothesis profile {name!r} is not registered")
    assert profile.max_examples > 0


def test_nightly_runs_more_examples_without_deadline() -> None:
    nightly = settings.get_profile("nightly")
    assert nightly.max_examples >= 1000
    assert nightly.deadline is None
    assert nightly.max_examples > settings.get_profile("ci").max_examples
