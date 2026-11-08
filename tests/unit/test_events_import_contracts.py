"""EVT.1 guard: the checkout never depends on events (spec 15 "Page types in a small events app").

`apps.events` is its own layer between `apps.cart` and `apps.cms` in the "Domain layers"
contract; `apps.tax` and `apps.shipping` may not import it (the engines contract), and a
forbidden contract keeps `apps.cart` and `apps.orders` off it. These tests run `lint-imports`
with the project's contracts on a throwaway `apps` package, so they prove that an import of
`apps.events` from those apps fails `make check` (which runs `lint-imports` on the real code).
"""

from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_engine_purity import lint_imports, toml_value

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = (
    "Domain layers (lower layers never import higher ones)",
    "Tax and shipping engines stay independent of orders and cart",
    "The checkout never imports events (spec 15)",
)


def _contracts() -> list[dict[str, Any]]:
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    contracts: list[dict[str, Any]] = config["tool"]["importlinter"]["contracts"]
    chosen = [c for c in contracts if c["name"] in CONTRACTS]
    assert sorted(c["name"] for c in chosen) == sorted(CONTRACTS)
    return chosen


def _modules(contracts: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for contract in contracts:
        for layer in contract.get("layers", []):
            names.update(part.strip() for part in layer.split("|"))
        names.update(contract.get("source_modules", []))
        names.update(contract.get("forbidden_modules", []))
    return {name for name in names if name.startswith("apps.")}


def write_project(root: Path, module: str, source: str) -> Path:
    """A throwaway `apps` package with every app the contracts name, and `module` = `source`."""
    contracts = _contracts()
    (root / "apps").mkdir()
    (root / "apps" / "__init__.py").write_text("")
    for name in _modules(contracts):
        package = root.joinpath(*name.split("."))
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("")
    root.joinpath(*module.split(".")).with_suffix(".py").write_text(source)

    lines = ["[tool.importlinter]", 'root_packages = ["apps"]', ""]
    for contract in contracts:
        lines.append("[[tool.importlinter.contracts]]")
        lines.extend(f"{key} = {toml_value(value)}" for key, value in contract.items())
        lines.append("")
    config = root / "importlinter.toml"
    config.write_text("\n".join(lines))
    return config


def test_events_sits_between_cart_and_cms_in_the_domain_layers() -> None:
    layers = next(c for c in _contracts() if c["type"] == "layers")["layers"]
    assert layers.index("apps.cart") + 1 == layers.index("apps.events")
    assert layers.index("apps.events") + 1 == layers.index("apps.cms")


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("apps.shipping.quotes", "import apps.events\n"),
        ("apps.tax.rules", "from apps.events import models\n"),
        ("apps.cart.lines", "from apps.events import services\n"),
        ("apps.orders.snapshots", "import apps.events.models\n"),
        ("apps.catalog.listings", "import apps.events\n"),
        ("apps.cms.blocks", "import apps.events\n"),
    ],
)
def test_an_import_of_events_from_below_or_from_the_checkout_fails(
    tmp_path: Path, module: str, source: str
) -> None:
    result = lint_imports(tmp_path, write_project(tmp_path, module, source))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "apps.events" in result.stdout


@pytest.mark.parametrize(
    "source",
    [
        "import apps.catalog\nimport apps.cms\nimport apps.vendors\nimport apps.core\n",
    ],
)
def test_events_may_import_catalog_cms_vendors_and_core(tmp_path: Path, source: str) -> None:
    result = lint_imports(tmp_path, write_project(tmp_path, "apps.events.models", source))
    assert result.returncode == 0, result.stdout + result.stderr
