"""axe-core in the e2e suite (THM.5, spec 14 §14.6, §14.11).

A pinned copy of axe-core lives in `tests/e2e/vendor/` (hash in `VENDORED.toml`, checked by a
unit test). `run_axe` evaluates it in the page through the browser's debugging protocol, which the
page's CSP does not govern, then runs the WCAG 2.2 AA rule set and returns the violations.
"""

import json
from functools import cache
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

E2E_VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
AXE_FILE = E2E_VENDOR_DIR / "axe.min.js"
# Spec 14 §14.11 item 4: every level-A and level-AA rule up to WCAG 2.2.
AXE_TAGS = ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa")
# Test tooling is never served by the site, so MPL-2.0 (axe-core) is allowed here only.
TEST_TOOL_LICENCES = frozenset({"MIT", "ISC", "BSD-3-Clause", "Apache-2.0", "MPL-2.0"})


@cache
def axe_source() -> str:
    return AXE_FILE.read_text(encoding="utf-8")


def run_axe(page: Page, *, include: str | None = None) -> list[dict[str, Any]]:
    """The axe violations of the current page (or of the `include` selector) for `AXE_TAGS`."""
    if not page.evaluate("() => typeof window.axe === 'object'"):
        page.evaluate(axe_source())
    options = {"runOnly": {"type": "tag", "values": list(AXE_TAGS)}, "resultTypes": ["violations"]}
    result: dict[str, Any] = page.evaluate(
        "([include, options]) => axe.run(include ? {include: [include]} : document, options)",
        [include, options],
    )
    violations: list[dict[str, Any]] = result["violations"]
    return violations


def format_violations(violations: list[dict[str, Any]]) -> str:
    """One block per rule: id, impact, help URL and the first few failing targets."""
    lines: list[str] = []
    for violation in violations:
        lines.append(
            f"{violation['id']} ({violation.get('impact')}): {violation['help']} "
            f"- {violation['helpUrl']}"
        )
        for node in violation["nodes"][:5]:
            lines.append(f"    {json.dumps(node['target'])}: {node.get('failureSummary', '')}")
    return "\n".join(lines)
