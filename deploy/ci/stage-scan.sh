#!/usr/bin/env bash
# SEC.5: external scans of a deployed environment (OWASP ZAP baseline, then nuclei).
#
# Runs in the security-stage workflow only. The target comes from TARGET_URL, which the workflow
# fills from a repository secret (secrets.STAGE_URL, or secrets.LIVE_SCAN_URL behind a second
# opt-in): it is never written in the repository. GitHub masks the secret, and the script masks it
# again before any tool runs. A mask covers only the full URL, while the scanners print hosts and
# paths on their own, so their output goes to files on the runner, never to the log: the log shows
# exit codes and counts only. Reports stay on the runner too (artifacts of a public repository are
# public), and only counts reach GITHUB_OUTPUT. Never turn on shell tracing here.
#
# Exit codes: 0 clean, 1 findings, 3 scanner error (the message says ZAP or nuclei).
# Fails when ZAP reports a medium or high risk alert, when nuclei finds a medium, high or critical
# issue, or when a scanner itself errors. Both scanners run even when the first one fails.
set -euo pipefail

: "${TARGET_URL:?TARGET_URL is not set}"
echo "::add-mask::${TARGET_URL}"

ZAP_IMAGE="zaproxy/zap-stable:2.17.0"
NUCLEI_IMAGE="projectdiscovery/nuclei:v3.11.1"
# Pinned nuclei-templates release (tag and the commit it must resolve to). Bump monthly, in a PR.
NUCLEI_TEMPLATES_REPO="projectdiscovery/nuclei-templates"
NUCLEI_TEMPLATES_TAG="v10.5.1"
NUCLEI_TEMPLATES_SHA="79fb682760ab3d5b0e22afbb44709353cbebf023"
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
  zap-baseline.py -t "$TARGET_URL" -J zap.json -I -m 3 > "$OUT/zap/zap.log" 2>&1
zap_exit=$?
set -e
if [ "$zap_exit" -ne 0 ] && [ "$zap_exit" -ne 2 ]; then
  echo "::error::ZAP scanner error: exit code $zap_exit"
  status=3
fi

REPORT="$(dirname "${BASH_SOURCE[0]}")/../../tools/stage_scan_report.py"
if ! zap_problem="$(python3 -I "$REPORT" zap-check "$OUT/zap/zap.json")"; then
  echo "::error::ZAP scanner error: $zap_problem"
  status=3
fi
python3 -I "$REPORT" zap-list "$OUT/zap/zap.json"
read -r zap_high zap_medium zap_low < <(python3 -I "$REPORT" zap-counts "$OUT/zap/zap.json")

# nuclei templates: the image ships none, and -disable-update-check also skips the first download.
# They are cloned at a pinned release tag and commit SHA and passed with -t, so a scan never runs
# unreviewed templates. A tag can be moved: the SHA is checked after the clone, and a mismatch is a
# scanner error. Bump both values monthly, in a PR (docs/dev/release.md, "Security schedule").
set +e
git clone --quiet --depth 1 --branch "$NUCLEI_TEMPLATES_TAG" \
  "https://github.com/$NUCLEI_TEMPLATES_REPO.git" "$OUT/templates" > "$OUT/nuclei/update.log" 2>&1
clone_exit=$?
set -e
if [ "$clone_exit" -ne 0 ]; then
  echo "::error::nuclei scanner error: template clone failed with exit code $clone_exit"
  status=3
  rm -rf "$OUT/templates"
  mkdir -p "$OUT/templates"
elif [ "$(git -C "$OUT/templates" rev-parse HEAD)" != "$NUCLEI_TEMPLATES_SHA" ]; then
  echo "::error::nuclei scanner error: templates commit does not match the pinned SHA"
  status=3
  rm -rf "$OUT/templates"
  mkdir -p "$OUT/templates"
fi
template_count="$(find "$OUT/templates" -type f -name '*.yaml' | wc -l | tr -d ' ')"
echo "nuclei templates ($NUCLEI_TEMPLATES_TAG): $template_count"
if [ "$template_count" -eq 0 ]; then
  echo "::error::nuclei scanner error: 0 templates loaded, the scan would test nothing"
  status=3
fi

# Default templates without intrusive, fuzzing or denial-of-service ones, rate limited.
set +e
docker run --rm --network host -v "$OUT/nuclei:/out:rw" -v "$OUT/templates:/templates:ro" \
  "$NUCLEI_IMAGE" -u "$TARGET_URL" -t /templates -severity medium,high,critical \
  -etags dos,fuzz,intrusive -rate-limit 10 -disable-update-check -no-color -jsonl \
  -o /out/nuclei.jsonl > "$OUT/nuclei/nuclei.log" 2>&1
nuclei_exit=$?
set -e
# nuclei exits 0 on a clean run, with or without findings: any other code is a scanner error.
if [ "$nuclei_exit" -ne 0 ]; then
  nuclei_class="$(python3 -I "$REPORT" nuclei-error "$OUT/nuclei/nuclei.log")"
  echo "::error::nuclei scanner error: exit code $nuclei_exit, error class $nuclei_class"
  status=3
fi

python3 -I "$REPORT" nuclei-list "$OUT/nuclei/nuclei.jsonl"
read -r nuclei_critical nuclei_high nuclei_medium < <(
  python3 -I "$REPORT" nuclei-counts "$OUT/nuclei/nuclei.jsonl")

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
  echo "::error::the scan found medium or higher issues (the reports stay on the runner)"
  if [ "$status" -eq 0 ]; then status=1; fi
fi
exit "$status"
