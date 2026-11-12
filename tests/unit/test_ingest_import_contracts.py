"""ING.1 guard: `ingest` sits above `catalog` only (spec 01 §1.3: catalog --> ingest).

It may import catalog, vendors, accounts and core, and no other domain app; the checkout, tax,
shipping, payments, ledger, cms, events, seo and compliance never import it. Runs `lint-imports`
with the project's contracts on a throwaway package, like the events guard.
"""

from pathlib import Path

import pytest

from tests.unit import test_events_import_contracts as events_guard
from tests.unit.test_engine_purity import lint_imports
from tests.unit.test_events_import_contracts import write_project

CONTRACTS = (
    "Domain layers (lower layers never import higher ones)",
    "Ingest depends only on catalog, vendors, accounts and core (spec 01 graph)",
)


@pytest.fixture(autouse=True)
def _contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events_guard, "CONTRACTS", CONTRACTS)


@pytest.mark.parametrize(
    "module",
    ["apps.cart.lines", "apps.orders.snapshots", "apps.tax.rules", "apps.shipping.quotes",
     "apps.catalog.listings", "apps.cms.blocks", "apps.events.pages", "apps.vendors.scoping"],
)  # fmt: skip
def test_nothing_below_ingest_imports_it(tmp_path: Path, module: str) -> None:
    result = lint_imports(tmp_path, write_project(tmp_path, module, "import apps.ingest\n"))
    assert result.returncode != 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "target",
    ["orders", "cart", "tax", "shipping", "payments", "ledger", "cms", "events", "seo",
     "compliance", "api", "manager", "mcp"],
)  # fmt: skip
def test_ingest_does_not_import_the_rest_of_the_domain(tmp_path: Path, target: str) -> None:
    source = f"import apps.{target}\n"
    result = lint_imports(tmp_path, write_project(tmp_path, "apps.ingest.services", source))
    assert result.returncode != 0, result.stdout + result.stderr


def test_ingest_may_import_catalog_vendors_accounts_and_core(tmp_path: Path) -> None:
    source = "import apps.catalog\nimport apps.vendors\nimport apps.accounts\nimport apps.core\n"
    result = lint_imports(tmp_path, write_project(tmp_path, "apps.ingest.services", source))
    assert result.returncode == 0, result.stdout + result.stderr
