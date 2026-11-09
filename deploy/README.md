# Containers and deployment (ARC.5)

One OCI image, `mpmv-app`, runs every process type in every environment
([ADR-0002](../docs/adr/0002-local-containers-apple.md), [ADR-0007](../docs/adr/0007-hosting-hetzner-self-managed-postgres.md), which supersedes ADR-0005).
Configuration comes only from environment variables.

| Path | What |
|---|---|
| [`../Containerfile`](../Containerfile) | Multi-stage build: uv from `uv.lock`, Python 3.13, `collectstatic` at build, non-root user `10001` |
| [`../.dockerignore`](../.dockerignore) | Build context exclusions (secrets, `private/`, local state) |
| [`container/entrypoint.sh`](container/entrypoint.sh) | Process types: `web`, `worker`, `migrate`, `manage <command>` |
| [`local/stack.sh`](local/stack.sh) | Local stack on Apple `container`, driven by `make dev` |
| [`quadlet/`](quadlet/) | Podman Quadlet units for STAGE and LIVE |
| [`caddy/Caddyfile`](caddy/Caddyfile) | TLS reverse proxy in front of the app |
| [`server/mpmv-deploy`](server/mpmv-deploy) | Deploy and rollback by image tag on a server |
| [`server/*.env.example`](server/) | Variables the server needs, without values |
| [`ci/parity-smoke.sh`](ci/parity-smoke.sh) | CI and release gate: runs the image as the server does (ARC.23) |

Release rules (tagging, STAGE, LIVE, no hand edits): [docs/dev/release.md](../docs/dev/release.md).

## The image

- **Process types.** The entry point takes the role as its first argument:
  `web` (gunicorn on `MPMV_HTTP_PORT`, default 8000), `worker`, `migrate`, `manage <command>`.
- **Worker.** `worker` runs `manage.py procrastinate worker` (ARC.9, ADR-0004): queued jobs and
  periodic schedules.
- **Static files** are collected at build time and served by WhiteNoise with hashed, compressed
  names (`mpmv/settings/live.py`). Media will live on object storage (spec 01 §1.2).
- **Health.** `python -m mpmv.healthcheck` calls `/healthz` over plain HTTP on the loopback
  interface; Podman runs it as `HealthCmd`, the local stack and CI use it to wait for the app. LIVE
  exempts `/healthz` from the HTTPS redirect, so the probe never forges `X-Forwarded-Proto`.
- **Hardening.** Code is owned by root and read-only for the app user; the containers run with a
  read-only root filesystem and `NoNewPrivileges`. No secret is baked in: the build generates a
  throwaway key only to run `collectstatic`.
- **Deploy units inside (ARC.23).** `/app/deploy` holds the release's `quadlet/` units,
  `caddy/Caddyfile`, `server/mpmv-deploy` and the env examples (no env file, no certificate).
  `mpmv-deploy` installs them from the tag it deploys, so a server runs the units of its release.
- **Base images** are pinned by tag and digest. To update one, take the new digest from the
  registry, change tag and digest together, and run `make image` and the CI image job.

## Versions and tags

| Where | Tag | Made by |
|---|---|---|
| Developer machine | `mpmv-app:dev` (or `make image VERSION=...`) | `make image` |
| CI on every PR | `mpmv-app:ci` for amd64 and arm64 (not published) | jobs *Container image (amd64)* and *(arm64)*, gate *Container image*, in `ci.yml` |
| Release | `ghcr.io/<owner>/mpmv-app:X.Y.Z` and `:sha-<commit>`, multi-arch | `release.yml` on git tag `vX.Y.Z` |
| Server, live release | `localhost/mpmv-app:current` | `mpmv-deploy` |

- Releases follow `vMAJOR.MINOR.PATCH` git tags on `main`. A release tag is immutable: the release
  workflow refuses to overwrite an existing image tag. `latest` is never used.
- The version is baked into the image as `MPMV_VERSION` and the OCI label
  `org.opencontainers.image.version`.
- Release images are multi-arch (ARC.13, ADR-0007 amendment 2026-11-08): one manifest list
  with `linux/amd64` and `linux/arm64`. Each architecture is built on a native GitHub runner
  (`ubuntu-24.04` and `ubuntu-24.04-arm`, no QEMU emulation) and pushed by digest; only the
  manifest list gets the release tags, after both builds pass. `podman pull` on the server picks
  the server's architecture. Each pull request builds and checks both architectures too.

