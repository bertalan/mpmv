#!/usr/bin/env bash
# SEC.5: external scans of a deployed environment (OWASP ZAP baseline, then nuclei).
#
# Runs in the security-stage workflow only. The target comes from TARGET_URL, which the workflow
# fills from a repository variable (vars.STAGE_URL, or vars.LIVE_SCAN_URL behind a second opt-in):
# it is never written in the repository. The script masks it before any tool runs, keeps the
# reports on the runner (artifacts of a public repository are public) and writes only counts to
# GITHUB_OUTPUT.
#
# Fails when ZAP reports a medium or high risk alert, when nuclei finds a medium, high or critical
# issue, or when a scanner itself errors. Both scanners run even when the first one fails.
set -euo pipefail

: "${TARGET_URL:?TARGET_URL is not set}"
echo "::add-mask::${TARGET_URL}"

ZAP_IMAGE="zaproxy/zap-stable:2.17.0"
NUCLEI_IMAGE="projectdiscovery/nuclei:v3.11.1"
OUT="${RUNNER_TEMP:-/tmp}/stage-scan"
OUTPUT_FILE="${GITHUB_OUTPUT:-/dev/null}"

mkdir -p "$OUT/zap" "$OUT/nuclei"
# Both images run as a non-root user and write their reports into these mounts.
chmod 0777 "$OUT/zap" "$OUT/nuclei"

status=0

# ZAP baseline: passive scan after a short spider. -I: warnings alone do not fail the tool; the
# risk counts below decide. Exit codes: 0 clean, 1 FAIL rules, 2 warnings, 3 scanner error.
set +e
docker run --rm --network host -v "$OUT/zap:/zap/wrk:rw" "$ZAP_IMAGE" \
  zap-baseline.py -t "$TARGET_URL" -J zap.json -I -m 3
zap_exit=$?
set -e
if [ "$zap_exit" -ne 0 ] && [ "$zap_exit" -ne 2 ]; then
  echo "::error::ZAP baseline exited with $zap_exit"
  status=1
fi

read -r zap_high zap_medium zap_low < <(python3 - "$OUT/zap/zap.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
risks = {"3": 0, "2": 0, "1": 0}
if path.is_file():
    for site in json.loads(path.read_text()).get("site", []):
        for alert in site.get("alerts", []):
            code = str(alert.get("riskcode", ""))
            if code in risks:
                risks[code] += 1
print(risks["3"], risks["2"], risks["1"])
PY
)

# nuclei: default templates without intrusive, fuzzing or denial-of-service ones, rate limited.
set +e
docker run --rm --network host -v "$OUT/nuclei:/out:rw" "$NUCLEI_IMAGE" \
  -u "$TARGET_URL" -severity medium,high,critical -etags dos,fuzz,intrusive \
  -rate-limit 10 -disable-update-check -no-color -jsonl -o /out/nuclei.jsonl
nuclei_exit=$?
set -e
if [ "$nuclei_exit" -ne 0 ]; then
  echo "::error::nuclei exited with $nuclei_exit"
  status=1
fi

read -r nuclei_critical nuclei_high nuclei_medium < <(python3 - "$OUT/nuclei/nuclei.jsonl" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
found = {"critical": 0, "high": 0, "medium": 0}
if path.is_file():
    for line in path.read_text().splitlines():
        if line.strip():
            severity = json.loads(line).get("info", {}).get("severity", "")
            if severity in found:
                found[severity] += 1
print(found["critical"], found["high"], found["medium"])
PY
)

{
  echo "zap_high=$zap_high"
  echo "zap_medium=$zap_medium"
  echo "zap_low=$zap_low"
  echo "nuclei_critical=$nuclei_critical"
  echo "nuclei_high=$nuclei_high"
  echo "nuclei_medium=$nuclei_medium"
} >> "$OUTPUT_FILE"

echo "ZAP alerts: high $zap_high, medium $zap_medium, low $zap_low"
echo "nuclei findings: critical $nuclei_critical, high $nuclei_high, medium $nuclei_medium"

if [ $((zap_high + zap_medium + nuclei_critical + nuclei_high + nuclei_medium)) -gt 0 ]; then
  echo "::error::the scan found medium or higher issues (see the tool output above)"
  status=1
fi
exit "$status"
