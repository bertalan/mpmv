#!/usr/bin/env bash
# Whether the parity smoke may skip its upgrade path (OPS.11), decided by the CI context.
#
#   deploy/ci/upgrade-policy.sh        prints "required" or "optional"
#
# required: a push to main or a release tag (v*). A missing upgrade run there is a failure.
# optional: a pull request or any other context. A missing upgrade run is a warning.
# The very first release (no earlier tag at all) is allowed in both; the smoke decides that.
set -euo pipefail

ref="${GITHUB_REF:-}"
event="${GITHUB_EVENT_NAME:-}"
if [ "$event" = push ] && { [ "$ref" = refs/heads/main ] || [[ "$ref" == refs/tags/v* ]]; }; then
  echo required
else
  echo optional
fi