## Local stack (Apple `container`)

```sh
make dev         # make db + make image, then migrate, app on http://127.0.0.1:8000, worker
make dev-status  # list the mpmv-* containers
make dev-logs    # follow the app logs (WHAT=worker for the worker)
make dev-down    # remove the app and worker containers; the database keeps running
make dev-reset   # dev-down, then delete the mpmv-net network and the mpmv-media volume
```

- The stack uses the database container of `make db` (`mpmv-db`, volume `mpmv-pgdata`), so tests
  and the stack share one PostgreSQL. Neither `dev-down` nor `dev-reset` touches it; `make db-reset`
  does.
- App and worker join the private network `mpmv-net` and the `default` network, where `mpmv-db`
  lives. `container` 1.0 has no DNS between containers, so the script passes the database address
  it reads from `container inspect`.
- The app runs with `mpmv.settings.live` (as on STAGE) and `DJANGO_SECURE_SSL_REDIRECT=false`.
  The secret key is generated once into `build/dev-stack.env` (git-ignored).
- The script only creates, stops or deletes resources named `mpmv-*`, always with
  `container delete`. A unit test (`tests/unit/test_local_stack.py`) enforces this.

## STAGE and LIVE (Podman Quadlets)

Each environment runs rootless Podman under a dedicated user on a Hetzner Cloud VPS (EU). LIVE
reaches PostgreSQL on a separate database VPS over the private network; STAGE runs PostgreSQL on
the same VPS, listening on loopback only
([ADR-0007](../docs/adr/0007-hosting-hetzner-self-managed-postgres.md) and its 2026-11-08
amendment). Either way the app connects with TLS `sslmode=verify-full` against an internal CA
(owner decision MQ-11). The units are in [`quadlet/`](quadlet/):

| Unit | Runs |
|---|---|
| `mpmv.network` | Network `mpmv-net`; Podman DNS resolves containers by name on it |
| `mpmv-app.container` | `web`, with a health check; not published, Caddy proxies to it |
| `mpmv-worker.container` | `worker` |
| `mpmv-migrate.container` | `migrate`, one-shot, started only by `mpmv-deploy` |
| `mpmv-caddy.container` | Caddy on 80/443 with certificates in volume `mpmv-caddy-data` |
| `mpmv-media.volume` | Volume `mpmv-media`: uploads and renditions (`MEDIA_ROOT`). App and worker write it, Caddy serves only image files in Wagtail's image folders from it, read-only, as inert files (no listing, `nosniff`, sandbox CSP); any other path under `/media/` is a 404. Back it up with the host until object storage replaces it |

App, worker and migrate read two env files from `~/.config/mpmv/`, in this order, and mount one
certificate read-only:

| File | Holds |
|---|---|
| `mpmv.env` | Settings and secrets of the environment ([`server/mpmv.env.example`](server/mpmv.env.example)) |
| `database.env` | `DATABASE_URL` only ([`server/database.env.example`](server/database.env.example)), usually written by the database setup. Read last, so it wins over `mpmv.env` |
| `pg-root-ca.crt` | Root certificate of the internal CA, mounted at `/etc/mpmv/pg-root-ca.crt`; `DATABASE_URL` points `sslrootcert` at it |

Caddy reads `caddy.env` ([`server/caddy.env.example`](server/caddy.env.example)):
`MPMV_SITE_ADDRESS` is the public host name and `MPMV_NOINDEX=true` adds
`X-Robots-Tag: noindex, nofollow` to every response (set on STAGE, empty on LIVE). Caddy gets
the certificate from Let's Encrypt by itself (the name must resolve to the host and ports 80 and
443 must be open) and serves the site only for that name: plain HTTP to any other name or to the
IP address is closed without an answer, HTTPS to them fails the TLS handshake, and a `Host`
header that differs from the TLS server name gets 421.

### First-time host setup

As the deploy user, on a host with the recommended Podman (5 or later, with its quadlet generator):

