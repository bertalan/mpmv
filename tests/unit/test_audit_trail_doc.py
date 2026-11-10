"""Guard: every audit action named in the code is listed in docs/dev/audit-trail.md.

Coherence checks 7 and 8 both found the table stale. The test reads action names from the
source: module-level constants whose value looks like `<app>.<thing>.<verb>` and string
literals passed as `action=`, `created=`, `updated=` or `deleted=`. A name counts as listed
when the page has the full name or, for the `.verified`-style shorthand, the same prefix and
the last segment as `.leaf`.
"""

import ast
import re
from pathlib import Path
from typing import TypeGuard

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs/dev/audit-trail.md").read_text().replace("`", "")
ACTION = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")
# Every app under apps/ is scanned, so a new app cannot be skipped. `vendor` (the singular used by
# the vendors app's audit actions) and `auth` (Django groups) are the two prefixes that are not
# apps.
PREFIXES = {p.name for p in (ROOT / "apps").iterdir() if (p / "__init__.py").exists()} | {
    "vendor",
    "auth",
}
KEYWORDS = {"action", "created", "updated", "deleted"}


def _is_action(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(ACTION.match(value)) and value.split(".")[0] in PREFIXES


def _is_action_name(name: str) -> bool:
    # Skips model labels (`..._MODEL`, `PAGE`), permission codenames (`..._PERMISSION`),
    # session keys and email template names.
    skipped = ("MODEL", "PERMISSION", "SESSION_KEY", "TEMPLATE")
    return name.isupper() and "_" in name and not name.endswith(skipped)


def _actions() -> set[str]:
    found: set[str] = set()
    for path in (ROOT / "apps").rglob("*.py"):
        if "tests" in path.parts or "migrations" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Assign | ast.AnnAssign) and isinstance(
                node.value, ast.Constant
            ):
                # `X = "..."` and `X: Final = "..."` (the compliance app annotates its constants).
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [t.id for t in targets if isinstance(t, ast.Name)]
                if any(_is_action_name(n) for n in names) and _is_action(node.value.value):
                    found.add(node.value.value)
            elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_record":
                # events.services._record(ctx, "<action>", ...)
                for arg in node.args[1:2]:
                    if isinstance(arg, ast.Constant) and _is_action(arg.value):
                        found.add(arg.value)
            elif (
                isinstance(node, ast.keyword)
                and node.arg in KEYWORDS
                and isinstance(node.value, ast.Constant)
                and _is_action(node.value.value)
            ):
                found.add(node.value.value)
    return found


def _listed(action: str) -> bool:
    if action in DOC:
        return True
    prefix, _, leaf = action.rpartition(".")
    return f"{prefix}." in DOC and f".{leaf}" in DOC


def test_every_audit_action_is_in_the_audit_trail_page() -> None:
    missing = sorted(a for a in _actions() if not _listed(a))
    assert not missing, f"add these actions to docs/dev/audit-trail.md: {missing}"


def test_the_scan_finds_actions() -> None:
    assert len(_actions()) > 40


def test_every_app_is_scanned() -> None:
    assert {"compliance", "ledger", "orders", "payments", "seo"} <= PREFIXES
