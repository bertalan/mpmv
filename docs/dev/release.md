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

### Platform staff permission (CORE.8)

Platform staff is the model permission `vendors.view_all_vendors`, not `is_staff`. No data
migration grants it: on every environment, existing staff accounts must get it explicitly (admin
user page, or the CORE.18 role groups once they exist), otherwise they lose the all-vendor view
after the deploy (fail closed). Superusers keep it. STAGE has only seed users: the next seed run
grants the permission to the seed operator.

### First platform owner (CORE.23)

The platform owner is the `core.platform_owner` permission; nobody holds it after the first
deploy, so nobody can open `/manager/platform/`. Until the ARC.25 first-start wizard exists, give
it to the first person with the management command, once per environment, inside the app
container:

```sh
manage.py bootstrap_platform_owner owner@example.org --dry-run   # shows what would change
manage.py bootstrap_platform_owner owner@example.org             # grants it, audited
manage.py bootstrap_platform_owner owner@example.org --create    # also creates the account
```

The command is idempotent (a second run says "already a platform owner" and changes nothing).
It refuses an unknown user without `--create`, an inactive user (also with `--create`: it never
reactivates an account) and a vendor member (platform roles and vendor membership never mix, so
the user would be locked out of both spaces). It does not refuse when another owner already
exists: several platform owners are allowed. A created account has no usable password and the
command never prints one. Until the CORE.19 password-set link exists, a superuser sets the
password in the staff admin (user page, "set password"); the owner then signs in and the Manager
gate forces the enrolment of a second factor (CORE.4) before any page opens. Check
the AuditLog entry `core.platform_owner.grant`; its target is the lower-cased email.

### Editor roles migration (CORE.8)

Migration `cms/0013_editor_roles` removes Wagtail's default "Editors" and "Moderators" groups
and moves their members into three edit-only roles (Page editors, Blog editors, Legal page
editors). Effects on STAGE and later environments:

- Former Editors and Moderators can edit and submit their work but no longer publish or approve.
- The three publisher roles and Event moderators are created empty. After the migrate, staff
  assign them in the staff admin (superuser only) to whoever must publish.
- The home page is in no role; only superusers edit it.
- A database without the default groups (STAGE, a fresh install) gets the roles and no members.
- Reverse restores the default groups for users who held all three editor roles only.
- The Wagtail admin menu shows CMS entries only, superusers included. Commerce work is in the
  Manager.

Details: [editor-roles.md](editor-roles.md).

### Product slugs without the language suffix (CAT.5, D-25)

Migration `catalog/0010_d25_product_slugs` reslugs every product translation to `{title}` and keeps
the old `{title}-{lang}` and `{title}-{lang}-{sku}` slugs in SlugHistory (one 301 each). Before
deploying a release that contains it:

1. Run `manage.py rebuild_product_slugs --dry-run` on the target environment and check the count
   and the listed renames. The dry run checks each translation against the stored slugs only, so two
   translations of the same run that would need a `-2` are not seen: its count may differ from the
   migration's, which reslugs one translation after the other.
2. Deploy; `migrate` applies the same reslug and writes the audit entries.
3. Rerunning changes nothing. Reverse is a no-op: the old slugs stay in SlugHistory.

### v0.1.4 on STAGE (EVT.5, I18N.11, CAT.5, CORE.17)

Changes since v0.1.3: product slugs without the language suffix (above), the language switch,
the vendor events API and the Manager fixes of #212 (which came after the v0.1.3 candidate).

- No new environment variable or setting, no one-off command beyond the slug dry run above.
  The only migration is `catalog/0010_d25_product_slugs`.
- The vendor events API needs no setup: `events:read` and `events:write` are in the vendor-grantable
  scope set (`apps/api/engine.py`), so a vendor key can hold them. Existing keys gain nothing.
- Owner checks on STAGE: (1) in the Manager the language switcher posts with a valid CSRF token,
  `/manager/` redirects, and a user without access sees the explanatory 403; (2) on a product, a
  category, a vendor storefront, an event and a CMS page, switching language lands on the same
  object, and an untranslated object lands on the target home with the notice once; on a
  confirmation or action-link page the switch goes home and the secret URL is never copied;
  (3) product URLs have no `-it`, `-de` suffix, and an old `{title}-{lang}` or SKU URL answers a
  single 301; (4) with a vendor key, `POST /api/v1/events?dry_run=true` returns the diff and
  writes nothing, an invalid `rrule` returns a 422 problem naming the field, and another
  vendor's event is a 404.

### Activation gate and tax territories (CMP.25, TAX.7)

- Migration `vendors/0023` seeds the `activation` requirements (DSA article 30 trader data) and
  makes self-certifications append-only. A vendor is activated only with that data and the
  self-certification; existing active vendors keep their status.
- Migration `tax/0006` seeds the special territories (Livigno, Canary Islands, French overseas
  departments and others) without overwriting rows staff changed. The postcode lists are for
  the owner to review in the tax admin; Northern Ireland is not seeded (D-02).

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

The nuclei templates are pinned like the images: `deploy/ci/stage-scan.sh` clones the
nuclei-templates repository at `NUCLEI_TEMPLATES_TAG` and fails as a scanner error unless the
checkout equals `NUCLEI_TEMPLATES_SHA`. Bump the tag and the SHA together, monthly, in a PR. The
scan exits 0 when clean, 1 on findings only, 3 on a scanner error (an unreadable ZAP report, a
failed template clone, zero templates, a nonzero nuclei exit).

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