```sh
loginctl enable-linger "$USER"                     # user services start at boot (needs root once)
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80   # persist it in /etc/sysctl.d/
mkdir -p ~/.config/containers/systemd ~/.config/mpmv ~/.local/bin
cp deploy/server/mpmv.env.example ~/.config/mpmv/mpmv.env    # fill in, then chmod 600
cp deploy/server/database.env.example ~/.config/mpmv/database.env  # unless the DB setup wrote it; chmod 600
cp <internal CA root certificate> ~/.config/mpmv/pg-root-ca.crt
cp deploy/server/caddy.env.example ~/.config/mpmv/caddy.env  # fill in
podman login ghcr.io                               # only if the package is private
# mpmv-deploy itself, from the release image; later deploys keep it in step with the release.
podman pull ghcr.io/<owner>/mpmv-app:X.Y.Z
podman run --rm --network none --entrypoint cat ghcr.io/<owner>/mpmv-app:X.Y.Z \
  /app/deploy/server/mpmv-deploy > ~/.local/bin/mpmv-deploy && chmod 755 ~/.local/bin/mpmv-deploy
```

The units and the Caddyfile are not copied by hand: `deploy` installs those of the release.
`~/.local/bin` is not on `PATH` in a non-interactive ssh session, so the script is always called
by its absolute path, `~/.local/bin/mpmv-deploy`.

On STAGE set `DJANGO_SETTINGS_MODULE=mpmv.settings.stage`, on LIVE `mpmv.settings.live`.
Keep `MPMV_TRUSTED_PROXY_HEADER` and `MPMV_TRUSTED_PROXIES` as in the example (CORE.5): the app
sees every request coming from Caddy, so it takes the client IP from the `X-Real-IP` header that
Caddy overwrites, and only from the `mpmv-net` subnet fixed in `mpmv.network`. Without them the
per-IP login lockout and registration limit act on the whole site; when they are missing,
`podman exec mpmv-app python manage.py check --deploy` warns (`core.W001`). A network created
before the subnet was fixed must be removed (`podman network rm mpmv-net`) and recreated by its
unit.
STAGE holds synthetic data only (spec 12). Then run the first deploy (below); it starts Caddy
too. Afterwards every unit with an `[Install]` section starts at boot.

### Deploy and rollback

```sh
~/.local/bin/mpmv-deploy deploy 0.2.0   # doctor, pull, units of 0.2.0, migrate, restart, check
~/.local/bin/mpmv-deploy status         # current and previous release
~/.local/bin/mpmv-deploy rollback       # back to the previous release and its units
~/.local/bin/mpmv-deploy doctor         # can this host run the live release?
~/.local/bin/mpmv-deploy check          # the post-deploy check, on demand
```

`deploy` first runs the doctor: Podman and its quadlet generator present with every subcommand the
script uses, every variable name of the release's `*.env.example` present in the env files (names
only, values are never printed), `pg-root-ca.crt` readable, the `mpmv-media` volume owned by the
image user (uid 10001), free disk above `MPMV_MIN_FREE_MB` (2048), and the installed and the new
units converting with `quadlet -dryrun`. Any failure stops the deploy before anything changes.

Then it pulls the release (an image already in local storage is reused: release tags are
immutable), copies the release's units, Caddyfile and script out of the image into
`~/.local/state/mpmv/units/<version>/`, installs them (`~/.config/containers/systemd/`,
`~/.config/mpmv/Caddyfile`, `~/.local/bin/mpmv-deploy`; stale `mpmv*` units are removed),
reloads systemd, tags the image `localhost/mpmv-app:current`, runs `mpmv-migrate`, and restarts
app and worker, plus Caddy when its Caddyfile or unit changed. If the migration fails, the live
units and tag come back and nothing restarts.

The check then polls, for at most `MPMV_CHECK_BUDGET` seconds (40), the app's health check and,
through Caddy on this host (`curl --resolve <site>:443:127.0.0.1`, the site name read from
`caddy.env`), `/healthz`, the home page and one `.webp` file of the media volume. If the app
container restarts on its own, the release fails at once. A failed check, or a unit that fails to
restart, rolls back by itself to the previous release: its units, its image, restarted and checked.
If `rollback` cannot bring the previous release up, it puts the live one back. Releases are
recorded in `~/.local/state/mpmv/releases` (a stack) and `deploy.log`; the unit sets of the last
five releases are kept. A release deployed before ARC.23 has no units in its image: the first
deploy saves the installed units as its set, so rolling back to it restores them.

Env files and `pg-root-ca.crt` are host configuration: deploys and rollbacks never touch them.

**Rollback does not revert migrations.** Migrations must be backward compatible with the previous
release (expand, then contract in a later release), so the previous image keeps working on the
migrated schema. Set `MPMV_IMAGE` to deploy from another registry path.
