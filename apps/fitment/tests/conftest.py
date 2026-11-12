"""Fixtures of the fitment tests."""

import pytest

from apps.fitment.tests.factories import FitmentSchemeFactory


@pytest.fixture(autouse=True)
def seeded_scheme(request: pytest.FixtureRequest) -> None:
    """The scheme row is seeded by migration 0001; a transactional test flushes it, so make sure
    it exists (the factory returns the existing row) for every test that uses the database."""
    if request.node.get_closest_marker("django_db") is not None:
        request.getfixturevalue("db")
        FitmentSchemeFactory.create()
