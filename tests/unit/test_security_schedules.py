"""Periodic security runs (SEC.5, owner request 2026-11-09).

`security-nightly` reruns the slow security suites with the full Hypothesis profile and audits the
dependencies against fresh advisories every night. `security-stage` runs the OWASP ZAP baseline
and nuclei against STAGE every week, only when lane-a has set the URL secret, and against LIVE
only with a second, explicit opt-in. A failing scheduled run opens or updates one `security`
issue; a green run closes it. Neither workflow, nor the issue text, ever names a server.
"""

import importlib.util
import re
import sys
import tomllib
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
NIGHTLY = WORKFLOWS / "security-nightly.yml"
STAGE = WORKFLOWS / "security-stage.yml"
SCAN_SCRIPT = ROOT / "deploy" / "ci" / "stage-scan.sh"
RELEASE_DOC = ROOT / "docs" / "dev" / "release.md"
PYPROJECT = ROOT / "pyproject.toml"
MAKEFILE = ROOT / "Makefile"

_spec = importlib.util.spec_from_file_location(
    "security_report", ROOT / "tools" / "security_report.py"
)
assert _spec is not None
assert _spec.loader is not None
security_report = importlib.util.module_from_spec(_spec)
sys.modules["security_report"] = security_report
_spec.loader.exec_module(security_report)

# GitHub-owned actions: a version tag, never a branch or a moving tag. Every other action: a full
# commit SHA (a tag can be moved), with the version in a trailing comment, as in release.yml.
GITHUB_OWNERS = {"actions", "github"}
PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@v\d+(\.\d+)*$")
SHA_PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
SHA_PIN_LINE = re.compile(r"uses:\s*([\w.-]+/[\w./-]+)@([0-9a-f]{40}) # v\d+(\.\d+)*\s*$")
ON_MAIN = "github.ref == 'refs/heads/main'"
PINNED_IMAGE = re.compile(r"^[\w./-]+:v?\d+(\.\d+)+$")
MOVING_TAGS = {"latest", "stable", "main", "master", "weekly", "nightly"}
ALLOWED_PERMISSIONS = {"contents": "read", "issues": "write"}


def load(path: Path) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{path.name} is not a mapping"
    return data


def triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    """The `on:` block; YAML 1.1 reads the bare key `on` as the boolean True."""
    value = workflow.get("on", workflow.get(True))
    assert isinstance(value, dict)
    return value


def all_steps(workflow: dict[Any, Any]) -> list[dict[str, Any]]:
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


@pytest.fixture(scope="module", params=[NIGHTLY, STAGE], ids=["nightly", "stage"])
def workflow_path(request: pytest.FixtureRequest) -> Path:
    path = request.param
    assert isinstance(path, Path)
    return path


def test_both_workflows_run_on_a_schedule_and_by_hand(workflow_path: Path) -> None:
    on = triggers(load(workflow_path))
    assert set(on) == {"schedule", "workflow_dispatch"}
    crons = [entry["cron"] for entry in on["schedule"]]
    assert crons
    for cron in crons:
        assert len(cron.split()) == 5, cron


def test_nightly_runs_every_day_and_stage_every_week() -> None:
    (nightly,) = (entry["cron"] for entry in triggers(load(NIGHTLY))["schedule"])
    assert nightly.split()[2:] == ["*", "*", "*"]
    (weekly,) = (entry["cron"] for entry in triggers(load(STAGE))["schedule"])
    day_of_month, month, day_of_week = weekly.split()[2:]
    assert (day_of_month, month) == ("*", "*")
    assert day_of_week.isdigit()


def test_stage_scan_runs_on_tuesday_at_three_utc() -> None:
    """Owner request (2026-11-09): the weekly STAGE scan runs on Tuesdays at 03:00 UTC."""
    (weekly,) = (entry["cron"] for entry in triggers(load(STAGE))["schedule"])
    assert weekly == "0 3 * * 2"


def test_permissions_are_minimal(workflow_path: Path) -> None:
    workflow = load(workflow_path)
    assert workflow["permissions"] == {"contents": "read"}
    for name, job in workflow["jobs"].items():
        granted = job.get("permissions", {})
        assert isinstance(granted, dict), name
        for scope, level in granted.items():
            assert ALLOWED_PERMISSIONS.get(scope) == level, f"{name}: {scope}: {level}"


