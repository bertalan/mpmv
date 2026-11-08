# Dependency updates, security alerts and SBOM

Dependencies are updated by pull requests that go through CI like any other change (backlog
OPS.4, spec 12 §12.5). The configuration is in [`.github/dependabot.yml`](../../.github/dependabot.yml);
a unit test (`tests/unit/test_dependency_updates.py`) checks it against the published Dependabot
schema and against the rules below.

## What gets updated

| Ecosystem | Files | Update PRs |
|---|---|---|
| `uv` | `pyproject.toml`, `uv.lock` | Runtime and dev dependencies |
| `github-actions` | `.github/workflows/*.yml` | Actions referenced with `uses:` |
| `docker` | `Containerfile` | Base images (`uv`, `python`); tag and digest bumped together |

Rules:

- **Weekly**: every Monday at 05:00 UTC.
- **Minor and patch updates are grouped**: one PR per ecosystem (`python-minor-patch`,
  `actions-minor-patch`, `images-minor-patch`).
- **Major updates are not grouped**: one PR per dependency, so each can be read, tested and
  reverted on its own.
- **Django and Wagtail always get their own PR**, patches included: their releases carry
  deprecations and migrations.
- **The `uv` image always gets its own PR**: uv is still at 0.x, where a minor release can
  break things, so it stays out of `images-minor-patch`.
- **The Python base image stays on the runtime minor** (3.13): moving to a new minor also
  changes `requires-python`, `.python-version` and CI, so it is a planned task, not a bot PR.
- **Cooldown**: a release is proposed only after 5 days (14 for Python majors; Dependabot has no
  per-semver cooldown for Actions and images), which gives the ecosystem time to spot a broken or
  hijacked release. Security updates skip the cooldown.
- **Labels** `dependencies` plus `python`, `github-actions` or `docker`; commit messages start
  with `OPS.4:`.

Not covered, still bumped by hand: the Caddy image in `deploy/quadlet/mpmv-caddy.container`
(pinned by tag and digest, see [deploy/README.md](../../deploy/README.md)), the `postgres:17`
service image in CI and `make db`, the pre-commit hook revisions (`uv run pre-commit autoupdate`)
and the Python minor version.

### Why Dependabot and not Renovate

Dependabot is built into GitHub: no app to install, no token to manage and no extra service with
write access to the repository. It supports all three ecosystems we use, groups and cooldowns,
and it is the same system that raises the security alerts. Renovate is more configurable (for
example, it can also bump the Quadlet images), but that does not outweigh running a third-party
app with write access. If we need it later, its config is validated by the same tool
(`check-jsonschema --builtin-schema vendor.renovate`).

## Reviewing an update PR

Update PRs run the full CI: static checks (including `pip-audit`), unit and integration tests,
the container image build with its health check, E2E and the secret scan. Dependabot PRs get a
read-only token and no repository secrets; CI needs none. Never merge a red update PR, and never
skip a check to get one through.

1. **Read the PR body.** Dependabot links release notes and changelogs. For a group, look at each
   entry, not just the title.
2. **Check the lockfile diff.** New packages in `uv.lock` deserve a look, since a new transitive
   dependency is new code in production:
   `git diff origin/main -- uv.lock | grep '^+name'`. `uv tree --invert --package <name>` shows
   what pulls a package in.
3. **Runtime or dev?** A runtime package ends up in the image and in the Python SBOM; dev tools
   (pytest, ruff, mypy, stubs) only affect CI. Look harder at runtime updates.
4. **Major updates**: read the upgrade guide and list the breaking changes in a PR comment. If the
   code needs changes, push them to the Dependabot branch (Dependabot then stops rebasing it) or
   close the PR and add a task to the backlog. For Django and Wagtail also run
   `uv run python -Wd manage.py check --settings=mpmv.settings.test` and the tests with `-Wd` to see
   new deprecation warnings.
5. **Sensitive packages** need a careful human review, whatever the version jump: `cryptography`,
   `django-otp`, `webauthn`, `psycopg`, `django`, `gunicorn`, and later the payment provider
   SDK. The PR must say so in its review notes.
6. **Base images**: CI builds the image, runs it as non-root, migrates and passes the health
   check. Locally, `make image` and `make dev` check the same on Apple `container`.
7. **GitHub Actions majors**: check changed inputs and the permissions the action asks for.

Useful comments on a Dependabot PR: `@dependabot rebase`, `@dependabot recreate`,
`@dependabot ignore this major version`, `@dependabot ignore this dependency` (record why in the
PR). Merging follows the normal flow: green CI, then merge into `main`.

## Security alerts

Three things report known vulnerabilities:

- **Dependabot alerts** (repository *Security* tab) read the dependency graph and open
  **security update PRs** right away, outside the weekly schedule and without the cooldown.
  These are not grouped. Review them first.
- **`pip-audit`** runs in every CI build (`make audit`) and fails on any known vulnerability in
  the pinned dependencies. A new advisory can turn `main` red without a code change: fix it with
  the Dependabot security PR or `uv lock --upgrade-package <name>`. If no fixed version exists
  yet, an ignore (`--ignore-vuln <ID>` in the `audit` target) needs a task, a justification and a
  date to look again, all in the PR.
- **The SBOMs** below can be scanned at any time, for example
  `grype sbom:build/sbom/mpmv-image-arm64.cdx.json` for the image's OS packages.

Repository settings (owner, *Settings → Advanced Security*, formerly *Code security*): dependency graph, Dependabot alerts
and Dependabot security updates must be on. Version updates need only this config file.

## SBOM

Every CI run and every release produce [CycloneDX](https://cyclonedx.org/) 1.6 JSON SBOMs: one of
the Python environment and one of the image for each architecture (ARC.13):

| SBOM | Produced by | Content |
|---|---|---|
| `mpmv-python.cdx.json` | `make sbom` (`cyclonedx-py`) | The production Python environment from `uv.lock`, no dev group, with licenses |
| `mpmv-image-amd64.cdx.json`, `mpmv-image-arm64.cdx.json` | syft (`anchore/sbom-action`) | The built image for that architecture: Debian packages, Python and every package in the virtualenv |

Where to find them:

- **CI**: the `sbom-python`, `sbom-image-amd64` and `sbom-image-arm64` artifacts of each run
  (*Actions → run → Artifacts*).
- **Releases**: attached to the GitHub release of each `vX.Y.Z` tag by `release.yml`, next to
  the image `ghcr.io/<owner>/mpmv-app:X.Y.Z` they describe.
- **Locally**: `make sbom` writes `build/sbom/mpmv-python.cdx.json`; with syft installed,
  `syft mpmv-app:dev -o cyclonedx-json` describes a local image.
