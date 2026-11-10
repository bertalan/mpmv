"""SEC.5: the STAGE scan log shows alert ids and separates nuclei errors from findings.

The fixtures are synthetic: no real host, path, evidence or parameter appears in them, and the
tests check that such fields never reach the printed output.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "stage_scan_report.py"
SCRIPT = ROOT / "deploy" / "ci" / "stage-scan.sh"

SECRET_HOST = "target.invalid"

ZAP = {
    "site": [
        {
            "@name": f"https://{SECRET_HOST}",
            "alerts": [
                {
                    "pluginid": "10038",
                    "riskcode": "2",
                    "name": "Content Security Policy Header Not Set",
                    "instances": [
                        {"uri": f"https://{SECRET_HOST}/a", "evidence": "secret-evidence"}
                    ],
                },
                {"pluginid": "10020", "riskcode": "1", "name": "Missing Anti-clickjacking Header"},
                {"pluginid": "10021", "riskcode": "1", "name": "X-Content-Type-Options Missing"},
                {"pluginid": "10049", "riskcode": "0", "name": "Informational thing"},
            ],
        }
    ]
}


def run(command: str, path: Path) -> str:
    result = subprocess.run(  # noqa: S603 - fixed argv, synthetic fixtures
        [sys.executable, "-I", str(TOOL), command, str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@pytest.fixture
def zap_report(tmp_path: Path) -> Path:
    path = tmp_path / "zap.json"
    path.write_text(json.dumps(ZAP))
    return path


def test_zap_list_prints_plugin_id_risk_and_name_only(zap_report: Path) -> None:
    out = run("zap-list", zap_report)
    assert "plugin 10038, risk medium, Content Security Policy Header Not Set" in out
    assert "plugin 10020, risk low, Missing Anti-clickjacking Header" in out
    assert "10049" not in out  # informational alerts are not triaged
    assert SECRET_HOST not in out
    assert "secret-evidence" not in out


def test_zap_counts_match_the_listing(zap_report: Path) -> None:
    assert run("zap-counts", zap_report).split() == ["0", "1", "2"]


def test_missing_reports_count_as_zero(tmp_path: Path) -> None:
    assert run("zap-counts", tmp_path / "none.json").split() == ["0", "0", "0"]
    assert run("nuclei-counts", tmp_path / "none.jsonl").split() == ["0", "0", "0"]
    assert run("nuclei-list", tmp_path / "none.jsonl") == ""


def test_nuclei_list_prints_template_id_and_severity_only(tmp_path: Path) -> None:
    lines = [
        {
            "template-id": "synthetic-exposure",
            "info": {"severity": "high", "name": "n"},
            "matched-at": f"https://{SECRET_HOST}/x",
        },
        {"template-id": "synthetic-info", "info": {"severity": "info"}},
        {"template-id": "synthetic-medium", "info": {"severity": "medium"}},
    ]
    path = tmp_path / "nuclei.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + '\n{"template-id": "cut')
    out = run("nuclei-list", path)
    assert "template synthetic-exposure, severity high" in out
    assert "template synthetic-medium, severity medium" in out
    assert "synthetic-info" not in out
    assert SECRET_HOST not in out
    assert run("nuclei-counts", path).split() == ["0", "1", "1"]


@pytest.mark.parametrize(
    ("log", "expected"),
    [
        ("[FTL] Could not run nuclei: no templates provided for scan", "no-templates"),
        ("[ERR] failed to download templates: rate limit exceeded", "template-download"),
        (f"[ERR] dial tcp {SECRET_HOST}:443: i/o timeout", "network"),
        ("something odd", "unknown"),
    ],
)
def test_nuclei_error_class_names_a_class_and_never_the_log(
    tmp_path: Path, log: str, expected: str
) -> None:
    path = tmp_path / "nuclei.log"
    path.write_text(log)
    out = run("nuclei-error", path)
    assert out.strip() == expected
    assert SECRET_HOST not in out


def test_script_separates_scanner_errors_from_findings() -> None:
    text = SCRIPT.read_text()
    assert "nuclei scanner error" in text
    assert "ZAP scanner error" in text
    assert "status=3" in text
    # Findings alone exit 1; a scanner error keeps its own code.
    assert 'if [ "$status" -eq 0 ]; then status=1; fi' in text
    # Templates come from a pinned tag and SHA, never from a floating update.
    assert "NUCLEI_TEMPLATES_TAG" in text
    assert "NUCLEI_TEMPLATES_SHA" in text
    assert "-update-templates" not in text


def test_clean_collapses_every_run_of_colons(tmp_path: Path) -> None:
    path = tmp_path / "zap.json"
    alert = {"pluginid": "1", "riskcode": "2", "name": "a:::b::error::c"}
    path.write_text(json.dumps({"site": [{"alerts": [alert]}]}))
    assert "::" not in run("zap-list", path)


def test_zap_alert_names_drop_url_and_host_like_text(tmp_path: Path) -> None:
    name = f"Leak at https://{SECRET_HOST}/p?q=1 and www.{SECRET_HOST} and {SECRET_HOST}:8443"
    alert = {"pluginid": "7", "riskcode": "2", "name": name}
    path = tmp_path / "zap.json"
    path.write_text(json.dumps({"site": [{"alerts": [alert]}]}))
    out = run("zap-list", path)
    assert "plugin 7, risk medium, Leak at" in out
    assert SECRET_HOST not in out
    assert "://" not in out


def test_zap_check_flags_a_missing_or_malformed_report(tmp_path: Path, zap_report: Path) -> None:
    def check(path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed argv, synthetic fixtures
            [sys.executable, "-I", str(TOOL), "zap-check", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )

    assert check(zap_report).returncode == 0
    bad = tmp_path / "bad.json"
    bad.write_text(f'{{"site": [{{"@name": "https://{SECRET_HOST}", ')
    result = check(bad)
    assert result.returncode == 3
    assert SECRET_HOST not in result.stdout + result.stderr
    assert check(tmp_path / "none.json").returncode == 3
    # The listing and the counts do not crash on a malformed report.
    assert run("zap-counts", bad).split() == ["0", "0", "0"]
    assert run("zap-list", bad) == ""


FAKE_DOCKER = """#!/usr/bin/env bash
# Stands in for docker: writes the reports the scanners would write, from FAKE_* variables.
args="$*"
mount() { for a in "$@"; do case "$a" in *":$MNT:"*) echo "${a%%:*}";; esac; done; }
case "$args" in
  *zap-baseline.py*)
    MNT=/zap/wrk; dir=$(mount "$@")
    [ -n "${FAKE_ARGS_LOG:-}" ] && printf '%s\\n' "$args" > "$FAKE_ARGS_LOG"
    [ -n "${FAKE_ZAP_JSON:-}" ] && printf '%s' "$FAKE_ZAP_JSON" > "$dir/zap.json"
    exit "${FAKE_ZAP_EXIT:-0}";;
  *)
    MNT=/out; dir=$(mount "$@")
    [ -n "${FAKE_NUCLEI_JSONL:-}" ] && printf '%s\\n' "$FAKE_NUCLEI_JSONL" > "$dir/nuclei.jsonl"
    exit "${FAKE_NUCLEI_EXIT:-0}";;