def test_only_the_report_job_writes_issues(workflow_path: Path) -> None:
    jobs = load(workflow_path)["jobs"]
    writers = {name for name, job in jobs.items() if "issues" in job.get("permissions", {})}
    assert writers == {"report"}


def test_actions_and_images_are_pinned(workflow_path: Path) -> None:
    workflow = load(workflow_path)
    for step in all_steps(workflow):
        if "uses" in step:
            uses = step["uses"]
            if uses.split("/", 1)[0] in GITHUB_OWNERS:
                assert PINNED_ACTION.match(uses), uses
            else:
                assert SHA_PINNED_ACTION.match(uses), f"{uses}: pin by full commit SHA"
    for line in workflow_path.read_text().splitlines():
        owner = line.split("uses:", 1)[1].strip().split("/", 1)[0] if "uses:" in line else ""
        if owner and owner not in GITHUB_OWNERS:
            assert SHA_PIN_LINE.search(line), f"{line.strip()}: SHA pin needs a '# vX.Y.Z' comment"
    for job in workflow["jobs"].values():
        for service in job.get("services", {}).values():
            tag = service["image"].rsplit(":", 1)[-1]
            assert tag.split(".")[0].isdigit(), service["image"]


def test_scanner_images_are_pinned_to_a_version() -> None:
    text = SCAN_SCRIPT.read_text() + NIGHTLY.read_text()
    images = re.findall(r"IMAGE[=:]\s*\"?([\w.-]+/[\w./-]+:[\w.-]+)", text)
    assert any("zap" in image for image in images), images
    assert any("nuclei" in image for image in images), images
    assert any("osv-scanner" in image for image in images), images
    for image in images:
        assert PINNED_IMAGE.match(image), image
        assert image.rsplit(":", 1)[1] not in MOVING_TAGS, image


def test_no_hard_coded_urls_or_hosts() -> None:
    for path in (NIGHTLY, STAGE, SCAN_SCRIPT):
        text = path.read_text()
        assert not re.search(r"https?://", text), path.name
        for host in re.findall(r"://(?:[^@/\s]*@)?([^/:\s\"']+)", text):
            assert host == "localhost", f"{path.name}: {host}"
        assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", text), path.name


def scan_job_guard(job: dict[str, Any], secret: str) -> None:
    """The secret reaches only the guard and the scan step; the scan runs only past the guard."""
    expression = "${{ secrets." + secret + " }}"
    assert "TARGET_URL" not in job.get("env", {})
    steps = job["steps"]
    (guard,) = (step for step in steps if step.get("id") == "target")
    assert guard["env"]["TARGET_URL"] == expression
    run = guard["run"]
    assert '-z "${TARGET_URL}"' in run
    assert "::notice::skipped: no target" in run
    assert "scanned=false" in run
    assert "scanned=true" in run
    # The guard never prints the target or anything derived from it.
    for line in run.splitlines():
        if "echo" in line:
            assert "TARGET_URL" not in line, line
    assert "exit 1" not in run
    (scan,) = (step for step in steps if step.get("id") == "scan")
    assert scan["if"] == "${{ steps.target.outputs.scanned == 'true' }}"
    assert scan["env"]["TARGET_URL"] == expression
    assert job["outputs"]["scanned"] == "${{ steps.target.outputs.scanned }}"
    for step in steps:
        if step not in (guard, scan):
            assert secret not in yaml.safe_dump(step), step
    assert secret not in str(job.get("name", ""))


def test_targets_come_from_secrets_never_from_variables() -> None:
    text = STAGE.read_text()
    for name in ("STAGE_URL", "LIVE_SCAN_URL"):
        assert f"vars.{name}" not in text, name
        assert f"secrets.{name}" in text, name
    # Secrets are not available in a job-level `if`: the guard lives inside the job.
    for name, job in load(STAGE)["jobs"].items():
        assert "secrets." not in str(job.get("if", "")), name


