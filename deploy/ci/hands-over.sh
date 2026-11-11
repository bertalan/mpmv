#!/usr/bin/env bash
# Does a release's mpmv-deploy hand a deploy over to the target release's own script (OPS.11)?
#
#   deploy/ci/hands-over.sh <path to an mpmv-deploy script>     exit 0 if it does, 1 if not
#
# Scripts from before the hand-over (release 0.1.7 and older) install the target's units with
# their own, older logic, so docs/dev/release.md has the operator refresh the script once, from the
# target's image, before such a deploy. The smoke does the same. The test is the script's own
# hand-over message, which a script with the re-exec carries and an older one cannot.
set -euo pipefail

script="${1:?usage: hands-over.sh <mpmv-deploy script>}"
grep -qF 'continuing with the deploy script of' "$script"
