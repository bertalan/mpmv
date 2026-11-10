"""SEC.5: the STAGE scan log shows alert ids and separates nuclei errors from findings.

The fixtures are synthetic: no real host, path, evidence or parameter appears in them, and the
tests check that such fields never reach the printed output.
"""

import json
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
    # Templates are downloaded explicitly, since -disable-update-check skips the first download.
    assert "-update-templates" in text