def test_jobs_that_read_target_secrets_run_only_on_main() -> None:
    """Repository secrets are readable from any branch (workflow_dispatch takes any ref): a job
    that reads a target URL must not run off `main`, or a branch could print a transformed URL."""
    readers = []
    for name, job in load(STAGE)["jobs"].items():
        dumped = yaml.safe_dump(job)
        if any(f"secrets.{secret}" in dumped for secret in ("STAGE_URL", "LIVE_SCAN_URL")):
            readers.append(name)
            assert ON_MAIN in str(job.get("if", "")), name
    assert set(readers) == {"stage", "live"}


def test_stage_job_guards_on_the_stage_secret() -> None:
    job = load(STAGE)["jobs"]["stage"]
    assert job["if"] == "${{ " + ON_MAIN + " }}"
    scan_job_guard(job, "STAGE_URL")


def test_live_scan_needs_the_opt_in_variable_and_the_secret() -> None:
    jobs = load(STAGE)["jobs"]
    job = jobs["live"]
    assert job["if"] == "${{ " + ON_MAIN + " && vars.ALLOW_LIVE_SCAN == 'true' }}"
    scan_job_guard(job, "LIVE_SCAN_URL")
    # No other job ever reads the LIVE URL.
    for name, other in jobs.items():
        if name != "live":
            assert "LIVE_SCAN_URL" not in yaml.safe_dump(other), name


def test_report_job_never_sees_a_target() -> None:
    report = load(STAGE)["jobs"]["report"]
    dumped = yaml.safe_dump(report)
    for name in ("STAGE_URL", "LIVE_SCAN_URL", "TARGET_URL"):
        assert name not in dumped, name
    # A scan skipped for lack of a target counts as skipped, not as a green scan.
    assert "needs.stage.outputs.scanned" in dumped
    assert "needs.live.outputs.scanned" in dumped


def test_scans_go_through_the_script_that_masks_the_target() -> None:
    script = SCAN_SCRIPT.read_text()
    assert "::add-mask::${TARGET_URL}" in script
    assert script.index("::add-mask::") < script.index("docker run")
    assert "set -x" not in script
    # Scanner logs name hosts and paths that a mask of the full URL would not cover: they go
    # to files on the runner, and the log shows only counts and exit codes.
    for command in re.findall(r"^docker run .*(?:\n\s+.*)*", script, re.M):
        assert '> "$OUT/' in command, command
    for line in script.splitlines():
        if line.lstrip().startswith("echo") and "add-mask" not in line:
            assert "TARGET_URL" not in line, line
    for name in ("stage", "live"):
        runs = [step.get("run", "") for step in load(STAGE)["jobs"][name]["steps"]]
        assert any("deploy/ci/stage-scan.sh" in run for run in runs), name


def test_stage_scan_reports_are_never_uploaded() -> None:
    # The ZAP and nuclei reports name the server: a public repository's artifacts are public.
    for step in all_steps(load(STAGE)):
        assert "upload-artifact" not in step.get("uses", ""), step


def test_nightly_runs_the_slow_suites_and_a_fresh_audit() -> None:
    workflow = load(NIGHTLY)
    text = NIGHTLY.read_text()
    assert "make test-security-nightly" in text
    recipe = re.search(r"^test-security-nightly:.*\n((?:\t.*\n?)+)", MAKEFILE.read_text(), re.M)
    assert recipe
    assert "-m security_slow" in recipe.group(1)
    assert "tests/security" in recipe.group(1)
    assert workflow["env"]["HYPOTHESIS_PROFILE"] == "nightly"
    assert "make audit" in text
    assert "osv-scanner" in text
    assert "uv.lock" in text
    services = workflow["jobs"]["tests"]["services"]
    assert services["postgres"]["image"].startswith("postgres:")


def test_security_slow_marker_is_registered() -> None:
    markers = tomllib.loads(PYPROJECT.read_text())["tool"]["pytest"]["ini_options"]["markers"]
    assert any(marker.startswith("security_slow:") for marker in markers)


