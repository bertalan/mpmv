# Releases (ARC.23)

How a commit becomes a release and reaches STAGE and LIVE. The mechanics are in
[deploy/README.md](../../deploy/README.md); this page holds the rules.

## Tagging

- Tag only a commit on `main` whose CI run is green, and name it by its full SHA:
  `git tag -a v0.2.0 <full SHA> -m v0.2.0 && git push origin v0.2.0`. Never tag a branch name
  or `HEAD`: by the time the tag is pushed, either may point elsewhere.
- Release tags are immutable. A broken release gets a new patch version, never a re-tag.

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

## No hand edits on servers

Units, the Caddyfile and `mpmv-deploy` on a server come from the release it runs. An emergency hand
fix on STAGE is allowed to restore service, but it must become a pull request the same day, and
the next release replaces it. Hand fixes on LIVE wait for a release. Env files and the CA
certificate are host configuration: deploys never touch them.
