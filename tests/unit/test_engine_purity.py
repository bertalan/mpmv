"""ARC.18 guard: engines are pure functions, with no database or ORM access (spec 01 §1.3).

The import-linter contract in `pyproject.toml` forbids `django`, every `models` module and every
`services` module in `apps.*.engine`. import-linter can forbid an external package only as a
whole (`django`, not `django.db`), so engines import no Django at all; the one pure Django
helper they need (`slugify`) comes through `apps.core.text`, the only ignored import.

These tests run `lint-imports` with that contract on a throwaway project, so they prove the
contract itself rejects a violating engine. `make check` runs it on the real code.
"""

import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_NAME = "Engines are pure: no Django, models or services in apps.*.engine (ARC.18)"


def engine_contract() -> dict[str, Any]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    contracts: list[dict[str, Any]] = config["tool"]["importlinter"]["contracts"]
    matches = [c for c in contracts if c["name"] == CONTRACT_NAME]
    assert len(matches) == 1, f"pyproject.toml needs exactly one contract named {CONTRACT_NAME!r}"
    return matches[0]


def write_project(root: Path, engine_source: str) -> Path:
    """A minimal `apps` package with a `shop` app whose engine has `engine_source`."""
    shop = root / "apps" / "shop"
    core = root / "apps" / "core"
    shop.mkdir(parents=True)
    core.mkdir(parents=True)
    for package in (root / "apps", shop, core):
        (package / "__init__.py").write_text("")
    (shop / "models.py").write_text("from django.db import models\n")
    (shop / "services.py").write_text("from apps.shop import models\n")
    (core / "text.py").write_text("from django.utils.text import slugify\n")
    (shop / "engine.py").write_text(engine_source)

    contract = {**engine_contract(), "unmatched_ignore_imports_alerting": "none"}
    lines = [
        "[tool.importlinter]",
        'root_packages = ["apps"]',
        "include_external_packages = true",
        "",
        "[[tool.importlinter.contracts]]",
        *(f"{key} = {toml_value(value)}" for key, value in contract.items()),
    ]
    config = root / "importlinter.toml"
    config.write_text("\n".join(lines) + "\n")
    return config


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def lint_imports(root: Path, config: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 (this interpreter, test-controlled config)
        [
            sys.executable,
            "-c",
            "import sys; from importlinter.cli import lint_imports_command; "
            "sys.exit(lint_imports_command())",
            "--config",
            str(config),
            "--no-cache",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_contract_covers_every_engine_and_forbids_django_models_and_services() -> None:
    contract = engine_contract()
    assert contract["type"] == "forbidden"
    assert contract["source_modules"] == ["apps.*.engine"]
    assert set(contract["forbidden_modules"]) == {"django", "apps.*.models", "apps.*.services"}
    assert contract.get("ignore_imports", []) == ["apps.core.text -> django"]
    assert not contract.get("allow_indirect_imports", False)


@pytest.mark.parametrize(
    "engine_source",
    [
        "from django.db import models\n",
        "import django.db.transaction\n",
        "from apps.shop import models\n",
        "from apps.shop.services import x\n",
        "from apps.shop.models import Product\n",
    ],
)
def test_lint_imports_fails_when_an_engine_touches_the_database(
    tmp_path: Path, engine_source: str
) -> None:
    result = lint_imports(tmp_path, write_project(tmp_path, engine_source))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "apps.shop.engine is not allowed to import" in result.stdout


@pytest.mark.parametrize(
    "engine_source",
    [
        "import re\nfrom decimal import Decimal\n",
        "from apps.core.text import slugify\n",
    ],
)
def test_lint_imports_passes_a_pure_engine(tmp_path: Path, engine_source: str) -> None:
    result = lint_imports(tmp_path, write_project(tmp_path, engine_source))
    assert result.returncode == 0, result.stdout + result.stderr