def test_report_job_runs_after_every_scan_only_on_main(workflow_path: Path) -> None:
    jobs = load(workflow_path)["jobs"]
    report = jobs["report"]
    assert set(report["needs"]) == set(jobs) - {"report"}
    assert "always()" in report["if"]
    assert "github.ref == 'refs/heads/main'" in report["if"]
    runs = " ".join(step.get("run", "") for step in report["steps"])
    assert "tools/security_report.py" in runs
    assert report["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"


def test_release_doc_has_the_security_schedule() -> None:
    text = RELEASE_DOC.read_text()
    assert "## Security schedule" in text
    section = text.split("## Security schedule", 1)[1]
    for needle in (
        "security-nightly",
        "security-stage",
        "STAGE_URL",
        "LIVE_SCAN_URL",
        "ALLOW_LIVE_SCAN",
        "workflow_dispatch",
        "#86",
    ):
        assert needle in section, needle


# --- the issue report (tools/security_report.py) -----------------------------------------------


def junit(tmp_path: Path, cases: list[tuple[str, str, str | None]]) -> Path:
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for classname, name, outcome in cases:
        case = ET.SubElement(suite, "testcase", classname=classname, name=name)
        if outcome:
            ET.SubElement(case, outcome, message="boom")
    path = tmp_path / "junit.xml"
    path.write_bytes(ET.tostring(root))
    return path


def test_junit_summary_counts_and_names_failures(tmp_path: Path) -> None:
    path = junit(
        tmp_path,
        [
            ("tests.security.unit.test_a", "test_ok", None),
            ("tests.security.unit.test_a", "test_broken", "failure"),
            ("tests.security.integration.test_b", "test_crash", "error"),
            ("tests.security.unit.test_a", "test_later", "skipped"),
        ],
    )
    summary = security_report.junit_summary([path])
    assert summary.total == 4
    assert summary.failed == 1
    assert summary.errors == 1
    assert summary.skipped == 1
    assert summary.names == [
        "tests.security.unit.test_a::test_broken",
        "tests.security.integration.test_b::test_crash",
    ]


def test_missing_junit_file_is_not_an_error(tmp_path: Path) -> None:
    summary = security_report.junit_summary([tmp_path / "absent.xml"])
    assert (summary.total, summary.failed, summary.names) == (0, 0, [])


def test_body_has_counts_and_names_but_never_a_server(tmp_path: Path) -> None:
    path = junit(
        tmp_path,
        [
            ("t.m", "test_ssrf[http://10.0.0.1/admin]", "failure"),
            ("t.m", "test_x[fe80::1-stage.example.org]", "failure"),
        ],
    )
    body = security_report.issue_body(
        workflow="security-stage",
        run_url="RUN-LINK",
        results={"stage": "failure", "audit": "success"},
        counts={"zap_high": "2", "nuclei_medium": "1"},
        summary=security_report.junit_summary([path]),
    )
    assert "RUN-LINK" in body
    assert "stage: failure" in body
    assert "zap_high: 2" in body
    assert "test_x" in body
    assert "10.0.0.1" not in body
    assert "fe80" not in body
    assert "example.org" not in body
    assert "t.m::test_x[<addr>-<host>]" in body
    assert "http://" not in body


def test_counts_must_be_numbers() -> None:
    with pytest.raises(ValueError, match="count"):
        security_report.parse_counts(["zap_high=stage.example"])
    assert security_report.parse_counts(["zap_high=3", "nuclei_high=0"]) == {
        "zap_high": "3",
        "nuclei_high": "0",
    }


def test_issue_title_carries_the_workflow_prefix() -> None:
    title = security_report.issue_title("security-nightly")
    assert title.startswith(security_report.title_prefix("security-nightly"))
    assert security_report.title_prefix("security-nightly") == "[security-nightly]"


def test_lane_board_line_follows_the_owner_format() -> None:
    line = security_report.board_line("security-nightly", "RUN-LINK")
    assert line == "to lane-a: security-nightly failed: RUN-LINK"


def test_find_issue_matches_label_and_prefix() -> None:
    issues = [
        {"number": 3, "title": "[security-stage] scheduled run failing"},
        {"number": 7, "title": "[security-nightly] scheduled run failing"},
    ]
    assert security_report.pick_issue(issues, "security-nightly") == 7
    assert security_report.pick_issue(issues[:1], "security-nightly") is None
