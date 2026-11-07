"""The suite of a test is decided by its directory (ADR-0006)."""

from pathlib import Path

import pytest

from conftest import suite_for


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("apps/tax/tests/unit/test_engine.py", "unit"),
        ("apps/orders/tests/integration/test_services.py", "integration"),
        ("tests/e2e/test_checkout.py", "e2e"),
        ("tests/test_loose.py", None),
    ],
)
def test_suite_for_uses_the_closest_suite_directory(path: str, expected: str | None) -> None:
    assert suite_for(Path(path)) == expected
