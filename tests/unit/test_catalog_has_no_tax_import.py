"""TAX.12: the catalog stays free of tax imports (spec 01 graph); its templates load the tax
app's `consumer_prices` tags instead. `make imports` checks the same contract
("The catalog never imports tax (TAX.12)"); this test names the offending file."""

import ast
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[2] / "apps" / "catalog"


def _imports(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_no_catalog_module_imports_the_tax_app() -> None:
    offenders = [
        f"{path.relative_to(CATALOG)}: {name}"
        for path in sorted(CATALOG.rglob("*.py"))
        for name in _imports(path)
        if name == "apps.tax" or name.startswith("apps.tax.")
    ]
    assert offenders == []


def test_the_catalog_templates_get_the_gross_price_from_the_tax_tag() -> None:
    card = (CATALOG / "templates" / "catalog" / "includes" / "product_card.html").read_text()
    assert "consumer_prices %}" in card
    assert "{% consumer_price card.price card.fiscal_category card.vendor as shown %}" in card
