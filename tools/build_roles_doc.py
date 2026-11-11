#!/usr/bin/env python3
"""Write (or `--check`) docs/dev/platform-roles.md from apps/manager/roles.py (CORE.18)."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "docs" / "dev" / "platform-roles.md"


def render() -> str:
    # roles.py is pure data; load it without Django.
    spec = importlib.util.spec_from_file_location(
        "_platform_roles", ROOT / "apps" / "manager" / "roles.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return str(module.render_matrix_doc())


def main() -> int:
    text = render()
    if "--check" in sys.argv[1:]:
        if not TARGET.exists() or TARGET.read_text() != text:
            print("docs/dev/platform-roles.md is stale: run `make docs`", file=sys.stderr)
            return 1
        return 0
    TARGET.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
