# Containers and deployment (ARC.5)

One OCI image, `mpmv-app`, runs every process type in every environment
([ADR-0002](../docs/adr/0002-local-containers-apple.md), [ADR-0005](../docs/adr/0005-hosting.md)).
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
- **Base images** are pinned by tag and digest. To update one, take the new digest from the
  registry, change tag and digest together, and run `make image` and the CI image job.

## Versions and tags

| Where | Tag | Made by |
|---|---|---|
| Developer machine | `mpmv-app:dev` (or `make image VERSION=...`) | `make image` |
| CI on every PR | `mpmv-app:ci` (not published) | job *Container image* in `ci.yml` |
| Release | `ghcr.io/<owner>/mpmv-app:X.Y.Z` and `:sha-<commit>` | `release.yml` on git tag `vX.Y.Z` |
| Server, live release | `localhost/mpmv-app:current` | `mpmv-deploy` |

- Releases follow `vMAJOR.MINOR.PATCH` git tags on `main`. A release tag is immutable: the release
  workflow refuses to overwrite an existing image tag. `latest` is never used.
- The version is baked into the image as `MPMV_VERSION` and the OCI label
  `org.opencontainers.image.version`.
- Release images are built on GitHub's `ubuntu-24.04` runners, so they are `linux/amd64`. An
  arm64 server would need a multi-platform build.

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

Each environment is an EU VPS running rootless Podman under a dedicated user, with managed
PostgreSQL 17 in the same region (ADR-0005). The units are in [`quadlet/`](quadlet/):

| Unit | Runs |
|---|---|
| `mpmv.network` | Network `mpmv-net`; Podman DNS resolves containers by name on it |
| `mpmv-app.container` | `web`, with a health check; not published, Caddy proxies to it |
| `mpmv-worker.container` | `worker` |
| `mpmv-migrate.container` | `migrate`, one-shot, started only by `mpmv-deploy` |
| `mpmv-caddy.container` | Caddy on 80/443 with certificates in volume `mpmv-caddy-data` |

### First-time host setup

As the deploy user, on a host with Podman 5 or later:

```sh
loginctl enable-linger "$USER"                     # user services start at boot (needs root once)
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80   # persist it in /etc/sysctl.d/
mkdir -p ~/.config/containers/systemd ~/.config/mpmv ~/.local/bin
cp deploy/quadlet/* ~/.config/containers/systemd/
cp deploy/caddy/Caddyfile ~/.config/mpmv/Caddyfile
cp deploy/server/mpmv.env.example ~/.config/mpmv/mpmv.env    # fill in, then chmod 600
cp deploy/server/caddy.env.example ~/.config/mpmv/caddy.env  # fill in
install -m 755 deploy/server/mpmv-deploy ~/.local/bin/mpmv-deploy
podman login ghcr.io                               # only if the package is private
systemctl --user daemon-reload
```

On STAGE set `DJANGO_SETTINGS_MODULE=mpmv.settings.stage`, on LIVE `mpmv.settings.live`.
STAGE holds synthetic data only (spec 12). Then run the first `mpmv-deploy deploy X.Y.Z` (below)
and `systemctl --user start mpmv-caddy.service`. Afterwards every unit with an `[Install]` section
starts at boot.

### Deploy and rollback

```sh
mpmv-deploy deploy 0.2.0   # pull ghcr.io/<owner>/mpmv-app:0.2.0, migrate, restart, health check
mpmv-deploy status         # current and previous release
mpmv-deploy rollback       # back to the previous release
```

`deploy` pulls the release, tags it `localhost/mpmv-app:current`, runs `mpmv-migrate` and then
restarts app and worker. If the migration fails, the tag goes back to the live release and nothing
restarts. If app or worker fail to restart on the new release, or it does not pass its health
check, `deploy` switches back to the previous release by itself. If `rollback` cannot bring the
previous release up, it puts the live one back. Releases are recorded in `~/.local/state/mpmv/releases` (a stack) and
`deploy.log`.

**Rollback does not revert migrations.** Migrations must be backward compatible with the previous
release (expand, then contract in a later release), so the previous image keeps working on the
migrated schema. Set `MPMV_IMAGE` to deploy from another registry path.
