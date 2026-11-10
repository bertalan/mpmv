# Releases (ARC.23)

How a commit becomes a release and reaches STAGE and LIVE. The mechanics are in
[deploy/README.md](../../deploy/README.md); this page holds the rules.

## Tagging

- Tag only a commit on `main` whose CI run is green, and name it by its full SHA:
  `git tag -a v0.2.0 <full SHA> -m v0.2.0 && git push origin v0.2.0`. Never tag a branch name
  or `HEAD`: by the time the tag is pushed, either may point elsewhere.
- Tag only the merge commit of the coherence PR described in the next section.
- Release tags are immutable. A broken release gets a new patch version, never a re-tag.

## Coherence check before a release candidate

Owner rule: every STAGE release is preceded by a coherence check on the exact commit being
released.

1. lane-a announces "release candidate" on the Lane board issue (#86). lane-b runs the
   coherence check on current `main` and opens one PR (docs and tests only).
2. lane-a merges that PR with no other merge in between, then cuts the pre-release from that
   merge commit, named by its full SHA, after green CI.
3. A major finding that the PR does not fix blocks the tag until lane-a decides. If anything
   else merges after the check, the next tag needs a new check.

## What the release workflow does

`release.yml` runs on the tag. On each architecture (amd64 and arm64, native runners) it builds
the image, runs the production parity smoke and only then pushes the image by digest; the
manifest list gets the release tag after both architectures passed.

The parity smoke ([deploy/ci/parity-smoke.sh](../../deploy/ci/parity-smoke.sh)) also runs on every
pull request inside the *Container image* check. It turns the runner into a STAGE-like host and
deploys the image with its own `mpmv-deploy`: the real Quadlet units under `systemd --user`
(converted by Podman's quadlet generator), the production Caddyfile, env files shaped like the
examples with throwaway values, PostgreSQL over TLS with `sslmode=verify-full`. It migrates,
seeds, fetches `/healthz`, the home page, a category page and a seeded `/media/` image through
Caddy, checks the containers' read-only root, capabilities and media volume, then deploys a
deliberately bad release and requires the rollback, units included, in under 60 s.

The smoke rewrites the runner's MPMV configuration and leaves it, its containers and its sysctl
changes behind, so it runs only on GitHub-hosted (ephemeral) runners: it refuses to start
elsewhere unless `MPMV_PARITY_THROWAWAY_HOST=1` declares the host disposable. Never point it at a
server or at a self-hosted runner that is reused between jobs.

## Deploying to STAGE

From a workstation, by absolute path (a non-interactive ssh session has no `~/.local/bin` on
`PATH`):

```sh
ssh <stage> '~/.local/bin/mpmv-deploy deploy 0.2.0'
```

`deploy` runs, in order, and stops at the first failure:

1. **doctor**: Podman and its quadlet generator support what the units and the script use, the env
   files hold every variable name of the release's examples (names only, values are never
   printed), the database root CA is readable, the media volume belongs to the image user, the
   disk has room, the units convert (`quadlet -dryrun`). A failure here changes nothing.
2. **units of the tag**: the units, Caddyfile and `mpmv-deploy` baked into the image under
   `/app/deploy` replace the installed ones; the previous set is kept for rollback.
3. **migrate**: the one-shot `mpmv-migrate` unit. If it fails, the previous units and image tag
   come back and nothing restarts.
4. **restart** app and worker (Caddy too when its Caddyfile or unit changed).
5. **check**: `/healthz`, the home page and one `/media/` file through Caddy on the host, by the
   site name in `caddy.env`, within 40 s; a container that restarts on its own fails at once.
6. **automatic rollback** if the check fails: the previous image and its units, restarted and
   checked again.

`~/.local/bin/mpmv-deploy doctor` and `~/.local/bin/mpmv-deploy check` run steps 1 and 5 on demand.

## Promoting to LIVE

LIVE gets only a tag that has run on STAGE without incident, and only on the owner's go. The same
tag, the same image digest: never a rebuild for LIVE.

Launch prerequisite: before the first LIVE launch, and before the account activation batches of
CORE.19 start, the sending domain has SPF, DKIM and DMARC records in place and verified (ARC.19,
[transactional email](transactional-email.md)).

## Security schedule

The security checks run at three cadences (SEC.5, owner request of 2026-11-09).

| When | Workflow | What runs |
| --- | --- | --- |
| Every pull request and push to `main` | `ci.yml` (required checks) | The fast suites (unit and integration, `tests/security` included), `make audit` (pip-audit), ruff with the `S` rules, the gitleaks secret scan |
| Every night, 02:30 UTC, on `main` | `security-nightly.yml` | `make test-security-nightly`: the `security_slow` tests (ReDoS sweep, malicious-upload corpus, long hostile-input runs) and `tests/security` again with `HYPOTHESIS_PROFILE=nightly`, against a PostgreSQL service; then pip-audit and osv-scanner on `uv.lock` against the advisories of the day, even when no code changed |
| Every Tuesday, 03:00 UTC | `security-stage.yml` | OWASP ZAP baseline and nuclei (pinned container images, no intrusive, fuzzing or denial-of-service templates) through `deploy/ci/stage-scan.sh`, against STAGE |

Run either scheduled workflow by hand from Actions > *Run workflow* (`workflow_dispatch`), or with
`gh workflow run security-nightly.yml` and `gh workflow run security-stage.yml`. Locally,
`make db` then `make test-security-nightly` runs the nightly test selection.

Settings (Settings > Secrets and variables > Actions), set by lane-a on the owner's go:

- Secret `STAGE_URL`: the STAGE base URL. While it is empty the STAGE job ends green at its first
  step with a "skipped: no target" notice, and the report counts it as skipped.
- Secret `LIVE_SCAN_URL` and variable `ALLOW_LIVE_SCAN`: LIVE is scanned only when the secret is
  set and `ALLOW_LIVE_SCAN` is exactly `true`. Both are unset by default; set them only on the
  owner's go, and unset `ALLOW_LIVE_SCAN` again afterwards.

The URLs are secrets, never variables: no server detail is visible on GitHub. They are never
written in the repository, an issue or a pull request. Secrets cannot be read in a job-level `if`,
so each scan job checks its secret in a first guard step. Repository secrets are readable by a run
on any branch, and `workflow_dispatch` accepts any ref, so both scan jobs run only on `main`
(`github.ref == 'refs/heads/main'`): a manual run from another branch skips them. Only the guard and the scan step get the
secret, as `TARGET_URL`; it never appears in a job name, a step summary, a cache key, an artifact
or the report job. GitHub masks the secret in the log, and the scan script masks it again; since a
mask covers only the full URL, the ZAP and nuclei output goes to files on the runner and the log
shows exit codes and counts only. The reports stay on the runner as well, because the artifacts of
a public repository are public: to see the findings, rerun the scan locally against the target. A
scan fails on a ZAP alert of medium or high risk, a nuclei finding of medium severity or above, or
a scanner error.

The nuclei engine image is pinned, but its template set is not: the image downloads the current
public templates at run time (`-disable-update-check` covers only the engine). Findings can
therefore change between two weekly runs with no change on our side; check the template release
before treating a new finding as a regression.

How failures are reported: when a scheduled or manual run on `main` fails, the `report` job
(`tools/security_report.py`, the only job with `issues: write`) opens one issue labelled
`security` per workflow, titled `[security-nightly] ...` or `[security-stage] ...`, with the job
results, finding counts and failing test names (no URL, host or address), and posts one line on the
lane board, issue #86: `to lane-a: security-nightly failed: <run link>`. While that issue stays
open, later failures update its text and add a comment instead of a new line on #86. The next green
run comments and closes it. A skipped run (no target secret, or a run off `main`) never closes
it: if `STAGE_URL` is removed while a `[security-stage]` issue is open, the issue stays open until
a scan runs again or someone closes it by hand.

## No hand edits on servers

Units, the Caddyfile and `mpmv-deploy` on a server come from the release it runs. An emergency hand
fix on STAGE is allowed to restore service, but it must become a pull request the same day, and
the next release replaces it. Hand fixes on LIVE wait for a release. Env files and the CA
certificate are host configuration: deploys never touch them.
