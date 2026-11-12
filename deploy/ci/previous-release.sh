#!/usr/bin/env bash
# The release before this build (OPS.11), for the parity smoke's upgrade path.
#
#   deploy/ci/previous-release.sh <repository> [<tag being built>]
#
# Prints the version (no "v") of the highest release tag vX.Y.Z of the repository's origin that is
# lower than the version being built (for vX.Y.Z-rc.N, lower than X.Y.Z), or the highest one when
# the build is not a version tag (a pull request: the latest release). Prints nothing when there is no such tag. The tags are read from the remote, so a
# shallow checkout without tags works; pre-release and unrelated tags never count. Exits non-zero
# when the remote cannot be read: that is not the same as having no release.
set -euo pipefail

repo="${1:?usage: previous-release.sh <repository> [<tag being built>]}"
building="${2:-}"

# The version a build is for: vX.Y.Z, or vX.Y.Z-suffix (a pre-release) which upgrades from the
# release below X.Y.Z. Anything else (a pull request, a branch) has no version: latest release.
core=""
if [[ "$building" =~ ^(v[0-9]+\.[0-9]+\.[0-9]+)(-.+)?$ ]]; then core="${BASH_REMATCH[1]}"; fi

refs="$(git -C "$repo" ls-remote --tags --refs origin 'v*')"
previous=""
# The build's own version joins the list, so the walk stops at its place even when no such release
# tag exists (a pre-release).
while IFS= read -r tag; do
  [ "$tag" != "$core" ] || break
  previous="$tag"
done < <({
  sed -n 's|.*\trefs/tags/\(v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)$|\1|p' <<<"$refs"
  if [ -n "$core" ]; then echo "$core"; fi
} | sort -uV)
if [ -n "$previous" ]; then echo "${previous#v}"; fi
