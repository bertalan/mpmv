#!/usr/bin/env bash
# The release before this build (OPS.11), for the parity smoke's upgrade path.
#
#   deploy/ci/previous-release.sh <repository> [<tag being built>]
#
# Prints the version (no "v") of the highest release tag vX.Y.Z of the repository's origin that is
# lower than the tag being built, or the highest one when none is given (a pull request: the latest
# release). Prints nothing when there is no such tag. The tags are read from the remote, so a
# shallow checkout without tags works; pre-release and unrelated tags never count. Exits non-zero
# when the remote cannot be read: that is not the same as having no release.
set -euo pipefail

repo="${1:?usage: previous-release.sh <repository> [<tag being built>]}"
building="${2:-}"

refs="$(git -C "$repo" ls-remote --tags --refs origin 'v*')"
previous=""
while IFS= read -r tag; do
  [ "$tag" != "$building" ] || break
  previous="$tag"
done < <(sed -n 's|.*\trefs/tags/\(v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)$|\1|p' <<<"$refs" | sort -V)
if [ -n "$previous" ]; then echo "${previous#v}"; fi