esac
"""

FAKE_VCS = """#!/usr/bin/env bash
# Stands in for the version control tool: a clone makes a templates dir, rev-parse a commit id.
if [ "$1" = clone ]; then
  [ "${FAKE_CLONE_EXIT:-0}" = 0 ] || exit "$FAKE_CLONE_EXIT"
  dest="${@: -1}"; mkdir -p "$dest"
  [ "${FAKE_TEMPLATES:-1}" = 1 ] && echo "id: synthetic" > "$dest/synthetic.yaml"
  exit 0
fi
echo "$FAKE_VCS_SHA"
"""

PINNED_SHA = re.search(r'NUCLEI_TEMPLATES_SHA="([0-9a-f]{40})"', SCRIPT.read_text())

CLEAN_ZAP = json.dumps({"site": [{"alerts": []}]})
MEDIUM_NUCLEI = json.dumps({"template-id": "synthetic-m", "info": {"severity": "medium"}})


def run_script(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    vcs = bin_dir / "git"
    vcs.write_text(FAKE_VCS)
    vcs.chmod(0o755)
    assert PINNED_SHA
    full_env = {
        "FAKE_VCS_SHA": PINNED_SHA.group(1),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "TARGET_URL": f"https://{SECRET_HOST}",
        "RUNNER_TEMP": str(tmp_path / "runner"),
        "GITHUB_OUTPUT": str(tmp_path / "out.txt"),
        "FAKE_ZAP_JSON": CLEAN_ZAP,
        **env,
    }
    return subprocess.run(  # noqa: S603 - fixed argv, fake docker on PATH
        ["/bin/bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=full_env,
    )


def test_script_exit_0_on_a_clean_run(tmp_path: Path) -> None:
    result = run_script(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_script_exit_1_on_findings_only(tmp_path: Path) -> None:
    result = run_script(tmp_path, FAKE_NUCLEI_JSONL=MEDIUM_NUCLEI)
    assert result.returncode == 1, result.stdout
    assert "template synthetic-m, severity medium" in result.stdout
    assert "scanner error" not in result.stdout


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"FAKE_NUCLEI_EXIT": "1"}, "nuclei scanner error"),
        ({"FAKE_ZAP_EXIT": "3"}, "ZAP scanner error"),
        ({"FAKE_ZAP_JSON": '{"site": ['}, "ZAP scanner error: report unreadable"),
        ({"FAKE_TEMPLATES": "0"}, "nuclei scanner error: 0 templates"),
        ({"FAKE_CLONE_EXIT": "1"}, "nuclei scanner error: template clone"),
        ({"FAKE_VCS_SHA": "0" * 40}, "does not match the pinned SHA"),
    ],
)
def test_script_exit_3_on_a_scanner_error(
    tmp_path: Path, env: dict[str, str], message: str
) -> None:
    result = run_script(tmp_path, **env)
    assert result.returncode == 3, result.stdout + result.stderr
    assert message in result.stdout
    shown = "".join(line for line in result.stdout.splitlines(True) if "add-mask" not in line)
    assert SECRET_HOST not in shown + result.stderr
    # The counts still reach GITHUB_OUTPUT, so the report job has them.
    assert "zap_high=" in (tmp_path / "out.txt").read_text()


def test_a_scanner_error_wins_over_findings(tmp_path: Path) -> None:
    result = run_script(tmp_path, FAKE_NUCLEI_JSONL=MEDIUM_NUCLEI, FAKE_NUCLEI_EXIT="1")
    assert result.returncode == 3


RULES = SCRIPT.parent / "zap-rules.tsv"
ACCEPTED_IDS = {"90004", "10110"}


def rule_lines() -> list[list[str]]:
    lines = [ln for ln in RULES.read_text().splitlines() if ln.strip() and ln[0] != "#"]
    return [ln.split("\t") for ln in lines]


def test_rules_file_ignores_only_the_documented_accepts() -> None:
    rules = rule_lines()
    assert rules
    assert all(len(rule) >= 2 and rule[1] == "IGNORE" for rule in rules)
    assert {rule[0] for rule in rules} == ACCEPTED_IDS
    assert len({rule[0] for rule in rules}) == len(rules), "one line per plugin id"
    doc = (SCRIPT.parents[2] / "docs" / "dev" / "security-scans.md").read_text()
    for plugin_id in ACCEPTED_IDS:
        assert plugin_id in doc


def test_script_passes_the_read_only_rules_file_to_zap(tmp_path: Path) -> None:
    log = tmp_path / "args.log"
    result = run_script(tmp_path, FAKE_ARGS_LOG=str(log))
    assert result.returncode == 0, result.stdout + result.stderr
    args = log.read_text()
    assert f"{RULES}:/zap/wrk/zap-rules.tsv:ro" in args
    assert "-c zap-rules.tsv" in args
    assert "-J zap.json" in args


def test_script_still_fails_on_a_medium_alert_with_the_rules_wired(tmp_path: Path) -> None:
    alert = {"pluginid": "10098", "riskcode": "2", "name": "X"}
    result = run_script(tmp_path, FAKE_ZAP_JSON=json.dumps({"site": [{"alerts": [alert]}]}))
    assert result.returncode == 1, result.stdout
