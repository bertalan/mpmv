"""SEC.5: read the ZAP and nuclei reports of a STAGE scan without leaking the target.

Called by deploy/ci/stage-scan.sh. The reports name the scanned server, so this module prints
only what is safe in a public log: ZAP plugin id, risk and alert name; nuclei template id and
severity; counts; and, for a failed nuclei run, a coarse error class taken from its log. URLs,
evidence, parameters and raw scanner output are never printed.

    python3 -I tools/stage_scan_report.py zap-list REPORT      alert lines
    python3 -I tools/stage_scan_report.py zap-counts REPORT    "high medium low"
    python3 -I tools/stage_scan_report.py nuclei-list REPORT   finding lines
    python3 -I tools/stage_scan_report.py nuclei-counts REPORT "critical high medium"
    python3 -I tools/stage_scan_report.py nuclei-error LOG     error class, one word
"""

import json
import re
import sys
from pathlib import Path

ZAP_RISKS = {"3": "high", "2": "medium", "1": "low"}
NUCLEI_SEVERITIES = ("critical", "high", "medium")

# Error classes, checked in order, matched case-insensitively against the nuclei log. Only the
# class name is ever printed.
NUCLEI_ERROR_CLASSES = (
    (
        "no-templates",
        r"no templates (?:provided|found|loaded)|templates? (?:not found|missing)",
    ),
    (
        "template-download",
        r"templates? (?:download|install|update)|could not (?:download|install)|"
        r"failed to (?:download|install)|rate limit",
    ),
    (
        "network",
        r"no route|connection (?:refused|reset|timed out)|i/o timeout|dial tcp|"
        r"no such host|could not connect|tls handshake",
    ),
    ("target", r"no (?:valid )?targets? (?:found|provided)|could not (?:parse|resolve)"),
)


def _clean(text: object) -> str:
    """One printable line with no workflow-command prefix."""
    line = re.sub(r"[^\x20-\x7e]", "?", str(text)).replace("::", ":")
    return line.strip()[:120]


def zap_alerts(path: Path) -> list[tuple[str, str, str]]:
    """(plugin id, risk, name) per alert; empty when the report is missing."""
    if not path.is_file():
        return []
    alerts = []
    for site in json.loads(path.read_text()).get("site", []):
        for alert in site.get("alerts", []):
            risk = ZAP_RISKS.get(str(alert.get("riskcode", "")))
            if risk:
                plugin = _clean(alert.get("pluginid", "?"))
                alerts.append((plugin, risk, _clean(alert.get("name", "?"))))
    return alerts


def nuclei_findings(path: Path) -> list[tuple[str, str]]:
    """(template id, severity) per finding; empty when the report is missing."""
    if not path.is_file():
        return []
    found = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                item = json.loads(line)
            except ValueError:
                continue  # a line cut short by a killed scanner
            severity = item.get("info", {}).get("severity", "")
            if severity in NUCLEI_SEVERITIES:
                found.append((_clean(item.get("template-id", "?")), severity))
    return found


def nuclei_error_class(path: Path) -> str:
    """A coarse class for a failed nuclei run, from its log; never the log text."""
    text = path.read_text(errors="replace") if path.is_file() else ""
    for name, pattern in NUCLEI_ERROR_CLASSES:
        if re.search(pattern, text, re.I):
            return name
    return "unknown"


def main(argv: list[str]) -> int:
    command, path = argv[1], Path(argv[2])
    if command == "zap-list":
        for plugin, risk, name in zap_alerts(path):
            print(f"ZAP alert: plugin {plugin}, risk {risk}, {name}")
    elif command == "zap-counts":
        risks = [risk for _, risk, _ in zap_alerts(path)]
        print(*(risks.count(r) for r in ("high", "medium", "low")))
    elif command == "nuclei-list":
        for template, severity in nuclei_findings(path):
            print(f"nuclei finding: template {template}, severity {severity}")
    elif command == "nuclei-counts":
        severities = [severity for _, severity in nuclei_findings(path)]
        print(*(severities.count(s) for s in NUCLEI_SEVERITIES))
    elif command == "nuclei-error":
        print(nuclei_error_class(path))
    else:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
