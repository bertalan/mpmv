"""Root pytest configuration: every test belongs to exactly one suite (ADR-0006).

The suite comes from the directory: .../tests/unit/, .../tests/integration/ or tests/e2e/.
"""

from pathlib import Path

import pytest

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
