"""Supply chain checks in CI (SEC.5 item 6).

`make audit` runs a pinned pip-audit against the hashed requirements exported from uv.lock and
fails on any known vulnerability: pip-audit reports no severity, so every advisory counts as
blocking. The only way past one is a reviewed entry in tools/pip_audit_ignore.toml, with a task,
a reason and an expiry date. The ruff `S` (flake8-bandit) rules run everywhere, and every
per-line `# noqa: S...` carries its reason.
"""

import datetime as dt
import importlib.util
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
MAKEFILE = ROOT / "Makefile"
PYPROJECT = ROOT / "pyproject.toml"
IGNORES = ROOT / "tools" / "pip_audit_ignore.toml"
GUIDE = ROOT / "docs" / "dev" / "dependency-updates.md"

_spec = importlib.util.spec_from_file_location("audit_ignores", ROOT / "tools" / "audit_ignores.py")
assert _spec is not None
assert _spec.loader is not None
audit_ignores = importlib.util.module_from_spec(_spec)
sys.modules["audit_ignores"] = audit_ignores
_spec.loader.exec_module(audit_ignores)

TODAY = dt.date(2026, 11, 9)


def make_recipe(target: str) -> str:
    match = re.search(
        rf"^{re.escape(target)}:.*\n((?:\t.*\n?)+)", MAKEFILE.read_text(), re.MULTILINE
    )
    assert match, f"Makefile has no target {target}"
    return match.group(1)


def static_steps() -> list[dict[str, Any]]:
    ci = yaml.safe_load(CI.read_text())
    steps = ci["jobs"]["static"]["steps"]
    assert isinstance(steps, list)
    return steps


# --- pip-audit in CI ------------------------------------------------------------------------


def test_static_checks_job_runs_the_dependency_audit() -> None:
    ci = yaml.safe_load(CI.read_text())
    assert ci["jobs"]["static"]["name"] == "Static checks"  # no new required check name
    audit = [step for step in static_steps() if step.get("run", "").strip() == "make audit"]
    assert len(audit) == 1
    assert "continue-on-error" not in audit[0]
    assert "if" not in audit[0]


def test_audit_runs_a_pinned_pip_audit_on_the_hashed_lockfile_export() -> None:
    recipe = make_recipe("audit")
    assert re.search(r"uv export --frozen\b", recipe)
    assert "--no-hashes" not in recipe
    assert "uvx pip-audit@$(PIP_AUDIT_VERSION) " in recipe, "pin the pip-audit version"
    assert re.search(r"^PIP_AUDIT_VERSION := \d+\.\d+\.\d+$", MAKEFILE.read_text(), re.MULTILINE)
    for flag in ("--strict", "--require-hashes", "--disable-pip", "-r build/requirements.txt"):
        assert flag in recipe, flag
    # Every ignore comes from the reviewed file, through the validating script.
    assert "--ignore-vuln" not in recipe
    assert "tools/audit_ignores.py" in recipe


def test_audit_is_not_part_of_the_offline_make_check() -> None:
    # pip-audit needs the advisory database online; `make check` must run without network.
    assert "audit" not in MAKEFILE.read_text().split("\ncheck:", 1)[1].split("\n", 1)[0]


def test_the_reviewed_ignore_list_is_valid_today() -> None:
    entries = audit_ignores.load(IGNORES)
    audit_ignores.validate(entries, today=dt.date.today())


def valid_entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": "GHSA-aaaa-bbbb-cccc",
        "package": "example",
        "task": "SEC.5",
        "reason": "No fixed release yet; the vulnerable function is never called.",
        "expires": dt.date(2026, 12, 1),
    }
    entry.update(overrides)
    return entry


def test_a_valid_entry_becomes_an_ignore_flag() -> None:
    entries = [valid_entry()]
    audit_ignores.validate(entries, today=TODAY)
    assert audit_ignores.arguments(entries) == ["--ignore-vuln", "GHSA-aaaa-bbbb-cccc"]


@pytest.mark.parametrize("missing", ["id", "package", "task", "reason", "expires"])
def test_an_entry_without_a_required_field_is_refused(missing: str) -> None:
    entry = valid_entry()
    del entry[missing]
    with pytest.raises(audit_ignores.IgnoreListError, match=missing):
        audit_ignores.validate([entry], today=TODAY)


@pytest.mark.parametrize(
    "overrides",
    [
        {"expires": dt.date(2026, 11, 9)},  # expires today: review it again
        {"expires": dt.date(2026, 1, 1)},
        {"expires": "2026-12-01"},  # a string, not a TOML date
        {"expires": dt.date(2027, 3, 1)},  # more than 90 days ahead
        {"reason": "  "},
        {"task": "later"},
        {"id": "--strict"},
    ],
)
def test_an_expired_or_sloppy_entry_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(audit_ignores.IgnoreListError):
        audit_ignores.validate([valid_entry(**overrides)], today=TODAY)


def test_the_script_fails_on_an_expired_entry(tmp_path: Path) -> None:
    path = tmp_path / "ignore.toml"
    path.write_text(
        '[[ignore]]\nid = "PYSEC-2026-1"\npackage = "example"\ntask = "SEC.5"\n'
        'reason = "Pending upstream fix."\nexpires = 2026-01-01\n'
    )
    result = subprocess.run(  # noqa: S603 (this interpreter, a repository script)
        [sys.executable, str(ROOT / "tools" / "audit_ignores.py"), str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0
    assert "PYSEC-2026-1" in result.stderr
    assert result.stdout == ""


def test_the_severity_policy_is_documented() -> None:
    text = GUIDE.read_text()
    for topic in ("pip_audit_ignore.toml", "severity", "expires"):
        assert topic in text, topic


# --- ruff S (flake8-bandit) ---------------------------------------------------------------------


def test_bandit_rules_are_enabled_and_only_test_asserts_are_ignored_wholesale() -> None:
    lint = tomllib.loads(PYPROJECT.read_text())["tool"]["ruff"]["lint"]
    assert "S" in lint["select"]
    assert not any(code.startswith("S") for code in lint.get("ignore", []))
    allowed_in_tests = {"S101", "S105", "S106"}  # assert, fixture passwords built at run time
    for pattern, codes in lint["per-file-ignores"].items():
        bandit = {code for code in codes if code.startswith("S")}
        assert "S" not in bandit, pattern
        if bandit:
            assert "tests" in pattern or pattern == "conftest.py", pattern
            assert bandit <= allowed_in_tests, pattern


NOQA_S = re.compile(r"#\s*noqa:\s*((?:S\d+[,\s]*)+)(.*)$")


def python_files() -> list[Path]:
    skip = {".venv", ".claude", "build", "private", "node_modules", ".git"}
    return [
        path for path in ROOT.rglob("*.py") if not skip.intersection(path.relative_to(ROOT).parts)
    ]


def test_every_bandit_noqa_states_its_reason() -> None:
    bare = []
    for path in python_files():
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            match = NOQA_S.search(line)
            if match and not re.search(r"\w", match.group(2)):
                bare.append(f"{path.relative_to(ROOT)}:{number}")
    assert not bare, "add the reason after the code: " + ", ".join(bare)
