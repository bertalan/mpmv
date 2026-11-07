# ADR-0002: Local containers on Apple `container`

- **Status:** Accepted
- **Date:** 2026-11-07
- **Deciders:** project owner
- **Backlog:** ARC.3, ARC.5 · **Related:** D-12 (hosting model)

## Context

Development happens on macOS 26 machines. Apple's `container` CLI (Containerization framework,
v1.0) runs Linux OCI containers natively, each in its own lightweight VM. The project owner wants
it used instead of Docker Desktop or OrbStack.

## Options considered

1. **Apple `container`**: native, no third-party daemon, OCI compatible. Version 1.0 has no
   compose equivalent.
2. **Docker / OrbStack with Compose**: familiar, and a compose file is declarative. It adds another
   tool to maintain and is not the owner's preference.

## Decision

- Images are built from OCI `Containerfile`s with `container build`. The same images run on the
  Linux STAGE/LIVE hosts.
- The local stack (network, volumes, Postgres, app, worker) is orchestrated by Makefile targets and
  a small script that call `container network|volume|run`. No compose file for local development.
- Production orchestration (Compose, Podman Quadlets or a PaaS) is decided in ARC.5 together with
  D-12. It must consume the same images.

## Consequences

- No container-specific code in the application. Configuration comes only from environment
  variables (12-factor).
- The orchestration script takes the place of compose. Keep it small and idempotent
  (`make dev`, `make dev-down`, `make dev-reset`).
- CI on Linux builds the same `Containerfile` with a standard OCI builder.
